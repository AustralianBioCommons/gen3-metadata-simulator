"""Typer CLI for gen3-metadata-simulator."""

from __future__ import annotations

import enum
import json
import logging
import random
from pathlib import Path
from typing import Optional

import typer

from gen3_metadata_simulator.errors import Gen3SimulatorError
from gen3_metadata_simulator.generator import MetadataGenerator
from gen3_metadata_simulator.providers.base import ValueProvider, ValueRequest
from gen3_metadata_simulator.providers.random_provider import RandomValueProvider
from gen3_metadata_simulator.schema import SchemaLoader
from gen3_metadata_simulator.validation import (
    flatten_records,
    self_validate,
    summarize_failures,
)
from gen3_metadata_simulator.writers import write_outputs

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

app = typer.Typer(
    add_completion=False,
    help="Simulate linked Gen3 metadata JSON files from a bundled Gen3 schema.",
)


def configure_logging(verbose: bool = False, debug: bool = False) -> None:
    """Set up package logging from the CLI verbosity flags.

    Quiet by default (only warnings/errors). ``--verbose`` surfaces the
    milestone INFO logs (schema loaded, warmup summary, validation result);
    ``--debug`` adds per-item DEBUG detail and turns on the Anthropic SDK's own
    logging. The final result/error lines are printed regardless of level.
    """
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    # Keep the root (and thus chatty dependencies like gen3_validator) at WARNING;
    # only raise verbosity for our own package logger.
    logging.basicConfig(level=logging.WARNING, format=_LOG_FORMAT, force=True)
    logging.getLogger("gen3_metadata_simulator").setLevel(level)
    if debug:
        logging.getLogger("anthropic").setLevel(logging.DEBUG)


class Provider(str, enum.Enum):
    random = "random"
    llm = "llm"


def _build_provider(
    provider: Provider,
    rng: random.Random,
    array_size: int,
    llm_model: Optional[str],
    cache_path: str,
    text_pool_size: int,
    refresh_llm: bool,
) -> ValueProvider:
    if provider is Provider.random:
        return RandomValueProvider(rng, array_size=array_size)

    # llm: realistic values from a lightweight model. Requires an explicit model
    # and an API key resolved from the file referenced by LLM_API_KEY_FILE.
    if not llm_model:
        raise typer.BadParameter("--llm-model is required when --provider llm")
    from gen3_metadata_simulator.config import load_api_key
    from gen3_metadata_simulator.providers.llm_provider import LLMValueProvider
    from gen3_metadata_simulator.providers.specs import AnthropicSpecSource

    api_key = load_api_key()
    source = AnthropicSpecSource(api_key=api_key, model=llm_model)
    return LLMValueProvider(
        rng, source, cache_path=cache_path, array_size=array_size,
        text_pool_size=text_pool_size, force_refresh=refresh_llm,
    )


@app.command()
def generate(
    schema: Path = typer.Option(..., "--schema", "-s", exists=True, readable=True,
                                help="Path to the bundled Gen3 JSON schema."),
    output_dir: Path = typer.Option(Path("./output"), "--output-dir", "-o",
                                    help="Directory to write metadata files into."),
    num_records: int = typer.Option(30, "--num-records", "-n", min=1,
                                    help="Records to generate per node."),
    project_code: str = typer.Option("simulated_project", "--project-code", "-p",
                                     help="Project code (used as the project link target)."),
    seed: Optional[int] = typer.Option(None, "--seed", help="RNG seed for reproducible output."),
    provider: Provider = typer.Option(Provider.random, "--provider",
                                      help="Value provider strategy."),
    array_size: int = typer.Option(0, "--array-size", min=0,
                                   help="Elements to emit for array properties (0 => [])."),
    llm_model: Optional[str] = typer.Option(None, "--llm-model",
                                            help="Model for --provider llm (required, e.g. claude-haiku-4-5)."),
    cache_path: Path = typer.Option(Path(".cache/distributions.json"), "--cache-path",
                                    help="Where the LLM provider caches field specs."),
    refresh_llm: bool = typer.Option(False, "--refresh-llm",
                                     help="Force fresh LLM estimates, ignoring the cache."),
    skip_validation: bool = typer.Option(False, "--skip-validation",
                                         help="Write output without self-validating."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log progress (INFO)."),
    debug: bool = typer.Option(False, "--debug", help="Log detail + tracebacks (DEBUG)."),
):
    """Generate simulated metadata that conforms to and links per the schema."""
    configure_logging(verbose, debug)
    loader = SchemaLoader(str(schema))
    try:
        loader.load()
        loader.validate_is_gen3_schema()
    except Gen3SimulatorError as exc:
        logger.debug("Schema load failed", exc_info=True)
        typer.secho(f"Invalid schema: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    rng = random.Random(seed)
    try:
        value_provider = _build_provider(
            provider, rng, array_size, llm_model, str(cache_path),
            text_pool_size=min(num_records, 15), refresh_llm=refresh_llm,
        )
    except Gen3SimulatorError as exc:
        logger.debug("Provider setup failed", exc_info=True)
        typer.secho(f"LLM configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    generator = MetadataGenerator(
        loader=loader,
        value_provider=value_provider,
        num_records=num_records,
        project_code=project_code,
        seed=seed,
    )
    try:
        data = generator.generate()
    except Gen3SimulatorError as exc:
        logger.debug("Generation failed", exc_info=True)
        typer.secho(f"Generation failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if not skip_validation:
        failures = self_validate(data, loader.resolved)
        typer.echo(summarize_failures(failures))
        if failures:
            typer.secho("Refusing to write invalid metadata.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

    out = write_outputs(data, generator.order, output_dir)
    node_count = len(generator.order)
    typer.secho(
        f"Wrote {node_count} node files + DataImportOrder.txt to {out}",
        fg=typer.colors.GREEN,
    )


@app.command()
def validate(
    schema: Path = typer.Option(..., "--schema", "-s", exists=True, readable=True,
                                help="Path to the bundled Gen3 JSON schema."),
    metadata_dir: Path = typer.Option(..., "--metadata-dir", "-m", exists=True,
                                      file_okay=False,
                                      help="Directory of metadata JSON files to validate."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log progress (INFO)."),
    debug: bool = typer.Option(False, "--debug", help="Log detail + tracebacks (DEBUG)."),
):
    """Validate an existing directory of metadata files against a schema."""
    configure_logging(verbose, debug)
    loader = SchemaLoader(str(schema))
    try:
        loader.load()
        loader.validate_is_gen3_schema()
    except Gen3SimulatorError as exc:
        logger.debug("Schema load failed", exc_info=True)
        typer.secho(f"Invalid schema: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    data: dict = {}
    for path in sorted(metadata_dir.glob("*.json")):
        node = path.stem
        data[node] = json.loads(path.read_text())

    failures = self_validate(data, loader.resolved)
    typer.echo(summarize_failures(failures))
    record_count = len(flatten_records(data))
    if failures:
        raise typer.Exit(code=1)
    typer.secho(f"All {record_count} records valid.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()

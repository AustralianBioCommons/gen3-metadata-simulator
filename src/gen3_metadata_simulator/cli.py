"""Typer CLI for gen3-metadata-simulator."""

from __future__ import annotations

import enum
import json
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

app = typer.Typer(
    add_completion=False,
    help="Simulate linked Gen3 metadata JSON files from a bundled Gen3 schema.",
)


class Provider(str, enum.Enum):
    random = "random"
    llm = "llm"


def _build_provider(provider: Provider, rng: random.Random, array_size: int) -> ValueProvider:
    if provider is Provider.random:
        return RandomValueProvider(rng, array_size=array_size)
    # llm: deferred to v2; constructing it is fine, calling value() raises.
    from gen3_metadata_simulator.providers.llm_provider import LLMValueProvider

    return LLMValueProvider(rng, array_size=array_size)


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
    skip_validation: bool = typer.Option(False, "--skip-validation",
                                         help="Write output without self-validating."),
):
    """Generate simulated metadata that conforms to and links per the schema."""
    loader = SchemaLoader(str(schema))
    try:
        loader.load()
        loader.validate_is_gen3_schema()
    except Gen3SimulatorError as exc:
        typer.secho(f"Invalid schema: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    rng = random.Random(seed)
    value_provider = _build_provider(provider, rng, array_size)

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
):
    """Validate an existing directory of metadata files against a schema."""
    loader = SchemaLoader(str(schema))
    try:
        loader.load()
        loader.validate_is_gen3_schema()
    except Gen3SimulatorError as exc:
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

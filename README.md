# gen3-metadata-simulator

Generate realistic, **linked**, schema-valid Gen3 metadata from a bundled Gen3
JSON schema. Point it at a Gen3 data dictionary and it produces one JSON file
per node (plus a `DataImportOrder.txt`), with every foreign key resolving to a
real parent record — then self-validates the result with
[`gen3-validator`](https://pypi.org/project/gen3-validator/).

## Why

Standing up or testing a Gen3 commons needs example data that conforms to your
dictionary and links together correctly. Hand-authoring it is tedious and
error-prone. This tool reads the dictionary, works out the node dependency
order, and fills every node with simulated records that pass validation.

## Install

Requires Python ≥ 3.12.10 (a constraint inherited from `gen3schemadev`).

```bash
poetry install
```

## Quickstart

```bash
poetry run gen3-metadata-simulator generate \
    --schema examples/jsonschema/acdc_schema_v1.1.5.json \
    --output-dir ./output \
    --num-records 30 \
    --project-code AusDiab_Simulated \
    --seed 1
```

This writes `./output/<node>.json` for every node, plus `DataImportOrder.txt`,
and prints `0 validation errors` on success. Re-running with the same `--seed`
reproduces byte-identical output. If validation fails, nothing is written.

### Options for `generate`

| Flag | Default | Description |
|------|---------|-------------|
| `--schema`, `-s` | *(required)* | Path to the bundled Gen3 JSON schema. |
| `--output-dir`, `-o` | `./output` | Where to write the metadata files. |
| `--num-records`, `-n` | `30` | Records per node. |
| `--project-code`, `-p` | `simulated_project` | Project `code` children link to. |
| `--seed` | *(none)* | RNG seed for reproducible output. |
| `--array-size` | `0` | Elements per array property (`0` → `[]`). |
| `--skip-validation` | off | Write without self-validating first. |

Run `poetry run gen3-metadata-simulator generate --help` for the full list, or
see [`docs/usage.md`](docs/usage.md).

### Validate an existing dataset

```bash
poetry run gen3-metadata-simulator validate \
    --schema examples/jsonschema/acdc_schema_v1.1.5.json \
    --metadata-dir ./output
```

## What the output looks like

- `project.json` — a single JSON object identified by `code`.
- `<node>.json` — a JSON array of N records, each with `type`, a unique
  `submitter_id`, foreign-key objects (`{"submitter_id": ...}`, or `{"code": ...}`
  for links to the project), and schema-conforming property values.
- `DataImportOrder.txt` — node names in dependency order, one per line, ready to
  drive a sequential Gen3 submission.

## How it works

1. **Resolve** the schema (`gen3-validator` inlines every `$ref`).
2. **Order** nodes topologically so parents are generated before children.
3. **Generate** records per node, wiring links to real parents.
4. **Validate** the whole set with `gen3_validator.validate_list_dict` and refuse
   to write anything that fails.

See [`docs/dev-notes.md`](docs/dev-notes.md) for a full walkthrough of how it
works and [`docs/usage.md`](docs/usage.md) for every flag.

## Realistic values with an LLM (`--provider llm`)

By default (`--provider random`) values are random within schema bounds. The
**LLM provider** instead asks a lightweight model for the *semantic* properties
of each field and samples from them, so output looks believable while still
validating:

- **numeric** — a distribution (mean ± stddev) and realistic limits, so
  `month_birth` stays in `[1, 12]` and `bmi_baseline` lands near 27 ± 5;
- **dates** — a real calendar date in a plausible window (no `3170-94-14`),
  rendered to the schema's pattern;
- **free text** — domain-appropriate strings (an assay `description` reads like
  a real one) drawn from an LLM-supplied pool.

Enums, booleans, and pattern-constrained strings (UBERON / ORCID / md5sum) keep
the random/regex behavior. Specs are cached to `.cache/distributions.json`, so
repeat runs make no API calls and a fixed `--seed` is reproducible.

### Setup

The API key is loaded indirectly — `.env` holds a **path** to a key file, never
the key itself:

```bash
cp .env.example .env
# edit .env:  LLM_API_KEY_FILE=/path/to/your/anthropic_key   (a file containing the key)
```

`.env` is gitignored. Then run with an explicit model (required):

```bash
poetry run gen3-metadata-simulator generate \
    --schema examples/jsonschema/acdc_schema_v1.1.5.json \
    --provider llm --llm-model claude-haiku-4-5 \
    --num-records 5 --seed 1
```

Extra flags: `--llm-model` (required), `--cache-path` (default
`.cache/distributions.json`). See [`docs/dev-notes.md`](docs/dev-notes.md) for
the design and the pluggable `ValueProvider` / `SpecSource` interfaces.

## Documentation

- **[docs/dev-notes.md](docs/dev-notes.md)** — start here. A ground-up,
  junior-dev-friendly walkthrough of how it all works: the pipeline, the value
  providers, a worked example, design decisions, a module map, and how to
  extend it.
- **[docs/usage.md](docs/usage.md)** — every CLI flag for `generate` and
  `validate`, with examples.

## Development

```bash
poetry run python3 -m pytest    # run the test suite (fully offline)
```

The example dictionary in `examples/jsonschema/` is the test fixture. The key
tests are the round-trips (`tests/test_roundtrip.py`, `tests/test_roundtrip_llm.py`):
generate → validate → assert zero errors. New to the codebase? Read
[docs/dev-notes.md](docs/dev-notes.md) first.

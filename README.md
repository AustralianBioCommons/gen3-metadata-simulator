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

See [`docs/architecture.md`](docs/architecture.md) for the full data flow and
[`docs/usage.md`](docs/usage.md) for every flag.

## Roadmap: realistic clinical values (v2)

Today, numeric values are random within schema bounds. The headline upcoming
feature is an **LLM-backed value provider**: a lightweight model is asked for the
plausible mean and standard deviation of each numeric clinical variable (by name
and description), the answers are cached as a distribution table, and values are
sampled from those distributions — so "blood pressure" lands near 120/80 instead
of an arbitrary number. Categorical fields keep sampling from their enums. The
`ValueProvider` interface is already in place
(`src/gen3_metadata_simulator/providers/`); see `docs/architecture.md`.

## Development

```bash
poetry run python3 -m pytest    # run the test suite
```

The example dictionary in `examples/jsonschema/` is used as the test fixture;
`tests/test_roundtrip.py` asserts generated metadata validates with zero errors.

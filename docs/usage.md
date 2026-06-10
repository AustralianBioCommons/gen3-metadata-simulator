# Usage

## `generate`

Generate a full set of linked metadata files from a schema.

```bash
poetry run gen3-metadata-simulator generate --schema <path> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--schema`, `-s` | *(required)* | Path to the bundled Gen3 JSON schema. |
| `--output-dir`, `-o` | `./output` | Directory to write metadata files into (created if absent). |
| `--num-records`, `-n` | `30` | Number of records to generate per node. |
| `--project-code`, `-p` | `simulated_project` | Project `code`; child nodes link to the project by this value. |
| `--seed` | *(none)* | RNG seed. Set it for byte-identical, reproducible output. |
| `--provider` | `random` | Value strategy: `random` (v1) or `llm` (v2, not yet implemented). |
| `--array-size` | `0` | Number of elements to emit for array-typed properties. `0` emits `[]`. |
| `--skip-validation` | off | Write output without self-validating first. |

On success the command prints `0 validation errors` and a summary of files
written. If validation fails, it prints the errors (grouped by node, with a few
concrete examples) and exits non-zero **without** writing any files.

### Examples

Reproduce a 30-record dataset:

```bash
poetry run gen3-metadata-simulator generate \
    -s examples/jsonschema/acdc_schema_v1.1.5.json \
    -o ./output -n 30 -p AusDiab_Simulated --seed 1
```

Small dataset with populated arrays:

```bash
poetry run gen3-metadata-simulator generate \
    -s examples/jsonschema/acdc_schema_v1.1.5.json \
    -n 5 --array-size 3
```

## `validate`

Validate an existing directory of metadata files against a schema. Useful for
checking data this tool did not produce, or output written with
`--skip-validation`.

```bash
poetry run gen3-metadata-simulator validate \
    --schema <path> --metadata-dir <dir>
```

| Flag | Default | Description |
|------|---------|-------------|
| `--schema`, `-s` | *(required)* | Path to the bundled Gen3 JSON schema. |
| `--metadata-dir`, `-m` | *(required)* | Directory of `<node>.json` files to validate. |

Every `*.json` file in the directory is loaded; the file stem is treated as the
node name. Records are validated per their `type` and a grouped error summary is
printed. Exit code is non-zero if any record fails.

## Interpreting validation output

Validation runs `gen3_validator.validate.validate_list_dict`, which checks each
record against its node schema with a JSON Schema Draft-4 validator. A failure
line looks like:

```
  - [demographic#3] sex: 'unknown' is not one of ['male', 'female', ...]
```

meaning record index 3 of the `demographic` node has an out-of-domain `sex`
value. `[node#index] key: message`.

## Notes on the reference example

The bundled `examples/metadata/AusDiab_Simulated/` dataset does **not** itself
fully validate (its `project.json` omits the required `programs` link and uses a
`null` enum value). This tool's output *does* validate: it emits the required
`programs` link with a synthesized program reference and never nulls a
non-nullable enum. The tool also generates `data_release` (a submittable node the
hand-made example skipped).

# Usage

> For how the tool works under the hood, see [dev-notes.md](dev-notes.md).
>
> Examples below use `poetry run` for local development. If you installed via
> `pip install gen3-metadata-simulator`, drop the `poetry run` prefix — the
> command is just `gen3-metadata-simulator …`.

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
| `--provider` | `random` | Value strategy: `random` (schema-driven random) or `llm` (realistic values via a lightweight model). |
| `--llm-provider` | *(from `.env`)* | LLM vendor override: `anthropic` or `openai`. Defaults to `.env` `LLM_PROVIDER`. |
| `--llm-model` | *(from `.env`)* | LLM model override, e.g. `claude-haiku-4-5` / `gpt-4o-mini`. Defaults to `.env` `LLM_MODEL`. |
| `--env-file` | `./.env` | Path to an env file with `LLM_*` settings. Useful when the tool is installed and run from elsewhere. |
| `--cache-path` | `.cache/distributions.json` | Where the LLM provider caches field specs (so repeat runs make no API calls). |
| `--refresh-llm` | off | Force fresh LLM estimates, ignoring the cache (re-estimates every field). |
| `--array-size` | `0` | Number of elements to emit for array-typed properties. `0` emits `[]`. |
| `--skip-validation` | off | Write output without self-validating first. |
| `--verbose`, `-v` | off | Log progress milestones (schema loaded, warmup cache breakdown, validation, write). |
| `--debug` | off | Log per-item detail (generation order, which fields are re-estimated and why) plus full tracebacks; also enables the Anthropic SDK's own logging. |

### Realistic values with `--provider llm`

The LLM provider asks a lightweight model (Anthropic **or** OpenAI) for each
field's realistic properties (numeric distribution + limits, plausible date
ranges, example text), caches them, and samples from the cache. See
[dev-notes.md → Value providers](dev-notes.md#3-value-providers--where-the-values-come-from).

Set up once — create a `.env` with three values. It holds the vendor, the model,
and a **path** to your key file (never the key itself):

```ini
# .env
LLM_PROVIDER=anthropic        # or: openai
LLM_MODEL=claude-haiku-4-5    # or e.g. gpt-4o-mini
LLM_API_KEY_FILE=/absolute/path/to/your/api_key
```

(Working in a clone of this repo? `cp .env.example .env` for a ready template.)

Then select the LLM strategy (vendor + model come from `.env`):

```bash
poetry run gen3-metadata-simulator generate \
    -s examples/jsonschema/acdc_schema_v1.1.5.json \
    --provider llm -n 5 --seed 1
```

The first run estimates field specs in **parallel batches** (so warmup takes
tens of seconds, not minutes) and shows a live `Estimating field specs: N/M
batches` counter on an interactive terminal. Later runs reuse the cache.

Override the `.env` per run with `--llm-provider anthropic|openai`,
`--llm-model <id>`, and `--env-file <path>` — e.g. to try OpenAI without editing
the file, or to point at config outside the current directory.

**Where the settings come from.** Each is read from `--env-file` (or `./.env`),
then the process environment. The API key resolves as: `LLM_API_KEY_FILE` (a
path to the key — use an **absolute** path, since a relative one is resolved
against the working directory) → otherwise the vendor's standard variable
(`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`), which the SDK reads directly. If
neither is set you get a clear error before any API call.

**Which should I use — key file or env var?**

- **Local / dev → key file (recommended).** Keep the key in its own file
  (`chmod 600`, outside the repo) and put only its *path* in `.env`. The secret
  then stays out of your shell history, out of every child process's environment
  (where credential scanners and careless dependencies look first), and out of
  anything you commit — `.env` carries a path, never the key. It also rotates by
  editing one file and can point at a secrets-manager mount.
- **CI / containers → env var.** Platforms inject secrets as environment
  variables, they're ephemeral (never written to disk), and there's no shell
  history — so `export OPENAI_API_KEY=…` (read automatically by the SDK) is the
  right call there. Avoid persisting an `export` in your shell rc files for local
  use; that's the weakest option (long-lived plaintext, inherited everywhere).

> Two "provider" words: `--provider random|llm` is the *value strategy*;
> `LLM_PROVIDER` / `--llm-provider` (`anthropic|openai`) is the *LLM vendor*.

The cache fingerprints each field by its schema, so a later run against an
**edited** schema re-estimates only the fields that actually changed; everything
else is reused with no API call. To re-estimate everything regardless, add
`--refresh-llm`.

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
| `--verbose`, `-v` | off | Log progress milestones. |
| `--debug` | off | Log detail plus full tracebacks. |

## Logging & debugging

Both commands are quiet by default (only the final result line, plus warnings
and errors). Add `--verbose` for a clean set of progress milestones — schema
loaded, the LLM warmup cache breakdown (fields reused vs re-estimated, and how
many API calls were made), validation result, and files written. Add `--debug`
for per-item detail (generation order, each re-estimated field and why) and full
tracebacks on failure. Noisy dependency logs are kept at WARNING so the output
stays readable.

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

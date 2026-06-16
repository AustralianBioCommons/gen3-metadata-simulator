# gen3-metadata-simulator

[![PyPI](https://img.shields.io/pypi/v/gen3-metadata-simulator.svg)](https://pypi.org/project/gen3-metadata-simulator/)
[![Python](https://img.shields.io/pypi/pyversions/gen3-metadata-simulator.svg)](https://pypi.org/project/gen3-metadata-simulator/)

Generate **realistic, linked, schema-valid** Gen3 metadata from a Gen3 data
dictionary. Point it at a bundled Gen3 JSON schema and it produces one JSON file
per node — every foreign key resolving to a real parent — then self-validates
with [`gen3-validator`](https://pypi.org/project/gen3-validator/) before writing.

Its headline feature: a lightweight **LLM fills each field with believable
clinical values** — numeric distributions with real-world limits, valid calendar
dates, and domain-appropriate text — that still pass validation. So `month_birth`
lands in `[1, 12]`, dates are real, and an assay `description` reads like one.

## Install

```bash
pip install gen3-metadata-simulator      # Python ≥ 3.9
```

## Quickstart — realistic data with an LLM

The core feature. Three steps:

**1. Configure the model + key.** Put your API key in its own file and lock it
down (works with OpenAI or Anthropic):

```bash
mkdir -p ~/.config/gen3-sim
printf 'sk-...' > ~/.config/gen3-sim/openai_key   # your key, in its own file
chmod 600 ~/.config/gen3-sim/openai_key           # readable only by you
```

Then create a `.env` in the directory you'll run from, pointing at that key file:

```ini
# .env  — works with OpenAI or Anthropic
LLM_PROVIDER=openai                            # or: anthropic
LLM_MODEL=gpt-4o-mini                          # or e.g. claude-haiku-4-5
LLM_API_KEY_FILE=/absolute/path/to/openai_key
```

`.env` holds only the *path*, never the key — keep it untracked (add it to your
`.gitignore`). This keeps the secret out of your shell history, out of every
child process's environment, and out of anything you commit; see
[docs/usage.md](docs/usage.md) for the full rationale and resolution rules.
(Working in a clone of this repo? `cp .env.example .env` for a ready template.)

**2. Generate** (provider + model come from `.env`):

```bash
gen3-metadata-simulator generate --schema your-gen3-schema.json --provider llm --num-records 30
```

> Cloned this repo to try it out? Use the bundled schema
> `examples/jsonschema/acdc_schema_v1.1.5.json`.

**3. You get** a self-validated `./output/` — realistic numbers within real
limits, valid dates, sensible text. Field estimates are cached
(`.cache/distributions.json`), so reruns make no API calls and `--seed` is
reproducible.

**In CI or a quick one-off?** Skip the file and let the SDK read the vendor's
standard env var instead — ephemeral and the native choice for CI/containers:

```bash
export OPENAI_API_KEY=sk-...   # injected as a secret in CI; avoid persisting it locally
gen3-metadata-simulator generate -s your-gen3-schema.json --provider llm --llm-provider openai --llm-model gpt-4o-mini -n 30
```

### No API key? Random placeholder values

Drop the LLM flags for schema-valid (but non-realistic) random data — no key
needed:

```bash
gen3-metadata-simulator generate --schema your-gen3-schema.json --num-records 30
```

## What you get

- `<node>.json` — a JSON array of N linked records per node.
- `project.json` — the single project object.
- `DataImportOrder.txt` — node order for sequential Gen3 submission.

Everything is validated with `gen3-validator` first; if validation fails, nothing
is written.

## Documentation

- **[docs/usage.md](docs/usage.md)** — every command and flag, LLM configuration,
  logging, and interpreting output.
- **[docs/dev-notes.md](docs/dev-notes.md)** — how it works: the pipeline, value
  providers, design decisions, and how to extend it.

## Development

```bash
poetry install
poetry run python3 -m pytest          # fully offline
```

New to the codebase? Start with [docs/dev-notes.md](docs/dev-notes.md).

# Developer Notes

A friendly, ground-up explanation of how `gen3-metadata-simulator` works —
written for someone new to the codebase (and to Gen3). If you can read Python
and have seen JSON, you have enough to follow along.

> TL;DR: we read a **Gen3 data dictionary** (a big JSON file describing node
> types and how they link), figure out a safe order to fill them in, then write
> one JSON file per node full of fake-but-valid records that point at each other
> correctly. Optionally, an LLM makes the values *realistic* instead of random.

---

## 1. The problem, in plain terms

Gen3 is a platform for hosting research data. Each Gen3 commons has a **data
dictionary** (a "schema") that defines:

- **node types** — like `subject`, `sample`, `demographic`, `lipidomics_assay`.
  Think of each as a table.
- **properties** — the columns of each table (e.g. `subject` has `patient_id`,
  `consent_codes`, …), each with a type and sometimes constraints.
- **links** — foreign keys between nodes (e.g. a `sample` belongs to a
  `subject`). This makes the whole dictionary a **graph**: nodes connected by
  links.

To test or demo a commons you need example data that (a) matches the dictionary
and (b) links together correctly (every `sample` points at a `subject` that
actually exists). Writing that by hand is painful. This tool generates it.

**Input:** one bundled Gen3 JSON schema (see
`examples/jsonschema/acdc_schema_v1.1.5.json`).
**Output:** one `<node>.json` per node + a `DataImportOrder.txt`, matching the
layout in `examples/metadata/AusDiab_Simulated/`.

---

## 2. The five-step pipeline

Everything the tool does is this pipeline. Follow it top to bottom:

```
  schema.json
     │  (1) LOAD + RESOLVE        schema.py        → resolved node schemas
     ▼
  ordering.generation_order()     ordering.py      → parents-before-children list
     │  (2) ORDER
     ▼
  MetadataGenerator.generate()    generator.py     → records per node
     │  (3) GENERATE  ── per record: links + property values
     ▼
  validation.self_validate()      validation.py    → must be ZERO errors
     │  (4) VALIDATE
     ▼
  writers.write_outputs()         writers.py       → <node>.json + DataImportOrder.txt
        (5) WRITE
```

If validation finds any error, we **refuse to write** and exit non-zero. The
output is only ever written if it's provably valid.

### Step 1 — Load & resolve (`schema.py`)

The raw schema uses `$ref` to share common definitions (e.g. every node reuses
`_definitions.yaml#/ubiquitous_properties` for `type`/`submitter_id`/…). Those
references have to be "inlined" before we can read a node's real shape. We let
the `gen3-validator` library do this:

```python
loader = SchemaLoader(schema_path).load()   # runs ResolveSchema under the hood
loader.validate_is_gen3_schema()            # sanity-check it's really a Gen3 dict
loader.node_schema("demographic")           # the fully-resolved demographic node
loader.submittable_nodes()                  # the node names we will generate
```

`submittable_nodes()` deliberately excludes helper keys (`_definitions.yaml`,
`_settings.yaml`, …) and `program` (which is administrative, never generated).

### Step 2 — Order the nodes (`ordering.py`)

A child can't reference a parent that doesn't exist yet, so we must generate
**parents before children**. The links form a directed graph; we run a
[topological sort](https://en.wikipedia.org/wiki/Topological_sorting) (Kahn's
algorithm) over the link edges:

```python
order = generation_order(loader.resolver, set(loader.submittable_nodes()))
# e.g. ['project', 'subject', 'clinical_descriptor', 'sample', 'lipidomics_assay', ...]
```

> **Gotcha we handle:** `gen3-validator`'s own ordering forces
> `core_metadata_collection` to the very end, but file nodes link *to* it — so
> it has to come *before* them. We compute our own sort instead of using theirs.
> (See the comment block at the top of `ordering.py`.)

### Step 3 — Generate records (`generator.py`)

This is the heart of the tool. For each node, in order, we build `num_records`
records. Each record is a dict. Walking `MetadataGenerator._make_record(node)`:

1. Start with `type` (the node name) and a unique `submitter_id` like
   `demographic_retrade_biophysiological` (the node name + two random words).
2. Work out which keys to emit: **declared properties minus system properties**
   (see "Key concepts" below for why).
3. For each key:
   - if it's a **link** → emit `{"submitter_id": "<a real parent's id>"}` (or
     `{"code": "<project code>"}` for links to the project). The parent is
     picked from the `GeneratedRecordRegistry`, which holds everything generated
     so far — and because parents come first, there's always one to point at.
   - otherwise it's a **data property** → ask the **value provider** for a value
     (see §3).
4. Record it in the registry (so children can later link to it) and return it.

`project` is special: it's a single object (not a list), keyed by `code` instead
of `submitter_id`.

### Step 4 — Validate (`validation.py`)

We flatten every record into one list and hand it to
`gen3_validator.validate.validate_list_dict(records, resolved_schema)`. It
checks each record against its node's JSON Schema (Draft-4) and returns a list
of failures — **empty means everything is valid**. We bail if it's non-empty.

### Step 5 — Write (`writers.py`)

`project.json` is written as a single object; every other node as a JSON array.
`DataImportOrder.txt` is the order list, one node name per line — exactly the
sequence Gen3 expects for submission.

---

## 3. Value providers — where the values come from

Steps above decide *which* fields to fill and *who links to whom*. A
**`ValueProvider`** decides the actual *value* of each non-link property. This
is a pluggable strategy (`providers/base.py`):

```python
class ValueProvider(ABC):
    def value(self, req: ValueRequest) -> Any: ...      # produce one value
    def warmup(self, requests) -> None: ...             # optional pre-pass (default: no-op)
```

A `ValueRequest` is a little bundle describing one property: its `node`, `name`,
`description`, `json_type`, `enum`, regex `pattern`, `format`, numeric
`minimum`/`maximum`. The generator builds one and never cares which provider is
plugged in — that's the whole point of the interface.

There are two providers:

### `RandomValueProvider` (the default, `providers/random_provider.py`)

Schema-driven randomness, all from one seeded `random.Random` (so `--seed`
makes runs reproducible):

| property | value |
|----------|-------|
| enum | a random allowed value |
| integer / number | a bounded random number (respects `minimum`/`maximum`) |
| boolean | random `True`/`False` |
| string with a `pattern` | a string matching the regex, via `rstr` |
| plain string | a readable two-word token like `focometer_quinch` |
| array | `[]` (or `--array-size` sampled items) |

It's fast and dependency-light, but the values are nonsense — a `bmi_baseline`
might be `41.7`, a date might be `3170-94-14` (regex-valid, but month 94 isn't
real).

### `LLMValueProvider` (`--provider llm`, `providers/llm_provider.py`)

The headline feature: use a lightweight LLM's domain knowledge to make values
*believable* while keeping them schema-valid. It doesn't call the model
per-record (that'd be slow and expensive). Instead:

1. **`warmup()`** runs once before generation. It collects every numeric/date/
   text field, asks the model for a compact **spec** per field, and caches them
   to `.cache/distributions.json`. Fields already in the cache (unchanged) are
   skipped — see "Cache invalidation" below.
2. **`value()`** then just samples from the cached spec — no network, fully
   reproducible under a seed.

The cleverness is routing. `providers/classify.py::field_kind(req)` sorts every
field into one of four buckets, and each bucket is handled differently:

| kind | example field | what the LLM provides | how a value is made |
|------|---------------|------------------------|---------------------|
| `numeric` | `bmi_baseline`, `month_birth` | mean, stddev, **min/max limits**, unit | `gauss(mean, stddev)` then clamp to limits; round if integer |
| `date` | `baseline_date`, `intended_release_date` | a plausible `earliest`..`latest` window | pick a **real calendar date** in range (`dates.py`), render to the field's pattern, verify it matches |
| `text` | `assay_description` | a pool of realistic example strings | `rng.choice` of the pool |
| `other` | `sex` (enum), `sample_source` (UBERON pattern), `md5sum` | — | fall back to `RandomValueProvider` |

Two concrete wins:

- **`month_birth`** is an integer. The LLM says `min=1, max=12`, so a generated
  month is always a real month — not just "any integer ≥ 0".
- **`baseline_date`** must match `^[0-9]{4}-[0-9]{2}-[0-9]{2}$`. We generate an
  actual `datetime.date` in the LLM's plausible window, so the month is 1–12 and
  the day is valid; then we render it `YYYY-MM-DD` and double-check it matches
  the regex. No more `3170-94-14`.

Anything the LLM didn't cover (or any non-LLM kind) quietly falls back to the
random provider, so generation can never *fail* for lack of a spec.

#### Where do the specs come from? `SpecSource` (`providers/specs.py`)

`warmup()` doesn't talk to Anthropic directly — it talks to a `SpecSource`, an
interface with one method `estimate(requests, text_pool_size) -> {key: FieldSpec}`.
This indirection is what makes the whole provider testable offline:

- **`AnthropicSpecSource`** — the real one. Batches ~20 fields per call to
  `client.messages.parse(...)` with a Pydantic schema (structured output, so the
  model is forced to return valid JSON), and maps the reply into `FieldSpec`s.
  The Anthropic client is injectable, so tests pass a fake instead of the network.
- **A fake source** (in the tests) — returns canned specs. Every test runs with
  no network, no API key, no cost.

A `FieldSpec` is just a frozen dataclass holding the per-kind fields
(`mean/stddev/min/max/unit`, or `earliest/latest`, or `examples`). `SpecCache`
loads/saves the whole table to JSON.

#### Cache invalidation — re-estimating only what changed

The cache would be a trap if it never noticed the schema changed: edit a field's
type and you'd keep getting stale (or randomly-fallen-back) values. To avoid
that, every cache entry stores an **md5 fingerprint of that field's JSON schema**
alongside the spec:

```json
"demographic/month_birth": {
  "fingerprint": "63f969cda984d46534b3905cc3f20e47",
  "spec": {"kind": "numeric", "mean": 6.5, "stddev": 3.4, "minimum": 1, "maximum": 12, "unit": "month"}
}
```

The fingerprint is computed in `generator._build_request` from the resolved
property schema (type, enum, pattern, bounds, description — anything that affects
generation). On each run, `warmup()` compares it against the cached one:

- **unchanged** (fingerprint matches) → reuse the cached spec, no API call;
- **changed** (a property's type/bounds were edited) → the md5 differs, so *just
  that field* is re-estimated and its entry overwritten;
- **new** field → no entry yet, so it's estimated;
- removed field → its stale entry is simply ignored.

So pointing the tool at an edited schema refreshes only the affected fields — not
the whole table. Need everything regenerated regardless? Pass `--refresh-llm` to
ignore the cache and re-estimate every field. Old cache files written before
fingerprinting still load (their entries just lack a fingerprint, so they rebuild
once on the next run).

#### How the API key is loaded (`config.py`)

By design, the key is **never** stored in the repo or in `.env`. Instead:

```
.env  →  LLM_API_KEY_FILE=/path/to/keyfile   (a PATH, gitignored)
keyfile (outside the repo)  →  sk-ant-...      (the actual key)
```

`load_api_key()` reads `.env`, follows the path, reads the key file, and returns
the key. If the variable is missing or the file is missing/empty, you get a
clear `ConfigError` instead of a confusing 401 later.

---

## 4. A worked example: one `demographic` record

Putting it together. Say we're generating a `demographic` record with the LLM
provider:

1. `demographic` links to `clinical_descriptor`. The registry already has
   clinical_descriptor records (it came earlier in the order), so the link
   becomes `{"submitter_id": "clinical_descriptor_quartful_rheophore"}`.
2. `sex` is an enum → `field_kind` says `other` → random pick: `"female"`.
3. `month_birth` is an integer → `numeric` → cached spec `{mean 6.5, std 3.4,
   min 1, max 12}` → `gauss` then clamp → `4`.
4. `bmi_baseline` is a number → `numeric` → `{mean 27, std 5, min 12, max 60}`
   → `26.81…`.
5. `baseline_date` matches a date pattern → `date` → real date in
   `1990–2020` rendered `YYYY-MM-DD` → `"2004-08-17"`.
6. `id`, `state`, `project_id`, … are **system properties** → not emitted.

Result: a record that reads like a real participant and passes validation.

---

## 5. Key concepts & gotchas

- **System properties are dropped.** Gen3 nodes set `additionalProperties:
  false`, and fields like `id`, `state`, `created_datetime` are assigned by the
  server. We emit *declared properties minus `systemProperties`*. `type` and
  `submitter_id` survive (they come from the shared "ubiquitous properties", not
  from `systemProperties`).
- **Referential integrity is free** because of the topological order: when we
  generate node X, every node X links to already exists in the registry.
- **`program` is never generated** (it's not submittable). The `project`'s
  required `programs` link is filled with a synthesized placeholder so the
  output still validates.
- **Subgroup links are flattened.** A file node can link to several parents at
  once via a `subgroup`; `links.py::extract_links` returns one `LinkSpec` per
  member so every foreign key is emitted.
- **Patterns matter for strings.** A string with a regex `pattern` (UBERON,
  ORCID, md5sum, dates) is generated to match it. The random provider uses
  `rstr`; the LLM date path generates a real date and *verifies* the match.
- **Determinism.** All randomness flows through a single seeded
  `random.Random`. Same `--seed` ⇒ identical output (after warmup, for the LLM
  provider, since the cache is fixed).

---

## 6. How to run, test, and extend

### Run it

```bash
poetry install
# random values
poetry run gen3-metadata-simulator generate -s examples/jsonschema/acdc_schema_v1.1.5.json -n 30 --seed 1
# realistic values (needs .env → LLM_API_KEY_FILE; see docs/usage.md)
poetry run gen3-metadata-simulator generate -s examples/jsonschema/acdc_schema_v1.1.5.json \
    --provider llm --llm-model claude-haiku-4-5 -n 5 --seed 1
```

Runs are quiet by default. Add `--verbose` to see milestones (including the LLM
warmup cache breakdown: fields reused vs re-estimated, and API calls made), or
`--debug` for per-item detail and full tracebacks. Logging uses the standard
`logging` module, one logger per module (`logging.getLogger(__name__)`); the CLI
just sets the level via `configure_logging`.

### Test it

```bash
poetry run python3 -m pytest -q
```

Tests are **fully offline** — the LLM tests inject a fake `SpecSource` or mock
the Anthropic client, so no key or network is needed. The most important test is
the **round-trip** (`test_roundtrip.py` and `test_roundtrip_llm.py`): generate →
validate → assert zero errors. If you change generation, that's the test to
watch.

### Extend it — add a new value strategy

Want values from, say, a CSV of real distributions instead of an LLM? Implement
the interface and plug it into the CLI:

```python
from gen3_metadata_simulator.providers.base import ValueProvider

class CsvValueProvider(ValueProvider):
    def value(self, req): ...      # return one value for req
    # warmup() is optional — override if you need a pre-pass
```

Nothing in `generator.py`, `writers.py`, or `validation.py` changes — they only
know the interface.

### Extend it — swap where LLM specs come from

The LLM provider doesn't care *who* produces specs, only that they implement
`SpecSource.estimate(...)`. To use a different model vendor or a local file,
write a new `SpecSource` and pass it to `LLMValueProvider`.

---

## 7. Module map (where to look)

| File | Responsibility |
|------|----------------|
| `cli.py` | Typer CLI: `generate` and `validate` commands, flag parsing, provider wiring |
| `schema.py` | Load + resolve the schema; expose resolved nodes and the submittable set |
| `ordering.py` | Topological sort → generation/import order |
| `links.py` | Read a node's links, flattening subgroups → `LinkSpec`s |
| `registry.py` | Remember generated records so children can link to real parents |
| `generator.py` | The record factory + the per-run orchestration |
| `validation.py` | Run `gen3_validator` and summarize failures |
| `writers.py` | Write `<node>.json` files and `DataImportOrder.txt` |
| `config.py` | Load the LLM API key via the `LLM_API_KEY_FILE` indirection |
| `providers/base.py` | `ValueProvider` interface + `ValueRequest` |
| `providers/random_provider.py` | Random, schema-driven values (default) |
| `providers/classify.py` | `field_kind` — route a field to numeric/date/text/other |
| `providers/specs.py` | `FieldSpec`, `SpecCache`, `SpecSource`, `AnthropicSpecSource` |
| `providers/dates.py` | Real-calendar-date generation rendered to a pattern |
| `providers/llm_provider.py` | `LLMValueProvider` — ties specs + dates + random together |
| `errors.py` | Typed exceptions (`InvalidGen3SchemaError`, `ConfigError`, …) |

For every CLI flag and option, see [`usage.md`](usage.md).

---

## 8. Glossary

- **node** — a type in the Gen3 dictionary (≈ a table), e.g. `sample`.
- **record** — one generated instance of a node (≈ a row).
- **link** — a foreign key from one node to another; rendered as a nested
  `{"submitter_id": ...}` (or `{"code": ...}` for the project).
- **submitter_id** — a record's human-readable unique id; how other records
  refer to it.
- **resolved schema** — the schema after all `$ref`s have been inlined.
- **topological order** — an ordering where every node comes after the nodes it
  depends on (its link targets).
- **spec / `FieldSpec`** — the LLM's hint for one field (distribution + limits,
  or a date window, or example strings).
- **warmup** — the one-time pass that fills the spec cache before generation.

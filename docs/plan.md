# gen3-metadata-simulator — Implementation Plan (v1)

## Context

The repo is currently empty (just `examples/` + `.gitignore`). The goal is a Poetry-managed Python CLI tool that:

1. Takes a **bundled Gen3 JSON schema** as input (e.g. `examples/jsonschema/acdc_schema_v1.1.5.json` — a dict keyed by `"<node>.yaml"` with graph-based node defs: `links`, `properties`, `required`, enums, `$ref`s into `_definitions.yaml`).
2. **Validates** it is a valid Gen3 schema.
3. **Generates simulated JSON metadata** — one file per node, records linked via `submitter_id` foreign keys with guaranteed referential integrity, generated in topological (import) order — matching the format of `examples/metadata/AusDiab_Simulated/`.
4. Writes a `DataImportOrder.txt` and **self-validates** the output using `gen3_validator.validate.validate_list_dict`.
5. Designed for a **future LLM value provider** (lightweight model returns mean/stddev per numeric clinical variable; samples realistic values) via a pluggable `ValueProvider` interface. Not implemented in v1 — random values only.

Dependencies (both on PyPI, source mirrored in `examples/ref/` for reference):
- `gen3-validator ^2.0.1` — `ResolveSchema` ($ref resolution), `DataDictionary.calculate_node_order()` (Kahn topological sort over link graph), `validate.validate_list_dict(data_list, resolved_schema)` (Draft4 validation; returns list of FAIL dicts, empty = pass; each record must carry a `"type"` key).
- `Gen3SchemaDev ^2.3.6` — secondary/optional schema validators (`RuleValidator`, metaschema validator).

## Verified output-format ground truth (from examples/metadata/AusDiab_Simulated/)

- **27 node JSON files + DataImportOrder.txt**. No `program.json`, no `data_release.json`.
- `project.json` = **single object** (not array); has `"code"` (no submitter_id); **drops the `programs` link entirely**; keys alphabetically sorted, `indent=4`.
- All other files = **JSON array of N records** (example N=30), `indent=4`, sorted keys.
- Each record: `"type"`, `"submitter_id"` (`<node>_<word>_<word>` pattern), link props as nested objects — `{"submitter_id": "<parent's submitter_id>"}`, or `{"code": "<project code>"}` for links targeting project. Link key = link `name` (e.g. `clinical_descriptors`).
- System properties (`id`, `state`, `project_id`, `created_datetime`, `updated_datetime`) are **excluded**; `additionalProperties: false` means emit ONLY declared schema properties.
- **Subgroup links are flattened** — every subgroup member is emitted (verified in qc_file/genomics_file), regardless of `exclusive`/per-member `required`.
- Array props emitted as `[]` (e.g. `consent_codes`); some optional fields emitted as `null`.
- `DataImportOrder.txt` = **plain node names, one per line, newline-separated** (verified via od: `project\nacknowledgement\n...`). NOT numbered/tab-separated.
- Date fields are plain `type: string` in this schema (no `format` enforcement) — random strings validate fine (example contains "3170-94-14").

## Key gotchas (verified against gen3_validator source)

- `DataDictionary.calculate_node_order()` includes non-generatable entries (`program`, phantom `data_release`) → **must filter node_order to real submittable schema nodes**.
- `get_node_order()` forces `core_metadata_collection` last; the example places it 5th. Both are valid topological orders — use the validator's order and document the divergence.
- `validate_list_dict` looks up node schema by each record's `type` (handles `.yaml` suffix itself) and **raises** if missing — every record must have `type`.

## Package layout

```
pyproject.toml            README.md
docs/  plan.md  usage.md  architecture.md
src/gen3_metadata_simulator/
  __init__.py
  cli.py                  # Typer app
  schema.py               # SchemaLoader: wraps ResolveSchema + gen3 schema validity check
  ordering.py             # generation_order(): node_order filtered to generatable nodes
  links.py                # LinkSpec dataclass + extract_links() (subgroup flattening)
  registry.py             # GeneratedRecordRegistry (referential integrity)
  generator.py            # MetadataGenerator: record factory, orchestration
  providers/
    base.py               # ValueProvider ABC + ValueRequest dataclass
    random_provider.py    # v1 random values
    llm_provider.py       # v2 stub (NotImplementedError), interface defined now
  writers.py              # per-node JSON + DataImportOrder.txt
  validation.py           # self-validate wrapper + report rendering
  errors.py
tests/  conftest.py  test_schema.py  test_ordering.py  test_links.py
        test_generator.py  test_writers.py  test_providers.py  test_roundtrip.py
```

### pyproject.toml essentials
- Poetry, `python = "^3.10"`, src layout.
- Deps: `gen3-validator ^2.0.1`, `Gen3SchemaDev ^2.3.6`, `typer ^0.12`, `rich` (validation report tables). Dev: `pytest ^8`.
- Script entry: `gen3-metadata-simulator = "gen3_metadata_simulator.cli:app"`.
- CLI framework: **Typer** (type-hint flags, free enum/validation handling, built on Click).

## Module design

### schema.py — SchemaLoader
- `load()`: `ResolveSchema(schema_path).resolve_schema()` → keep `.schema_resolved` (dict keyed `"<id>.yaml"`, exact shape `validate_list_dict` expects) and the underlying `DataDictionary`.
- `validate_is_gen3_schema()`: assert `_definitions.yaml`/`_settings.yaml` present, `get_schema_version()` parses, every node resolves; optionally run Gen3SchemaDev `RuleValidator` as a secondary check (pin exact entrypoint during implementation from `examples/ref/gen3schemadev/`). Raise `InvalidGen3SchemaError` with a clear message on failure.
- `submittable_nodes()`: resolved node keys minus `_definitions/_terms/_settings/program/root/metaschema`.
- `node_schema(node)`: resolved node dict.

### ordering.py
- `generation_order(dd, generatable)`: `dd.calculate_node_order()` → filter `dd.node_order` to `generatable` (drops `program`, `data_release`, phantoms). Result = generation order AND DataImportOrder.txt content. `project` first.

### links.py
- `LinkSpec(name, target_type, multiplicity, required)`.
- `extract_links(node_schema)`: iterate `links`; flatten `subgroup` members; skip links whose `target_type` isn't generated (e.g. project→program).

### registry.py — GeneratedRecordRegistry
- Stores records per node; `random_parent_submitter_id(node, rng)` picks from already-generated parents (topological order guarantees they exist); `project_code()`; raises `MissingParentError` if ordering ever breaks.

### generator.py — MetadataGenerator
- `__init__(loader, value_provider, num_records, project_code, seed)`; single `random.Random(seed)` for full determinism.
- `generate()`: project first (single object, `code=project_code`, no submitter_id, no programs link), then each node in order × `num_records` records.
- `_make_record(node)`:
  - emit keys = resolved properties keys − `systemProperties` (resolved props already merge ubiquitous `type`/`submitter_id` via $ref).
  - `type` = node; `submitter_id` = `f"{node}_{word}_{word}"`.
  - links → `{"code": ...}` for project target else `{"submitter_id": registry.random_parent_submitter_id(...)}`.
  - other props → `ValueRequest` → `value_provider.value(req)`.

### providers/ — pluggable values
- `ValueRequest(node, name, description, json_type, enum, item_request, fmt, minimum, maximum)`.
- `ValueProvider` ABC: `value(req)` + `warmup(requests)` (no-op default; LLM provider will batch-precompute its mean/stddev table here).
- `RandomValueProvider`: enum→`rng.choice`; integer/number→bounded random; boolean→choice; string→two random words (small embedded wordlist, matches example style); array→`[]` by default (always valid without minItems), `--array-size N` to fill; occasional `null` for optional fields; name heuristic for `md5sum` (real 32-hex).
- `LLMValueProvider` (v2 stub): documented design — `warmup` prompts a lightweight model (Haiku-class) with variable name+description for mean/stddev, caches a distribution table JSON on disk, `value` samples `rng.gauss(mean, std)` clamped to min/max; categorical defers to enum sampling. Raises `NotImplementedError` in v1.

### writers.py
- `project.json` as object; others as arrays; `json.dump(..., indent=4, sort_keys=True)` to match example.
- `DataImportOrder.txt` = `"\n".join(order) + "\n"` (plain names).

### validation.py
- Flatten all records (project object + arrays) → `validate.validate_list_dict(data_list, loader.resolved)` → return FAIL list; render Rich table grouped by node; CLI exits 1 on failures unless `--skip-validation`.

### cli.py (Typer)
- `generate` command: `--schema` (required), `--output-dir` (default `./output`), `--num-records` (default 30), `--project-code` (default `simulated_project`), `--seed`, `--provider [random|llm]` (default random), `--skip-validation`, `--array-size` (default 0).
- Flow: load → validate schema → generate → self-validate → write outputs (+ print summary).
- Bonus `validate` command: validate an existing metadata dir against a schema (reuses validation.py).

## Edge cases handled
- Optional links: emit all (matches example). many_to_many/one_to_many: single nested object (matches example); multiplicity kept on LinkSpec for v2.
- Subgroups: flatten, emit all members; ignore `exclusive` in v1 (documented).
- program: never generated; project's `programs` link dropped.
- `additionalProperties: false`: strict emit-key set from resolved properties.
- enums with `enumDef`: sample from `enum` only.
- format/pattern: carried on ValueRequest but unenforced by this dictionary (plain strings pass); documented gap for stricter schemas.

## Testing plan (pytest, per user's global test guidelines: clear inputs/outputs, standalone docstrings)
- Fixtures: example schema path; loaded SchemaLoader; generator with `seed=42, num_records=5`.
- `test_ordering`: program/data_release excluded; topological property asserted programmatically over `node_pairs`; project first.
- `test_links`: genomics_file → 2 subgroup links; qc_file → 5; demographic → clinical_descriptors.
- `test_generator`: every record has type+submitter_id; no system props; no undeclared keys; every link resolves to an existing parent (referential integrity); enums in-domain; same seed → identical output.
- `test_writers`: project.json object; arrays of N; DataImportOrder.txt plain names, project first.
- **`test_roundtrip` (critical)**: generate → `validate_list_dict` → assert zero failures, for N ∈ (1, 5, 30).
- Run via `poetry run python3 -m pytest`.

## Docs
- `README.md`: purpose, install, quickstart command, output description, roadmap (LLM provider).
- `docs/plan.md` (this plan), `docs/usage.md` (flag reference + examples), `docs/architecture.md` (data flow + ValueProvider/LLM v2 design incl. distribution-table cache format).

## Verification (end-to-end)
1. `poetry install` — resolves both PyPI deps.
2. `poetry run python3 -m pytest -q` — all green, esp. roundtrip.
3. `poetry run gen3-metadata-simulator generate --schema examples/jsonschema/acdc_schema_v1.1.5.json --output-dir /tmp/out --num-records 30 --project-code AusDiab_Simulated --seed 1` → exit 0, "0 validation errors".
4. Compare `/tmp/out` file list to example dir (same 27 json + DataImportOrder.txt); spot-check link integrity.
5. Same-seed re-run → identical output.

## Critical reference files
- `examples/ref/gen3_validator/src/gen3_validator/{dict.py,resolve_schema.py,validate.py}`
- `examples/jsonschema/acdc_schema_v1.1.5.json`
- `examples/metadata/AusDiab_Simulated/` (ground truth: project.json, demographic.json, qc_file.json, DataImportOrder.txt)

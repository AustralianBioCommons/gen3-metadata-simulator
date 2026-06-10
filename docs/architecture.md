# Architecture

## Data flow

```
schema.json
   │  SchemaLoader.load()           gen3_validator.ResolveSchema  (inlines $ref)
   ▼
resolved schema (dict keyed "<node>.yaml")
   │  ordering.generation_order()   Kahn topological sort over link edges
   ▼
generation order  ──►  MetadataGenerator.generate()
                          │  per node, per record:
                          │    • type + submitter_id
                          │    • links → real parent refs (registry)
                          │    • properties → ValueProvider.value()
                          ▼
                       node → record(s) mapping
                          │  validation.self_validate()   gen3_validator.validate_list_dict
                          ▼  (zero failures required)
                       writers.write_outputs()  ──►  <node>.json + DataImportOrder.txt
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `schema.py` | `SchemaLoader`: resolve the bundle, assert Gen3 validity, expose resolved node schemas and the set of submittable nodes. |
| `ordering.py` | `generation_order`: topological sort so parents precede children. Computes its own Kahn sort rather than `DataDictionary.get_node_order`, which force-moves `core_metadata_collection` last (wrong for generation — file nodes link *to* it). |
| `links.py` | `extract_links`: read a node's `links`, flattening `subgroup` blocks into one `LinkSpec` per member. |
| `registry.py` | `GeneratedRecordRegistry`: remember generated records so children can reference real parent `submitter_id`s. |
| `generator.py` | `MetadataGenerator`: orchestration + the per-record factory. |
| `providers/` | `ValueProvider` strategy interface and implementations. |
| `validation.py` | Flatten records and run `validate_list_dict`; summarize failures. |
| `writers.py` | Write per-node JSON files and `DataImportOrder.txt`. |
| `cli.py` | Typer CLI (`generate`, `validate`). |

## Referential integrity

Because nodes are generated in topological order, by the time node *X* is
generated, every node *X* links to already has records in the registry. Each
child link picks a random existing parent `submitter_id`, so the foreign-key
graph is always closed. Links to the project use the project `code`; required
links to non-generated nodes (e.g. `program`) get a synthesized reference so the
output still validates.

## Property emission rules

For each node, the emitted keys are the resolved `properties` minus the node's
`systemProperties`. This matters because the schema sets
`additionalProperties: false`, so emitting a server-assigned field (`id`,
`state`, `project_id`, `created_datetime`, `updated_datetime`) or any undeclared
key would fail validation. `type` and `submitter_id` survive (they come from the
ubiquitous-properties `$ref`, not `systemProperties`).

## The ValueProvider interface

```python
class ValueProvider(ABC):
    def value(self, req: ValueRequest) -> Any: ...
    def warmup(self, requests: Iterable[ValueRequest]) -> None: ...  # optional
```

A `ValueRequest` carries everything needed to produce one value: node, property
name, description, JSON type, enum, numeric bounds, regex `pattern`, `format`,
and (for arrays) a nested `item_request`. The generator builds one per property
and never inspects the strategy — so providers are fully interchangeable.

### `RandomValueProvider` (v1)

Schema-driven random values, all flowing through one seeded `random.Random` for
reproducibility:

- enum → random allowed value
- integer / number → bounded random (respects `minimum`/`maximum`)
- boolean → random
- string → if a `pattern` is set, a matching string via `rstr` (bound to the
  same RNG); otherwise a readable two-word token
- array → `[]`, or `array_size` sampled elements

### `LLMValueProvider`

Realistic, semantically-constrained values from a lightweight model. Every field
is routed by `field_kind` (`providers/classify.py`) into one of four paths:

| kind | when | how it's generated |
|------|------|--------------------|
| `numeric` | integer/number, not enum | `rng.gauss(mean, stddev)` clamped to `[max(schema_min, llm_min), min(schema_max, llm_max)]`, rounded for integers |
| `date` | `format: date`/`date-time`, or a `YYYY-MM-DD` pattern, or a `*_date` name | a **real calendar date** uniformly within `[earliest, latest]` (`providers/dates.py`), rendered to the field's pattern and `re.fullmatch`-verified |
| `text` | unconstrained string (no pattern/enum) | `rng.choice` of an LLM-supplied example pool |
| `other` | enums, booleans, arrays, pattern-constrained strings | delegated to `RandomValueProvider` |

**Flow:**
1. **`warmup(requests)`** (called by the generator before record generation):
   filter to `numeric`/`date`/`text` fields, drop those already cached, and ask
   the `SpecSource` for the rest in batched structured-output calls. Merge into
   the cache and persist. The generator yields one request per `(node, property)`
   via `iter_value_requests()`.
2. **`value(req)`** reads the cached `FieldSpec` and takes the matching path
   above; anything uncached falls back to `RandomValueProvider`. All randomness
   flows through the shared seeded `rng`, so output is reproducible after warmup.

**`SpecSource` (the injection seam, `providers/specs.py`):**
- `AnthropicSpecSource` — calls the Anthropic API with `messages.parse()` +
  a Pydantic `SpecTable` schema (structured output), chunking ~20 fields/call.
  The model is **required** (`--llm-model`); the client is injectable for tests.
- A fake source is used in tests so the whole provider runs offline.

**Key loading (`config.py`):** `.env` holds `LLM_API_KEY_FILE`, a *path* to a
key file (never the key itself). `load_api_key()` resolves the path and reads
the key; `.env` is gitignored.

**Cache format** (`.cache/distributions.json`, keyed `node/name`):

```json
{
  "demographic/month_birth":   {"kind": "numeric", "mean": 6.5, "stddev": 3.4, "minimum": 1, "maximum": 12, "unit": "month"},
  "demographic/baseline_date": {"kind": "date", "earliest": "1990-01-01", "latest": "2020-12-31"},
  "lipidomics_assay/assay_description": {"kind": "text", "examples": ["LC-MS/MS lipidomics of plasma", "Shotgun lipidomics, negative ion mode"]}
}
```

Because the contract is just `value()` + `warmup()`, the generator, writers, and
validation are unchanged between providers.

## Known schema quirks handled

- **`core_metadata_collection` ordering** — generated before the file nodes that
  link to it, not last.
- **`program`** — not submittable, never generated; project's required
  `programs` link is synthesized.
- **Regex-constrained strings** — date/ontology/ORCID/DOI fields are plain
  strings with a `pattern`; satisfied via `rstr`.
- **Non-nullable enums** — never nulled (the random provider only nulls optional,
  non-enum scalars, and only when `null_rate > 0`).

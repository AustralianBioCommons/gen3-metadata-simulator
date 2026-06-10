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

### `LLMValueProvider` (v2 — planned)

The headline feature. Numeric clinical values should be *plausible*, not merely
in-bounds. Design:

1. **`warmup(requests)`** runs once before generation. It collects the distinct
   numeric variables `(node, name, description)` and asks a lightweight model
   (e.g. Claude Haiku) for each variable's population mean, standard deviation,
   and unit. Results are cached to `<cache_dir>/distributions.json` so repeat
   runs make zero model calls.
2. **`value(req)`** then samples numeric properties from
   `rng.gauss(mean, stddev)`, clamped to `[minimum, maximum]`. Categorical,
   string, boolean, and array properties defer to a composed
   `RandomValueProvider` — only the numeric path uses the model.

Cache format:

```json
{
  "demographic/bmi_baseline": {"mean": 27.5, "stddev": 5.1, "unit": "kg/m^2"},
  "blood_pressure_test/systolic": {"mean": 120, "stddev": 15, "unit": "mmHg"}
}
```

Because the contract is just `value()` + `warmup()`, dropping the LLM provider in
requires no change to the generator, writers, or validation.

## Known schema quirks handled

- **`core_metadata_collection` ordering** — generated before the file nodes that
  link to it, not last.
- **`program`** — not submittable, never generated; project's required
  `programs` link is synthesized.
- **Regex-constrained strings** — date/ontology/ORCID/DOI fields are plain
  strings with a `pattern`; satisfied via `rstr`.
- **Non-nullable enums** — never nulled (the random provider only nulls optional,
  non-enum scalars, and only when `null_rate > 0`).

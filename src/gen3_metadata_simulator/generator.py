"""Generate linked, schema-valid Gen3 metadata records.

Flow:
  1. Resolve generation order (parents before children) via ``ordering``.
  2. Generate the single ``project`` record first.
  3. For every other node, generate ``num_records`` records. Each record:
       * carries ``type`` and a unique ``submitter_id``
       * fills every link with a real parent reference (``{submitter_id}`` or,
         for links to project, ``{code}``)
       * fills every remaining declared property via the ValueProvider
       * emits only declared, non-system properties (``additionalProperties:
         false`` in the schema means extra keys would fail validation)
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from typing import Any

from gen3_metadata_simulator.links import LinkSpec, extract_links
from gen3_metadata_simulator.ordering import generation_order
from gen3_metadata_simulator.providers.base import ValueProvider, ValueRequest
from gen3_metadata_simulator.registry import GeneratedRecordRegistry
from gen3_metadata_simulator.schema import SchemaLoader

logger = logging.getLogger(__name__)

# Properties that carry semantic meaning but are not links or system fields and
# must always be present.
_TYPE_KEY = "type"
_SUBMITTER_ID_KEY = "submitter_id"

PROJECT_NODE = "project"


class MetadataGenerator:
    """Build a complete set of linked metadata records from a resolved schema."""

    def __init__(
        self,
        loader: SchemaLoader,
        value_provider: ValueProvider,
        num_records: int = 30,
        project_code: str = "simulated_project",
        seed: int | None = None,
    ):
        self.loader = loader
        self.provider = value_provider
        self.num_records = num_records
        self.project_code = project_code
        # A synthetic program reference for the project's required ``programs``
        # link. Program nodes are administrative and never generated, but the
        # schema requires the link, so we emit a stable placeholder reference.
        self.program_name = "simulated_program"
        self.rng = random.Random(seed)
        self.registry = GeneratedRecordRegistry()
        self._generatable = set(loader.submittable_nodes())
        self.order = generation_order(loader.resolver, self._generatable)

    def generate(self) -> dict[str, Any]:
        """Generate all records.

        :return: Mapping of bare node name -> records. ``project`` maps to a
            single dict; every other node maps to a list of dicts.
        """
        result: dict[str, Any] = {}
        logger.info(
            "Generating metadata: %d node(s), %d record(s) each, provider=%s",
            len(self.order), self.num_records, type(self.provider).__name__,
        )

        # Let the value provider pre-compute anything it needs (the LLM provider
        # builds its distribution/limit/date/text table here; random ignores it).
        self.provider.warmup(self.iter_value_requests())

        # project: a single object using ``code`` rather than a submitter_id.
        project = self._make_project()
        result[PROJECT_NODE] = project

        for node in self.order:
            if node == PROJECT_NODE:
                continue
            records = [self._make_record(node) for _ in range(self.num_records)]
            result[node] = records
            logger.debug("Generated %d %s record(s)", len(records), node)
        return result

    # -- record factories ----------------------------------------------------

    def _make_project(self) -> dict:
        schema = self.loader.node_schema(PROJECT_NODE)
        emit_keys = self._emit_keys(schema)
        links = {ls.name: ls for ls in extract_links(schema)}

        # set_project must happen before resolving links so project_code is set.
        self.registry.set_project(self.project_code)

        record: dict[str, Any] = {_TYPE_KEY: PROJECT_NODE, "code": self.project_code}
        for key in emit_keys:
            if key in (_TYPE_KEY, "code"):
                continue
            if key in links:
                ref = self._resolve_link(links[key])
                if ref is not None:
                    record[key] = ref
                continue
            record[key] = self._value_for(PROJECT_NODE, key, schema)
        return record

    def _make_record(self, node: str) -> dict:
        schema = self.loader.node_schema(node)
        emit_keys = self._emit_keys(schema)
        links = {ls.name: ls for ls in extract_links(schema)}

        record: dict[str, Any] = {_TYPE_KEY: node}
        record[_SUBMITTER_ID_KEY] = self._make_submitter_id(node)

        for key in emit_keys:
            if key in (_TYPE_KEY, _SUBMITTER_ID_KEY):
                continue
            if key in links:
                ref = self._resolve_link(links[key])
                if ref is not None:
                    record[key] = ref
                continue
            record[key] = self._value_for(node, key, schema)

        self.registry.add(node, record)
        return record

    def iter_value_requests(self):
        """Yield one ValueRequest per (node, data property) across all nodes.

        One request per (node, property) is enough for the provider's warmup —
        every record of a node shares the same property schema. Links, ``type``
        and ``submitter_id`` are excluded since they are not provider-generated.
        """
        for node in self.order:
            schema = self.loader.node_schema(node)
            link_names = {ls.name for ls in extract_links(schema)}
            for key in self._emit_keys(schema):
                if key in (_TYPE_KEY, _SUBMITTER_ID_KEY, "code") or key in link_names:
                    continue
                yield _build_request(node, key, schema["properties"][key], schema)

    # -- helpers -------------------------------------------------------------

    def _resolve_link(self, link: LinkSpec) -> dict | None:
        """Return the nested reference object for a link, or None to omit it.

        * Links to project use ``{"code": ...}``.
        * Links to another generated node use ``{"submitter_id": ...}`` pointing
          at a real parent record.
        * Links to a non-generated target (e.g. program) are emitted with a
          synthesized reference when the link is required (so output stays
          schema-valid), and omitted otherwise.
        """
        if link.target_type == PROJECT_NODE:
            return {"code": self.registry.project_code()}
        if link.target_type not in self._generatable:
            if not link.required:
                return None
            ref = self.program_name if link.target_type == "program" else f"{link.target_type}_simulated"
            return {_SUBMITTER_ID_KEY: ref}
        parent_id = self.registry.random_parent_submitter_id(link.target_type, self.rng)
        return {_SUBMITTER_ID_KEY: parent_id}

    def _emit_keys(self, schema: dict) -> list[str]:
        """Declared property keys minus system properties, in schema order."""
        system = set(schema.get("systemProperties", []))
        props = schema.get("properties", {})
        return [k for k in props if k != "$ref" and k not in system]

    def _make_submitter_id(self, node: str) -> str:
        from gen3_metadata_simulator.providers.random_provider import _WORDS

        return f"{node}_{self.rng.choice(_WORDS)}_{self.rng.choice(_WORDS)}"

    def _value_for(self, node: str, name: str, schema: dict) -> Any:
        prop = schema["properties"][name]
        req = _build_request(node, name, prop, schema)
        return self.provider.value(req)


def _build_request(node: str, name: str, prop: dict, node_schema: dict) -> ValueRequest:
    """Translate a resolved property schema into a ValueRequest."""
    required = name in node_schema.get("required", [])
    json_type = _json_type(prop)
    enum = _enum_of(prop)

    item_request = None
    if json_type == "array":
        items = prop.get("items", {})
        item_request = ValueRequest(
            node=node,
            name=name,
            description=prop.get("description"),
            json_type=_json_type(items),
            enum=_enum_of(items),
            pattern=items.get("pattern"),
            fmt=items.get("format"),
        )

    return ValueRequest(
        node=node,
        name=name,
        description=prop.get("description"),
        json_type=json_type,
        enum=enum,
        item_request=item_request,
        fmt=prop.get("format"),
        pattern=prop.get("pattern"),
        minimum=prop.get("minimum"),
        maximum=prop.get("maximum"),
        required=required,
        fingerprint=_fingerprint(prop),
    )


def _fingerprint(prop: dict) -> str:
    """md5 of a field's resolved JSON schema.

    Any change to the property's schema (type, enum, pattern, bounds,
    description) flips this hash, so the LLM cache knows to re-estimate just that
    field. ``default=str`` keeps it robust to non-JSON-native values.
    """
    canonical = json.dumps(prop, sort_keys=True, default=str)
    return hashlib.md5(canonical.encode()).hexdigest()


def _json_type(prop: dict) -> str | None:
    """Return a single JSON type for a property schema.

    Handles ``type`` given as a list (e.g. ``["string", "null"]``) by picking
    the first non-null entry, and infers ``enum`` -> the type of its members.
    """
    t = prop.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        return non_null[0] if non_null else None
    if t is None and "enum" in prop:
        return "string"
    return t


def _enum_of(prop: dict) -> list | None:
    enum = prop.get("enum")
    return list(enum) if enum else None

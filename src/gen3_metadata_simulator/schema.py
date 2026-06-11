"""Load, resolve, and validate a bundled Gen3 JSON schema.

`SchemaLoader` wraps `gen3_validator.ResolveSchema` (which inlines every
``$ref`` in the dictionary) and exposes the handful of views the generator
needs: the resolved per-node schemas, the set of nodes that can actually be
generated, and the underlying `DataDictionary` used for topological ordering.
"""

from __future__ import annotations

import logging

from gen3_validator import ResolveSchema

from gen3_metadata_simulator.errors import InvalidGen3SchemaError

logger = logging.getLogger(__name__)

# Schema keys that are not generatable nodes. ``program`` is not submittable in
# Gen3 (it sits above ``project`` and is created administratively), so the
# simulator never emits records for it.
NON_NODE_KEYS = {
    "_definitions.yaml",
    "_terms.yaml",
    "_settings.yaml",
    "program.yaml",
    "root.yaml",
    "metaschema.yaml",
}


class SchemaLoader:
    """Resolve a bundled Gen3 schema and expose the views the generator needs."""

    def __init__(self, schema_path: str):
        self.schema_path = schema_path
        self._resolver: ResolveSchema | None = None
        self.raw: dict = {}
        self.resolved: dict = {}

    def load(self) -> "SchemaLoader":
        """Read the schema file and resolve all ``$ref`` references.

        After this call ``self.resolved`` is a dict keyed ``"<node>.yaml"`` whose
        values are fully-resolved node schemas — exactly the shape that
        ``gen3_validator.validate.validate_list_dict`` expects as its schema
        argument.
        """
        resolver = ResolveSchema(schema_path=self.schema_path)
        try:
            resolver.resolve_schema()
        except Exception as exc:  # noqa: BLE001 - re-raise as a typed error
            raise InvalidGen3SchemaError(
                f"Could not resolve schema {self.schema_path!r}: {exc}"
            ) from exc
        self._resolver = resolver
        self.raw = resolver.schema
        self.resolved = resolver.schema_resolved
        logger.info("Loaded schema %s: %d node(s) resolved, %d submittable",
                    self.schema_path, len(self.resolved), len(self.submittable_nodes()))
        return self

    @property
    def resolver(self) -> ResolveSchema:
        if self._resolver is None:
            raise InvalidGen3SchemaError("SchemaLoader.load() must be called first")
        return self._resolver

    def validate_is_gen3_schema(self) -> None:
        """Confirm the input is a structurally valid Gen3 schema.

        The strongest signal is that ``resolve_schema()`` succeeded (every
        ``$ref`` resolved). On top of that we assert the bundle carries the
        structural markers of a Gen3 dictionary: ``_definitions.yaml``,
        ``_settings.yaml`` with a readable dictionary version, and at least one
        submittable node. Raises :class:`InvalidGen3SchemaError` otherwise.
        """
        missing = [k for k in ("_definitions.yaml", "_settings.yaml") if k not in self.raw]
        if missing:
            raise InvalidGen3SchemaError(
                f"Schema is missing required section(s): {', '.join(missing)}"
            )
        try:
            self.resolver.get_schema_version()
        except Exception as exc:  # noqa: BLE001
            raise InvalidGen3SchemaError(
                f"Schema has no readable _settings.yaml/_dict_version: {exc}"
            ) from exc
        if not self.submittable_nodes():
            raise InvalidGen3SchemaError("Schema contains no submittable nodes to generate")

    def submittable_nodes(self) -> list[str]:
        """Return generatable node names (bare ids, no ``.yaml``), schema order.

        A node is generatable if it is not a structural/utility key, declares
        ``properties``, and is not explicitly ``submittable: false`` (e.g.
        ``program``).
        """
        nodes = []
        for key, node in self.raw.items():
            if key in NON_NODE_KEYS:
                continue
            if not isinstance(node, dict) or "properties" not in node:
                continue
            if node.get("submittable") is False:
                continue
            nodes.append(self._bare(key))
        return nodes

    def node_schema(self, node: str) -> dict:
        """Return the fully-resolved schema for ``node`` (with or without .yaml)."""
        key = node if node.endswith(".yaml") else f"{node}.yaml"
        schema = self.resolved.get(key)
        if schema is None:
            raise InvalidGen3SchemaError(f"No resolved schema for node {node!r}")
        return schema

    @staticmethod
    def _bare(key: str) -> str:
        return key[:-5] if key.endswith(".yaml") else key

"""Track generated records so child links can reference real parents.

Because nodes are generated in topological order, by the time a child node is
generated every parent node it links to already has records in the registry.
The child picks a random existing parent ``submitter_id`` (or the project
``code``), guaranteeing referential integrity in the output.
"""

from __future__ import annotations

import random

from gen3_metadata_simulator.errors import MissingParentError


class GeneratedRecordRegistry:
    """In-memory store of generated records keyed by bare node name."""

    def __init__(self):
        self._by_node: dict[str, list[dict]] = {}
        self._project_code: str | None = None

    def set_project(self, code: str) -> None:
        self._project_code = code

    def project_code(self) -> str:
        if self._project_code is None:
            raise MissingParentError("project has not been generated yet")
        return self._project_code

    def add(self, node: str, record: dict) -> None:
        self._by_node.setdefault(node, []).append(record)

    def submitter_ids(self, node: str) -> list[str]:
        return [r["submitter_id"] for r in self._by_node.get(node, []) if "submitter_id" in r]

    def random_parent_submitter_id(self, node: str, rng: random.Random) -> str:
        """Return a random ``submitter_id`` from already-generated ``node`` records."""
        ids = self.submitter_ids(node)
        if not ids:
            raise MissingParentError(
                f"link target {node!r} has no generated records; "
                "node order is likely wrong"
            )
        return rng.choice(ids)

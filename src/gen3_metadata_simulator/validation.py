"""Self-validate generated metadata against the resolved schema.

Wraps ``gen3_validator.validate.validate_list_dict``, which validates each
record against its node schema (looked up by the record's ``type``) using a
Draft-4 validator and returns a list of failure dicts (empty == all valid).
"""

from __future__ import annotations

from typing import Any

from gen3_validator import validate

from gen3_metadata_simulator.generator import PROJECT_NODE


def flatten_records(data: dict[str, Any]) -> list[dict]:
    """Flatten the generator's node->record(s) mapping into one record list.

    ``project`` is a single object; all other nodes are lists. Every record
    already carries a ``type`` key, which ``validate_list_dict`` requires.
    """
    records: list[dict] = []
    for node, value in data.items():
        if node == PROJECT_NODE:
            records.append(value)
        else:
            records.extend(value)
    return records


def self_validate(data: dict[str, Any], resolved_schema: dict) -> list[dict]:
    """Validate all generated records; return the list of failures (empty=pass)."""
    return validate.validate_list_dict(flatten_records(data), resolved_schema)


def summarize_failures(failures: list[dict]) -> str:
    """Render a compact, grouped summary of validation failures."""
    if not failures:
        return "0 validation errors"
    by_node: dict[str, int] = {}
    for f in failures:
        by_node[f.get("node", "?")] = by_node.get(f.get("node", "?"), 0) + 1
    lines = [f"{len(failures)} validation error(s):"]
    for node, count in sorted(by_node.items()):
        lines.append(f"  {node}: {count}")
    # Show a few concrete examples to aid debugging.
    for f in failures[:5]:
        lines.append(
            f"  - [{f.get('node')}#{f.get('index')}] "
            f"{f.get('invalid_key')}: {f.get('validation_error')}"
        )
    return "\n".join(lines)

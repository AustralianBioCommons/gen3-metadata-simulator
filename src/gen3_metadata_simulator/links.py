"""Extract link relationships from a resolved Gen3 node schema.

A Gen3 ``links`` block is a list whose entries are either a single link or a
``subgroup`` (a set of related links, optionally ``exclusive``). For metadata
generation we flatten subgroups and emit every member as its own foreign-key
field — matching how the example metadata renders file nodes that link to both
an assay and a core_metadata_collection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkSpec:
    """A single foreign-key relationship from a child node to a parent node.

    :param name: The property key the link is emitted under (e.g. ``samples``).
    :param target_type: The parent node's bare name (e.g. ``sample``).
    :param multiplicity: one_to_one / one_to_many / many_to_one / many_to_many.
    :param required: Whether the link is required by the schema.
    """

    name: str
    target_type: str
    multiplicity: str
    required: bool


def extract_links(node_schema: dict) -> list[LinkSpec]:
    """Return all links for a node, with subgroups flattened to their members."""
    specs: list[LinkSpec] = []
    for entry in node_schema.get("links", []):
        if "subgroup" in entry:
            for member in entry["subgroup"]:
                specs.append(_to_spec(member))
        else:
            specs.append(_to_spec(entry))
    return specs


def _to_spec(link: dict) -> LinkSpec:
    return LinkSpec(
        name=link["name"],
        target_type=link["target_type"],
        multiplicity=link.get("multiplicity", "many_to_one"),
        required=bool(link.get("required", False)),
    )

"""Tests for topological generation order.

Correct ordering is the linchpin of referential integrity: if a child node is
generated before its parent, the child cannot reference a real parent record.
"""

from gen3_metadata_simulator.ordering import generation_order


def test_program_is_excluded_but_data_release_included(loader):
    """Order excludes the non-submittable 'program' yet keeps submittable nodes.

    'program' is not a generatable node, so it must be absent. 'data_release' is
    submittable in this dictionary, so — unlike the hand-made example metadata —
    we do generate it.
    """
    generatable = set(loader.submittable_nodes())
    order = generation_order(loader.resolver, generatable)
    assert "program" not in order
    assert "data_release" in order
    assert order[0] == "project"


def test_every_link_target_precedes_its_node(loader):
    """For every node, all link targets appear earlier in the order.

    This is the property that makes referential integrity possible: by the time
    we generate node X, every node X links to already has records. We assert it
    directly from the schema's link edges (with core_metadata_collection NOT
    forced last, which is the bug we avoid from the validator's get_node_order).
    """
    from gen3_metadata_simulator.links import extract_links

    generatable = set(loader.submittable_nodes())
    order = generation_order(loader.resolver, generatable)
    position = {node: i for i, node in enumerate(order)}

    for node in order:
        schema = loader.node_schema(node)
        for link in extract_links(schema):
            target = link.target_type
            if target in position:  # skip non-generated targets like 'program'
                assert position[target] < position[node], (
                    f"{node} generated before its link target {target}"
                )


def test_order_is_deterministic(loader):
    """The same schema yields the same order every call (stable tie-breaking)."""
    generatable = set(loader.submittable_nodes())
    first = generation_order(loader.resolver, generatable)
    second = generation_order(loader.resolver, generatable)
    assert first == second

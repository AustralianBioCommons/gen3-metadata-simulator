"""Resolve the order in which nodes must be generated (and imported).

Gen3 dictionaries are directed graphs: a child node links to its parent(s), so
parents must exist before children. We derive the link edges from
`gen3_validator.DataDictionary` and run a Kahn topological sort over them.

We deliberately do *not* use ``DataDictionary.get_node_order`` directly: it
force-moves ``core_metadata_collection`` to the very end, which is wrong for
generation because file nodes link *to* core_metadata_collection and so must be
generated after it. Computing the sort ourselves keeps parents strictly before
children for every node, including core_metadata_collection.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque

from gen3_validator import DataDictionary

logger = logging.getLogger(__name__)


def generation_order(dd: DataDictionary, generatable: set[str]) -> list[str]:
    """Return generatable nodes in dependency order (parents before children).

    :param dd: A loaded ``DataDictionary`` (schema already parsed).
    :param generatable: Bare node names (no ``.yaml``) the simulator will emit.
    :return: Ordered list of bare node names; every link target precedes the
        node that links to it. Nodes with no links still appear (sourced from
        ``generatable``). Order within a dependency tier is deterministic.
    """
    if dd.nodes is None:
        dd.parse_schema()
    edges = dd.get_all_node_pairs()

    # Build the dependency graph restricted to generatable nodes. An edge
    # (parent, child) means "parent must come before child".
    graph: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n: 0 for n in generatable}
    for parent, child in edges:
        if parent not in generatable or child not in generatable:
            continue
        graph[parent].append(child)
        in_degree[child] += 1

    # Kahn's algorithm. Process zero-in-degree nodes in sorted order for
    # deterministic output across runs.
    queue = deque(sorted(n for n, d in in_degree.items() if d == 0))
    ordered: list[str] = []
    while queue:
        node = queue.popleft()
        ordered.append(node)
        for child in sorted(graph[node]):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
        # Re-sort to keep deterministic tie-breaking as new nodes free up.
        queue = deque(sorted(queue))

    # Any node left out (would only happen on a cycle) is appended so it is
    # still generated; referential integrity for its links may not hold.
    for node in sorted(generatable):
        if node not in ordered:
            ordered.append(node)
    logger.debug("Generation order (%d nodes): %s", len(ordered), ", ".join(ordered))
    return ordered

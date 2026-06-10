"""Write generated metadata to disk in the Gen3 submission layout.

Output directory contains one ``<node>.json`` per node plus a
``DataImportOrder.txt``. ``project.json`` is a single JSON object; every other
node file is a JSON array. Files are pretty-printed with 4-space indent and
sorted keys to match the reference metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gen3_metadata_simulator.generator import PROJECT_NODE

IMPORT_ORDER_FILE = "DataImportOrder.txt"


def write_outputs(data: dict[str, Any], order: list[str], output_dir: str | Path) -> Path:
    """Write per-node JSON files and DataImportOrder.txt.

    :param data: Mapping of node -> record(s) as returned by the generator.
    :param order: Generation/import order (bare node names, project first).
    :param output_dir: Destination directory (created if absent).
    :return: The output directory path.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for node, records in data.items():
        path = out / f"{node}.json"
        with path.open("w") as fh:
            json.dump(records, fh, indent=4, sort_keys=True)
            fh.write("\n")

    write_data_import_order(order, out)
    return out


def write_data_import_order(order: list[str], output_dir: str | Path) -> Path:
    """Write the import order as plain node names, one per line.

    Matches the reference format exactly: newline-separated bare names with a
    trailing newline, no numbering or tabs.
    """
    out = Path(output_dir)
    path = out / IMPORT_ORDER_FILE
    path.write_text("\n".join(order) + "\n")
    return path

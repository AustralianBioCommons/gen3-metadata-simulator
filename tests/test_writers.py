"""Tests for on-disk output format (file shapes + DataImportOrder.txt)."""

import json

from gen3_metadata_simulator.writers import IMPORT_ORDER_FILE, write_outputs


def test_project_file_is_object_others_are_arrays(generator, tmp_path):
    """project.json is a JSON object; every other node file is a JSON array of N.

    This mirrors the Gen3 submission layout exactly: one project object, and a
    list of records per child node.
    """
    data = generator.generate()
    write_outputs(data, generator.order, tmp_path)

    project = json.loads((tmp_path / "project.json").read_text())
    assert isinstance(project, dict)

    subjects = json.loads((tmp_path / "subject.json").read_text())
    assert isinstance(subjects, list)
    assert len(subjects) == generator.num_records


def test_data_import_order_is_plain_names(generator, tmp_path):
    """DataImportOrder.txt is newline-separated bare node names, project first.

    The Gen3 loader reads this file to sequence submission; it must be plain
    names (no numbering, no tabs) with parents before children.
    """
    data = generator.generate()
    write_outputs(data, generator.order, tmp_path)

    text = (tmp_path / IMPORT_ORDER_FILE).read_text()
    lines = text.splitlines()
    assert lines[0] == "project"
    assert "\t" not in text
    assert all(line.isidentifier() for line in lines)
    assert lines == generator.order

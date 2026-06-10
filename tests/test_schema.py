"""Tests for schema loading, resolution, and validity checks.

These guard the front door of the pipeline: if the schema does not load and
resolve correctly, nothing downstream can produce valid metadata.
"""

import pytest

from gen3_metadata_simulator.errors import InvalidGen3SchemaError
from gen3_metadata_simulator.schema import SchemaLoader


def test_load_resolves_node_schemas(loader):
    """A loaded schema exposes resolved, $ref-free node schemas keyed by '<node>.yaml'.

    Resolution must inline _definitions references so a node like 'demographic'
    carries concrete property definitions (e.g. the ubiquitous 'submitter_id'),
    which is what the validator later checks records against.
    """
    assert "demographic.yaml" in loader.resolved
    demographic = loader.resolved["demographic.yaml"]
    assert "submitter_id" in demographic["properties"]
    # No unresolved $ref should remain at the top of the properties block.
    assert "$ref" not in demographic["properties"]


def test_validate_is_gen3_schema_passes_for_real_dictionary(loader):
    """The example dictionary is a valid Gen3 schema and must pass the check."""
    loader.validate_is_gen3_schema()  # should not raise


def test_submittable_nodes_excludes_program_and_utility_keys(loader):
    """Generatable nodes exclude program (submittable: false) and utility files.

    'program' sits above 'project' administratively and is never submitted as a
    data record, so it must not appear in the generatable set; neither should
    the _definitions/_settings/_terms helper sections.
    """
    nodes = set(loader.submittable_nodes())
    assert "project" in nodes
    assert "demographic" in nodes
    assert "program" not in nodes
    assert "_definitions" not in nodes
    assert "_settings" not in nodes


def test_load_failure_raises_typed_error(tmp_path):
    """A non-schema JSON file surfaces as InvalidGen3SchemaError, not a raw crash.

    Callers (the CLI) rely on this typed error to print a clean message and exit
    non-zero rather than dumping a traceback.
    """
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a gen3 schema"}')
    with pytest.raises(InvalidGen3SchemaError):
        SchemaLoader(str(bad)).load().validate_is_gen3_schema()

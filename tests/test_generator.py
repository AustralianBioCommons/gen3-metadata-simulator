"""Tests for record generation: shape, link integrity, and determinism."""

from gen3_metadata_simulator.generator import MetadataGenerator, _build_request
from gen3_metadata_simulator.providers.random_provider import RandomValueProvider


def test_build_request_fingerprint_is_stable_and_change_sensitive():
    """A field's fingerprint is identical for the same schema and differs when it changes.

    This md5 is what lets the LLM cache re-estimate only the fields a schema edit
    actually touched: identical property schemas must hash the same, and changing
    the property's type must change the hash.
    """
    node_schema = {"required": []}
    prop_v1 = {"type": "integer", "description": "month of birth"}
    prop_v1_again = {"description": "month of birth", "type": "integer"}  # key order differs
    prop_v2 = {"type": "string", "description": "month of birth"}  # type changed

    fp1 = _build_request("demographic", "month_birth", prop_v1, node_schema).fingerprint
    fp1_again = _build_request("demographic", "month_birth", prop_v1_again, node_schema).fingerprint
    fp2 = _build_request("demographic", "month_birth", prop_v2, node_schema).fingerprint

    assert fp1 == fp1_again  # order-independent, stable
    assert fp1 != fp2        # a type change is detected


def test_project_is_single_object_with_code(generator):
    """The project node is emitted as one object keyed by 'code', not a list.

    Gen3 submits a single project per dataset, identified by 'code'; child nodes
    reference it by that code rather than a submitter_id.
    """
    data = generator.generate()
    project = data["project"]
    assert isinstance(project, dict)
    assert project["type"] == "project"
    assert project["code"] == "TestProject"
    assert "submitter_id" not in project


def test_records_have_type_submitter_id_and_no_system_props(generator):
    """Every non-project record carries 'type' + 'submitter_id' and no system fields.

    System properties (id, state, project_id, created_datetime, updated_datetime)
    are server-assigned; the schema sets additionalProperties:false, so emitting
    them — or any undeclared key — would fail validation.
    """
    data = generator.generate()
    demo_schema = generator.loader.node_schema("demographic")
    system = set(demo_schema.get("systemProperties", []))
    declared = set(demo_schema["properties"]) | {"type"}

    for record in data["demographic"]:
        assert record["type"] == "demographic"
        assert record["submitter_id"].startswith("demographic_")
        assert system.isdisjoint(record.keys())
        # No key outside the declared property set (additionalProperties guard).
        assert set(record.keys()) <= declared


def test_links_reference_existing_parents(generator):
    """Every foreign key points at a parent record that was actually generated.

    This is the core integrity guarantee: a child's link submitter_id must exist
    among the parent node's generated submitter_ids.
    """
    data = generator.generate()

    samples = {r["submitter_id"] for r in data["sample"]}
    for assay in data["lipidomics_assay"]:
        ref = assay["samples"]["submitter_id"]
        assert ref in samples


def test_enum_values_are_in_domain(generator):
    """Generated enum properties only ever contain allowed values.

    demographic.sex is an enum; sampling must stay within the schema's allowed
    set so the record validates.
    """
    data = generator.generate()
    sex_enum = set(
        generator.loader.node_schema("demographic")["properties"]["sex"]["enum"]
    )
    for record in data["demographic"]:
        if "sex" in record:
            assert record["sex"] in sex_enum


def test_same_seed_produces_identical_output(loader):
    """Two generators with the same seed produce byte-identical records.

    Reproducibility lets users regenerate an exact dataset from a seed, which is
    essential for debugging and for stable test fixtures.
    """
    import random

    def build():
        return MetadataGenerator(
            loader=loader,
            value_provider=RandomValueProvider(random.Random(7)),
            num_records=3,
            project_code="P",
            seed=7,
        ).generate()

    assert build() == build()

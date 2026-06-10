"""Tests for link extraction, including subgroup flattening.

Gen3 file nodes link to several parents at once via 'subgroup' blocks. If we do
not flatten those, file records would miss foreign keys and break the graph.
"""

from gen3_metadata_simulator.links import extract_links


def test_subgroup_links_are_flattened(loader):
    """qc_file's single subgroup of 5 links is flattened into 5 LinkSpecs.

    In the schema, qc_file declares one 'links' entry whose 'subgroup' holds
    five member links (genomics_file, core_metadata_collection, lipidomics_file,
    proteomics_file, metabolomics_file). Each member must become its own
    emittable foreign key.
    """
    specs = extract_links(loader.node_schema("qc_file"))
    names = {s.name for s in specs}
    assert names == {
        "genomics_files",
        "core_metadata_collections",
        "lipidomics_files",
        "proteomics_files",
        "metabolomics_files",
    }


def test_simple_link_target_and_multiplicity(loader):
    """clinical_descriptor links to subject with the schema's multiplicity.

    A plain (non-subgroup) link is read straight through, preserving its target
    node and multiplicity so downstream code can reference the right parent.
    """
    specs = extract_links(loader.node_schema("clinical_descriptor"))
    by_name = {s.name: s for s in specs}
    assert "subjects" in by_name
    assert by_name["subjects"].target_type == "subject"
    assert by_name["subjects"].multiplicity == "many_to_one"


def test_project_links_to_program(loader):
    """project declares a required 'programs' link to the program node.

    The generator handles this specially (program is never generated), but the
    link itself must be extracted so that handling can occur.
    """
    specs = extract_links(loader.node_schema("project"))
    by_name = {s.name: s for s in specs}
    assert "programs" in by_name
    assert by_name["programs"].target_type == "program"
    assert by_name["programs"].required is True

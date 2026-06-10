"""Shared pytest fixtures.

The bundled example schema is the single source of truth for every test: it is a
real, complete Gen3 dictionary, so exercising the simulator against it is the
most faithful check we can run short of a live Gen3 deployment.
"""

from pathlib import Path

import pytest

from gen3_metadata_simulator.generator import MetadataGenerator
from gen3_metadata_simulator.providers.random_provider import RandomValueProvider
from gen3_metadata_simulator.schema import SchemaLoader

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_SCHEMA = REPO_ROOT / "examples" / "jsonschema" / "acdc_schema_v1.1.5.json"


@pytest.fixture(scope="session")
def schema_path() -> str:
    return str(EXAMPLE_SCHEMA)


@pytest.fixture
def loader(schema_path) -> SchemaLoader:
    """A loaded, resolved SchemaLoader for the example dictionary."""
    return SchemaLoader(schema_path).load()


@pytest.fixture
def generator(loader) -> MetadataGenerator:
    """A small, deterministic generator (seed=42, 5 records per node)."""
    import random

    provider = RandomValueProvider(random.Random(42))
    return MetadataGenerator(
        loader=loader,
        value_provider=provider,
        num_records=5,
        project_code="TestProject",
        seed=42,
    )

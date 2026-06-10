"""The critical end-to-end test: generate, then validate with gen3_validator.

If this passes, the simulator's promise holds — the metadata it produces
conforms to the input schema and is internally consistent. It is the same check
a user would run with the gen3_validator tool against a real Gen3 deployment.
"""

import random

import pytest

from gen3_metadata_simulator.generator import MetadataGenerator
from gen3_metadata_simulator.providers.random_provider import RandomValueProvider
from gen3_metadata_simulator.validation import self_validate


@pytest.mark.parametrize("num_records", [1, 5, 30])
def test_generated_metadata_validates_against_schema(loader, num_records):
    """Generated metadata produces zero validation errors for several dataset sizes.

    We sweep record counts because edge cases differ: N=1 stresses single-parent
    linking, while N=30 matches the reference dataset size. In all cases
    validate_list_dict (Draft-4 validation per node 'type') must report no
    failures.
    """
    provider = RandomValueProvider(random.Random(123))
    generator = MetadataGenerator(
        loader=loader,
        value_provider=provider,
        num_records=num_records,
        project_code="RoundTrip",
        seed=123,
    )
    data = generator.generate()
    failures = self_validate(data, loader.resolved)
    assert failures == [], failures[:5]

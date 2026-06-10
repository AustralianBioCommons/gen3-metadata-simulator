"""End-to-end: LLM-provider output must still validate against the schema.

The realism features (distributions, real dates, free text) are worthless if the
generated metadata stops validating. This drives the full generator with an
LLMValueProvider backed by a FakeSpecSource (no network) and asserts
gen3_validator reports zero errors — the same guarantee the random provider
gives, now with semantically-constrained values.
"""

import random

import pytest

from gen3_metadata_simulator.generator import MetadataGenerator
from gen3_metadata_simulator.providers.classify import field_kind
from gen3_metadata_simulator.providers.llm_provider import LLMValueProvider
from gen3_metadata_simulator.providers.specs import FieldSpec, SpecSource, spec_key
from gen3_metadata_simulator.validation import self_validate


class CannedSpecSource(SpecSource):
    """Returns a plausible spec for every requested field, by kind.

    Mirrors what a real LLM would return — numeric distributions with limits,
    date windows, and small text pools — so the round-trip exercises all three
    realistic paths offline.
    """

    def estimate(self, requests, text_pool_size):
        out = {}
        for req in requests:
            kind = field_kind(req)
            if kind == "numeric":
                out[spec_key(req)] = FieldSpec(kind="numeric", mean=10.0, stddev=3.0,
                                               minimum=0.0, maximum=100.0, unit="x")
            elif kind == "date":
                out[spec_key(req)] = FieldSpec(kind="date", earliest="1990-01-01",
                                               latest="2020-12-31")
            elif kind == "text":
                out[spec_key(req)] = FieldSpec(
                    kind="text",
                    examples=tuple(f"realistic {req.name} example {i}" for i in range(text_pool_size)),
                )
        return out


@pytest.mark.parametrize("num_records", [1, 5])
def test_llm_generated_metadata_validates(loader, tmp_path, num_records):
    """Generating with the LLM provider yields zero validation errors.

    Sweeping N=1 and N=5 covers single-parent linking and the normal case. The
    provider draws numbers from distributions, real dates, and pooled text, yet
    every record must still pass validate_list_dict.
    """
    provider = LLMValueProvider(
        random.Random(99),
        CannedSpecSource(),
        cache_path=str(tmp_path / "specs.json"),
        text_pool_size=num_records,
    )
    generator = MetadataGenerator(
        loader=loader,
        value_provider=provider,
        num_records=num_records,
        project_code="LLMRoundTrip",
        seed=99,
    )
    data = generator.generate()
    failures = self_validate(data, loader.resolved)
    assert failures == [], failures[:5]

"""Tests for LLMValueProvider value generation and the warmup pass.

These exercise the provider end to end with an injected FakeSpecSource — no
network. They pin the behaviours the user asked for: numeric values respect the
LLM's min/max limits, dates are real, free text comes from the LLM pool, and
everything else falls back to the v1 random path. warmup must query the source
only for fields not already cached.
"""

import datetime as dt
import random

from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.specs import FieldSpec, SpecCache, SpecSource, spec_key
from gen3_metadata_simulator.providers.llm_provider import LLMValueProvider


class FakeSpecSource(SpecSource):
    """A scripted spec source that returns preset specs and logs what it was asked.

    Lets tests assert which fields warmup actually queried (only the uncached
    ones) without any API call.
    """

    def __init__(self, specs):
        self._specs = specs
        self.requested_keys = []

    def estimate(self, requests, text_pool_size):
        out = {}
        for req in requests:
            self.requested_keys.append(spec_key(req))
            if spec_key(req) in self._specs:
                out[spec_key(req)] = self._specs[spec_key(req)]
        return out


def _provider(specs, tmp_path, seed=0):
    source = FakeSpecSource(specs)
    cache_path = str(tmp_path / "specs.json")
    return LLMValueProvider(random.Random(seed), source, cache_path=cache_path), source


def test_numeric_value_respects_llm_min_max(tmp_path):
    """month_birth bounded to [1,12] never produces 13+ and is an int.

    This is the headline fix: the LLM supplies semantic limits, and sampling
    clamps to them — so a 'month' is always a real month, unlike v1's any-int.
    """
    spec = FieldSpec(kind="numeric", mean=6.5, stddev=10.0, minimum=1, maximum=12, unit="month")
    provider, _ = _provider({"demographic/month_birth": spec}, tmp_path)
    req = ValueRequest(node="demographic", name="month_birth", json_type="integer")
    provider.warmup([req])

    for _ in range(300):
        v = provider.value(req)
        assert isinstance(v, int)
        assert 1 <= v <= 12


def test_date_value_is_real_and_in_range(tmp_path):
    """A date field yields a real calendar date inside the LLM's plausible window."""
    spec = FieldSpec(kind="date", earliest="1995-01-01", latest="2010-12-31")
    provider, _ = _provider({"demographic/baseline_date": spec}, tmp_path)
    req = ValueRequest(node="demographic", name="baseline_date", json_type="string",
                       pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    provider.warmup([req])

    parsed = dt.date.fromisoformat(provider.value(req))
    assert dt.date(1995, 1, 1) <= parsed <= dt.date(2010, 12, 31)


def test_text_value_comes_from_llm_pool(tmp_path):
    """A free-text field returns one of the LLM-supplied example strings.

    Proves descriptive fields get domain-appropriate prose from the model rather
    than random word salad.
    """
    pool = ("LC-MS/MS lipidomics profiling of plasma", "Shotgun lipidomics, negative ion mode")
    spec = FieldSpec(kind="text", examples=pool)
    provider, _ = _provider({"lipidomics_assay/assay_description": spec}, tmp_path)
    req = ValueRequest(node="lipidomics_assay", name="assay_description", json_type="string",
                       description="Description of the assay")
    provider.warmup([req])

    assert provider.value(req) in pool


def test_enum_delegates_to_random(tmp_path):
    """Enum fields are never sent to the LLM and stay within their allowed set.

    The provider must fall back to enum sampling for categorical fields.
    """
    provider, source = _provider({}, tmp_path)
    req = ValueRequest(node="demographic", name="sex", json_type="string",
                       enum=["male", "female"])
    provider.warmup([req])

    assert provider.value(req) in {"male", "female"}
    assert source.requested_keys == []  # enum was not queried


def test_warmup_only_queries_uncached_numeric_date_text(tmp_path):
    """warmup queries the source once for uncached LLM-kind fields, then never again.

    A second warmup against a warm cache makes zero source calls, and an enum
    field is never queried — proving the cache and kind filter both work.
    """
    specs = {
        "demographic/bmi_baseline": FieldSpec(kind="numeric", mean=27.0, stddev=5.0, minimum=12, maximum=60),
    }
    provider, source = _provider(specs, tmp_path)
    numeric = ValueRequest(node="demographic", name="bmi_baseline", json_type="number")
    enum = ValueRequest(node="demographic", name="sex", json_type="string", enum=["male", "female"])

    provider.warmup([numeric, enum])
    assert source.requested_keys == ["demographic/bmi_baseline"]

    # Reload from the now-warm cache: a fresh provider must not re-query.
    source2 = FakeSpecSource(specs)
    provider2 = LLMValueProvider(random.Random(0), source2,
                                 cache_path=str(tmp_path / "specs.json"))
    provider2.warmup([numeric, enum])
    assert source2.requested_keys == []


def test_same_seed_is_deterministic(tmp_path):
    """Identical seed + warm cache produce identical numeric draws (reproducible)."""
    spec = FieldSpec(kind="numeric", mean=100.0, stddev=15.0, minimum=0, maximum=200)
    req = ValueRequest(node="lab_result", name="value", json_type="number")
    p1, _ = _provider({"lab_result/value": spec}, tmp_path / "a", seed=7)
    p2, _ = _provider({"lab_result/value": spec}, tmp_path / "b", seed=7)
    p1.warmup([req]); p2.warmup([req])
    assert p1.value(req) == p2.value(req)

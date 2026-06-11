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

    def estimate(self, requests, text_pool_size, progress=None):
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


# --- fingerprint-driven cache invalidation ---------------------------------

_FP_SPECS = {
    "demographic/bmi_baseline": FieldSpec(kind="numeric", mean=27.0, stddev=5.0, minimum=12, maximum=60),
    "demographic/month_birth": FieldSpec(kind="numeric", mean=6.5, stddev=3.4, minimum=1, maximum=12),
}


def _numeric_req(name, fingerprint, json_type="number"):
    return ValueRequest(node="demographic", name=name, json_type=json_type, fingerprint=fingerprint)


def test_changed_fingerprint_requeries_only_that_field(tmp_path):
    """When one field's schema changes, only that field is re-estimated.

    First run caches both fields with their fingerprints. On the second run
    month_birth's fingerprint differs (its schema changed) while bmi_baseline's
    is unchanged — so the source is asked for month_birth alone, and the
    unchanged field is reused from cache.
    """
    cache_path = str(tmp_path / "specs.json")
    bmi = _numeric_req("bmi_baseline", "bmi-v1")
    month = _numeric_req("month_birth", "month-v1", json_type="integer")

    s1 = FakeSpecSource(_FP_SPECS)
    LLMValueProvider(random.Random(0), s1, cache_path=cache_path).warmup([bmi, month])
    assert set(s1.requested_keys) == {"demographic/bmi_baseline", "demographic/month_birth"}

    month_changed = _numeric_req("month_birth", "month-v2", json_type="integer")
    s2 = FakeSpecSource(_FP_SPECS)
    LLMValueProvider(random.Random(0), s2, cache_path=cache_path).warmup([bmi, month_changed])
    assert s2.requested_keys == ["demographic/month_birth"]


def test_new_field_is_queried(tmp_path):
    """A field absent from the cache (new key) is always estimated."""
    cache_path = str(tmp_path / "specs.json")
    s1 = FakeSpecSource(_FP_SPECS)
    LLMValueProvider(random.Random(0), s1, cache_path=cache_path).warmup([_numeric_req("bmi_baseline", "bmi-v1")])

    s2 = FakeSpecSource(_FP_SPECS)
    LLMValueProvider(random.Random(0), s2, cache_path=cache_path).warmup(
        [_numeric_req("bmi_baseline", "bmi-v1"), _numeric_req("month_birth", "month-v1", "integer")]
    )
    assert s2.requested_keys == ["demographic/month_birth"]  # bmi reused, month new


def test_force_refresh_requeries_everything(tmp_path):
    """--refresh-llm (force_refresh) re-estimates every field even when cached.

    Despite identical fingerprints to the cached run, force_refresh bypasses the
    cache match so both fields are re-queried — the escape hatch for getting
    fresh estimates on demand.
    """
    cache_path = str(tmp_path / "specs.json")
    reqs = [_numeric_req("bmi_baseline", "bmi-v1"), _numeric_req("month_birth", "month-v1", "integer")]
    LLMValueProvider(random.Random(0), FakeSpecSource(_FP_SPECS), cache_path=cache_path).warmup(reqs)

    s2 = FakeSpecSource(_FP_SPECS)
    LLMValueProvider(random.Random(0), s2, cache_path=cache_path, force_refresh=True).warmup(reqs)
    assert set(s2.requested_keys) == {"demographic/bmi_baseline", "demographic/month_birth"}

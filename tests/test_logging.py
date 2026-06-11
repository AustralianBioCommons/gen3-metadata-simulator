"""Tests for CLI logging configuration and the warmup observability log.

Logging is the tool's only window into what the LLM provider is doing — how many
fields were reused from cache vs re-estimated (i.e. what you're paying for). These
tests pin the verbosity mapping and that warmup emits a useful breakdown.
"""

import logging
import random

from gen3_metadata_simulator.cli import configure_logging
from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.llm_provider import LLMValueProvider
from gen3_metadata_simulator.providers.specs import FieldSpec, SpecSource, spec_key

PKG_LOGGER = "gen3_metadata_simulator"


def test_configure_logging_maps_flags_to_levels():
    """The verbosity flags map to the expected log levels.

    Default is quiet (WARNING); --verbose surfaces INFO milestones; --debug turns
    on DEBUG detail. This is what keeps normal runs uncluttered while making deep
    output available on demand.
    """
    configure_logging(verbose=False, debug=False)
    assert logging.getLogger(PKG_LOGGER).level == logging.WARNING
    configure_logging(verbose=True, debug=False)
    assert logging.getLogger(PKG_LOGGER).level == logging.INFO
    configure_logging(verbose=False, debug=True)
    assert logging.getLogger(PKG_LOGGER).level == logging.DEBUG


class _OneNumericSource(SpecSource):
    """Returns a numeric spec for whatever is asked (no network)."""

    def estimate(self, requests, text_pool_size, progress=None):
        return {spec_key(r): FieldSpec(kind="numeric", mean=27.0, stddev=5.0, minimum=12, maximum=60)
                for r in requests}


def test_warmup_logs_cache_breakdown(tmp_path, caplog):
    """warmup logs how many fields were considered, reused, and re-estimated.

    On a cold cache one numeric field must be reported as considered=1, reused=0,
    to-estimate=1 — the visibility a user needs to understand cache behaviour and
    API cost.
    """
    provider = LLMValueProvider(random.Random(0), _OneNumericSource(),
                                cache_path=str(tmp_path / "specs.json"))
    req = ValueRequest(node="demographic", name="bmi_baseline", json_type="number", fingerprint="v1")

    with caplog.at_level(logging.INFO, logger=PKG_LOGGER):
        provider.warmup([req])

    summary = "\n".join(caplog.messages)
    assert "1 field(s) considered" in summary
    assert "0 reused from cache" in summary
    assert "1 to estimate" in summary

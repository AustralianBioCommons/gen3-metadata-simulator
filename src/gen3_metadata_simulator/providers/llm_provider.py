"""LLM-backed value provider (v2 — interface defined, not yet implemented).

The headline feature of this project: instead of meaningless random numbers,
ask a lightweight model (e.g. Claude Haiku) for the *distribution* of each
numeric clinical variable, then sample realistic values from it.

Design
------
``warmup(requests)`` runs once before generation:
  1. Collect the distinct numeric variables (node, name, description).
  2. For each, prompt the model: "For the clinical variable '{name}'
     ({description}), give a plausible population mean and standard deviation
     with units." Parse a structured ``{mean, stddev, unit}`` response.
  3. Cache the table to ``<cache_dir>/distributions.json`` keyed by
     ``(node, name)`` so repeat runs make zero model calls.

``value(req)``:
  * numeric  -> ``rng.gauss(mean, stddev)`` clamped to [minimum, maximum].
  * enum/categorical -> defer to enum sampling (compose a RandomValueProvider).
  * string/array/boolean -> defer to the random provider.

Only the numeric path differs from v1; everything else is delegated, which is
why ``ValueProvider`` keeps a uniform ``value()`` contract.

The cache file format (``distributions.json``)::

    {
      "demographic/bmi_baseline": {"mean": 27.5, "stddev": 5.1, "unit": "kg/m^2"},
      "blood_pressure_test/systolic": {"mean": 120, "stddev": 15, "unit": "mmHg"}
    }
"""

from __future__ import annotations

import random
from typing import Any, Iterable

from gen3_metadata_simulator.providers.base import ValueProvider, ValueRequest
from gen3_metadata_simulator.providers.random_provider import RandomValueProvider


class LLMValueProvider(ValueProvider):
    """Sample realistic clinical values using model-supplied distributions.

    Not implemented in v1. The class and cache contract are defined now so the
    generator can target this interface without change when v2 lands.
    """

    def __init__(
        self,
        rng: random.Random,
        cache_dir: str = ".cache",
        model: str = "claude-haiku-4-5-20251001",
        array_size: int = 0,
    ):
        self.rng = rng
        self.cache_dir = cache_dir
        self.model = model
        self._fallback = RandomValueProvider(rng, array_size=array_size)
        self._distributions: dict[str, dict] = {}

    def warmup(self, requests: Iterable[ValueRequest]) -> None:  # pragma: no cover
        raise NotImplementedError(
            "LLMValueProvider is planned for v2. Use --provider random for now."
        )

    def value(self, req: ValueRequest) -> Any:  # pragma: no cover
        raise NotImplementedError(
            "LLMValueProvider is planned for v2. Use --provider random for now."
        )

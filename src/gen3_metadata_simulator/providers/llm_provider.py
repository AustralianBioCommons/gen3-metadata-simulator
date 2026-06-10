"""LLM-backed value provider: realistic, semantically-constrained values.

Instead of arbitrary in-bounds randomness, this provider uses a lightweight
model's domain knowledge (captured as :class:`FieldSpec`s via a
:class:`SpecSource`, cached to disk) to produce:

* **numeric** — sampled from a distribution and clamped to realistic limits
  (so e.g. ``month_birth`` stays in ``[1, 12]``),
* **dates** — real calendar dates within a plausible window, rendered to the
  schema's pattern,
* **text** — domain-appropriate prose drawn from an LLM-supplied pool.

Everything else (enums, booleans, arrays, pattern-constrained strings) and any
field without a cached spec falls back to the v1 :class:`RandomValueProvider`.

``warmup`` runs once before generation: it asks the source for specs for the
uncached numeric/date/text fields and persists them, so generation itself makes
no API calls and is reproducible under a seed.
"""

from __future__ import annotations

import random
from typing import Any, Iterable

from gen3_metadata_simulator.providers.base import ValueProvider, ValueRequest
from gen3_metadata_simulator.providers.classify import field_kind
from gen3_metadata_simulator.providers.dates import realistic_date
from gen3_metadata_simulator.providers.random_provider import RandomValueProvider
from gen3_metadata_simulator.providers.specs import FieldSpec, SpecCache, SpecSource, spec_key

_LLM_KINDS = {"numeric", "date", "text"}


class LLMValueProvider(ValueProvider):
    """Generate realistic values from cached, LLM-supplied field specs."""

    def __init__(
        self,
        rng: random.Random,
        source: SpecSource,
        cache_path: str = ".cache/distributions.json",
        array_size: int = 0,
        text_pool_size: int = 10,
    ):
        self.rng = rng
        self.source = source
        self.cache_path = cache_path
        self.text_pool_size = text_pool_size
        self._cache = SpecCache().load(cache_path)
        self._random = RandomValueProvider(rng, array_size=array_size)

    def warmup(self, requests: Iterable[ValueRequest]) -> None:
        """Fetch and cache specs for uncached numeric/date/text fields."""
        missing: dict[str, ValueRequest] = {}
        for req in requests:
            if field_kind(req) not in _LLM_KINDS:
                continue
            key = spec_key(req)
            if key in self._cache or key in missing:
                continue
            missing[key] = req
        if not missing:
            return

        estimates = self.source.estimate(list(missing.values()), self.text_pool_size)
        for key, spec in estimates.items():
            self._cache.put(key, spec)
        self._cache.save(self.cache_path)

    def value(self, req: ValueRequest) -> Any:
        kind = field_kind(req)
        spec = self._cache.get(spec_key(req)) if kind in _LLM_KINDS else None
        if spec is not None:
            if kind == "date" and spec.kind == "date":
                return realistic_date(req, spec, self.rng)
            if kind == "numeric" and spec.kind == "numeric" and spec.mean is not None:
                return self._numeric(req, spec)
            if kind == "text" and spec.kind == "text" and spec.examples:
                return self.rng.choice(list(spec.examples))
        return self._random.value(req)

    def _numeric(self, req: ValueRequest, spec: FieldSpec) -> Any:
        std = abs(spec.stddev) if spec.stddev else 0.0
        value = self.rng.gauss(spec.mean, std)

        lo = _max_opt(req.minimum, spec.minimum)
        hi = _min_opt(req.maximum, spec.maximum)
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)

        if req.json_type == "integer":
            return int(round(value))
        return value


def _max_opt(a: float | None, b: float | None) -> float | None:
    vals = [v for v in (a, b) if v is not None]
    return max(vals) if vals else None


def _min_opt(a: float | None, b: float | None) -> float | None:
    vals = [v for v in (a, b) if v is not None]
    return min(vals) if vals else None

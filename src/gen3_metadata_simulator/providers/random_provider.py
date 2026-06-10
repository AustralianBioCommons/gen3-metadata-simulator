"""Schema-driven random value provider (v1).

Produces values that satisfy the property's JSON Schema constraints:

* enums           -> a random allowed value
* integer/number  -> a bounded random number (respecting minimum/maximum)
* boolean         -> random True/False
* string          -> if a ``pattern`` is present, a random string matching it
  (via ``rstr.xeger``); otherwise a random two-word token like the example
  metadata (e.g. ``focometer_quinch``)
* array           -> ``[]`` by default, or ``array_size`` sampled elements

All randomness flows through a single injected ``random.Random`` so a seed makes
the whole run reproducible. ``rstr`` is seeded from that same RNG per-call to
keep pattern strings deterministic too.
"""

from __future__ import annotations

import random
from typing import Any

import rstr

from gen3_metadata_simulator.providers.base import ValueProvider, ValueRequest

# A small embedded wordlist keeps string output readable and dependency-free,
# echoing the ``word_word`` style of the reference metadata.
_WORDS = [
    "focometer", "quinch", "palatopharyngeus", "perula", "gagee", "manna",
    "ceratite", "hyperparasitic", "paraheliotropic", "ferulaceous", "bucca",
    "effectualize", "orbitelariae", "unhull", "momus", "meltingly", "risibles",
    "hemoplastic", "sulfocarbolic", "grimacer", "aureola", "diazoimide",
    "rechase", "anteporch", "herpetology", "amphoral", "weathergleam",
    "courge", "puissantness", "ourselves", "cnidophore", "subabbot",
    "electrization", "watershed", "plicater", "infecter", "pergamentaceous",
    "stenchion", "retrade", "biophysiological", "trachelotomy", "philosophedom",
    "lethiferous", "bagrationite", "styryl", "nonlactescent", "quartful",
    "rheophore", "underprompter", "nonsanction", "patriotically", "unweaponed",
    "furoin", "britska", "squattage", "preapprise", "unbodylike",
]


class RandomValueProvider(ValueProvider):
    """Generate schema-valid random values for properties."""

    def __init__(self, rng: random.Random, array_size: int = 0, null_rate: float = 0.0):
        """:param rng: Seeded RNG shared with the generator for reproducibility.
        :param array_size: Number of elements to emit for array properties
            (0 => empty array, which is always valid absent ``minItems``).
        :param null_rate: Probability of emitting ``null`` for an optional
            property (0 disables; required properties are never nulled).
        """
        self.rng = rng
        self.array_size = array_size
        self.null_rate = null_rate
        # Bind rstr to our RNG so pattern-matching strings are reproducible too.
        self._rstr = rstr.Rstr(rng)

    def value(self, req: ValueRequest) -> Any:
        if (
            self.null_rate
            and not req.required
            and req.json_type != "array"
            and self.rng.random() < self.null_rate
        ):
            return None

        if req.enum:
            return self.rng.choice(req.enum)

        json_type = req.json_type
        if json_type == "array":
            return self._array(req)
        if json_type == "boolean":
            return self.rng.choice([True, False])
        if json_type == "integer":
            return self._integer(req)
        if json_type == "number":
            return self._number(req)
        # string and untyped/free-form properties
        return self._string(req)

    def _array(self, req: ValueRequest) -> list:
        if self.array_size <= 0 or req.item_request is None:
            return []
        return [self.value(req.item_request) for _ in range(self.array_size)]

    def _integer(self, req: ValueRequest) -> int:
        lo = int(req.minimum) if req.minimum is not None else 0
        hi = int(req.maximum) if req.maximum is not None else lo + 1000
        if hi < lo:
            hi = lo
        return self.rng.randint(lo, hi)

    def _number(self, req: ValueRequest) -> float:
        lo = float(req.minimum) if req.minimum is not None else 0.0
        hi = float(req.maximum) if req.maximum is not None else lo + 100.0
        if hi < lo:
            hi = lo
        return self.rng.uniform(lo, hi)

    def _string(self, req: ValueRequest) -> str:
        if req.pattern:
            return self._rstr.xeger(req.pattern)
        return f"{self.rng.choice(_WORDS)}_{self.rng.choice(_WORDS)}"

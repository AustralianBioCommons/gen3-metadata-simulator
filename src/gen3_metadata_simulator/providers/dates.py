"""Generate realistic calendar dates that also satisfy the schema's pattern.

v1 emitted regex-valid nonsense (e.g. ``3170-94-14`` — month 94). This module
picks a *real* date uniformly within the LLM-supplied plausible window (so the
month is always 1-12 and the day valid) and renders it in the exact shape the
field's ``pattern``/``format`` requires, verifying the result matches.
"""

from __future__ import annotations

import datetime as dt
import random
import re

import rstr

from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.specs import FieldSpec

_DEFAULT_EARLIEST = dt.date(1950, 1, 1)
_DEFAULT_LATEST = dt.date(2025, 12, 31)
_DATE_FMT = "%Y-%m-%d"
_DATETIME_FMT = "%Y-%m-%dT%H:%M:%SZ"


def realistic_date(req: ValueRequest, spec: FieldSpec, rng: random.Random) -> str:
    """Return a real calendar date within ``spec``'s window, formatted for ``req``.

    Falls back to ``rstr.xeger`` on the field's pattern if the rendered date does
    not match it (so output is always schema-valid even for unusual formats).
    """
    earliest = _parse(spec.earliest, _DEFAULT_EARLIEST)
    latest = _parse(spec.latest, _DEFAULT_LATEST)
    if latest < earliest:
        earliest, latest = latest, earliest

    ordinal = rng.randint(earliest.toordinal(), latest.toordinal())
    day = dt.date.fromordinal(ordinal)

    if _wants_datetime(req):
        # A plausible time-of-day keeps the timestamp realistic and deterministic.
        seconds = rng.randint(0, 24 * 3600 - 1)
        rendered = dt.datetime(day.year, day.month, day.day).replace(
            hour=seconds // 3600, minute=(seconds % 3600) // 60, second=seconds % 60
        ).strftime(_DATETIME_FMT)
    else:
        rendered = day.strftime(_DATE_FMT)

    if req.pattern and not re.fullmatch(req.pattern, rendered):
        return rng_xeger(req.pattern, rng)
    return rendered


def _wants_datetime(req: ValueRequest) -> bool:
    if req.fmt == "date-time":
        return True
    return bool(req.pattern and "T" in req.pattern)


def _parse(value: str | None, default: dt.date) -> dt.date:
    if not value:
        return default
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return default


def rng_xeger(pattern: str, rng: random.Random) -> str:
    """Generate a pattern-matching string using an rstr bound to ``rng``."""
    return rstr.Rstr(rng).xeger(pattern)

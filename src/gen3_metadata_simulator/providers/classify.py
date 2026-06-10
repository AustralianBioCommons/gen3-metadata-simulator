"""Classify a property into the value-generation strategy it should use.

``field_kind`` is the single routing decision shared by the LLM provider's
warmup pass (what to ask the model about) and its value generation (which path
to take):

* ``numeric`` — integer/number, not an enum → distribution + limits
* ``date``    — a temporal string → realistic calendar date in a plausible range
* ``text``    — an unconstrained string → realistic LLM-supplied prose
* ``other``   — enums, booleans, arrays, and pattern-constrained strings → keep
  the v1 random/regex/enum behaviour
"""

from __future__ import annotations

import re

from gen3_metadata_simulator.providers.base import ValueRequest

# The two canonical date sub-shapes that appear literally inside the schema's
# regex patterns (e.g. "^[0-9]{4}-[0-9]{2}-[0-9]{2}$" or "^\d{4}-\d{2}-\d{2}").
_DATE_PATTERN_TOKENS = (r"\d{4}-\d{2}-\d{2}", "[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATE_NAME = re.compile(r"(^date_|_date$|datetime)", re.IGNORECASE)


def field_kind(req: ValueRequest) -> str:
    """Return ``"numeric"``, ``"date"``, ``"text"``, or ``"other"`` for ``req``."""
    if _is_date(req):
        return "date"
    if req.json_type in ("integer", "number") and not req.enum:
        return "numeric"
    if req.json_type == "string" and not req.enum and not req.pattern:
        return "text"
    return "other"


def _is_date(req: ValueRequest) -> bool:
    if req.enum:
        return False
    if req.fmt in ("date", "date-time"):
        return True
    if req.pattern and any(tok in req.pattern for tok in _DATE_PATTERN_TOKENS):
        return True
    if req.json_type == "string" and not req.pattern and _DATE_NAME.search(req.name or ""):
        return True
    return False

"""Tests for realistic_date: real calendar dates that also satisfy the schema.

The v1 random provider produced regex-valid nonsense like '3170-94-14' (month
94). The whole point of the date path is that generated dates are *real* — month
1-12, valid day — within a plausible range, while still matching the schema's
pattern/format so validation passes.
"""

import datetime as dt
import random
import re

from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.dates import realistic_date
from gen3_metadata_simulator.providers.specs import FieldSpec


def _date_req():
    return ValueRequest(node="demographic", name="baseline_date", json_type="string",
                        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def test_generated_date_is_a_real_calendar_date_in_range():
    """The value parses as a real date with month 1-12, within [earliest, latest].

    Asserting the value round-trips through datetime.date.fromisoformat proves it
    is a genuine calendar date (no month 94), and the bounds check proves the
    LLM-supplied plausible window is honoured.
    """
    spec = FieldSpec(kind="date", earliest="1990-01-01", latest="2020-12-31")
    rng = random.Random(0)
    for _ in range(200):
        value = realistic_date(_date_req(), spec, rng)
        parsed = dt.date.fromisoformat(value)  # raises if month/day invalid
        assert 1 <= parsed.month <= 12
        assert dt.date(1990, 1, 1) <= parsed <= dt.date(2020, 12, 31)


def test_generated_date_matches_schema_pattern():
    """The rendered date fully matches the field's regex pattern.

    A realistic date is useless if it fails schema validation; the renderer must
    emit exactly the shape the pattern requires (here YYYY-MM-DD).
    """
    spec = FieldSpec(kind="date", earliest="2000-01-01", latest="2001-01-01")
    value = realistic_date(_date_req(), spec, random.Random(1))
    assert re.fullmatch(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", value)


def test_same_seed_is_deterministic():
    """Two calls with equal seeds produce the same date (reproducible output)."""
    spec = FieldSpec(kind="date", earliest="1990-01-01", latest="2020-12-31")
    a = realistic_date(_date_req(), spec, random.Random(42))
    b = realistic_date(_date_req(), spec, random.Random(42))
    assert a == b


def test_datetime_pattern_renders_iso_datetime():
    """A date-time field renders an ISO 8601 datetime matching its pattern.

    analysis_workflow.workflow_start_datetime requires a full timestamp; the
    renderer must produce one that validates, not a bare date.
    """
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
    req = ValueRequest(node="analysis_workflow", name="workflow_start_datetime",
                       json_type="string", pattern=pattern, fmt="date-time")
    spec = FieldSpec(kind="date", earliest="2015-01-01", latest="2020-12-31")
    value = realistic_date(req, spec, random.Random(3))
    assert re.fullmatch(pattern, value)

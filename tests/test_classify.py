"""Tests for field_kind: routing a property to the right generation strategy.

The LLM provider treats numeric, date, and free-text fields differently, while
leaving enums and pattern-constrained strings to the v1 random/regex path.
field_kind is the single source of truth for that routing, used by both the
warmup pass (what to ask the LLM about) and value generation (which path to
take), so it must classify the schema's real field shapes correctly.
"""

from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.classify import field_kind


def _req(**kw):
    base = dict(node="n", name="p")
    base.update(kw)
    return ValueRequest(**base)


def test_integer_field_is_numeric():
    """An integer property with no enum is numeric (gets a distribution + limits).

    e.g. demographic.month_birth — the LLM should bound it to [1, 12].
    """
    assert field_kind(_req(name="month_birth", json_type="integer")) == "numeric"


def test_date_pattern_and_datetime_format_are_dates():
    """Fields with a YYYY-MM-DD pattern or a date-time format classify as date.

    baseline_date carries pattern ^\\d{4}-\\d{2}-\\d{2}; intended_release_date
    carries format: date-time. Both must take the realistic-calendar-date path.
    """
    assert field_kind(_req(name="baseline_date", json_type="string",
                           pattern=r"^\d{4}-\d{2}-\d{2}")) == "date"
    assert field_kind(_req(name="intended_release_date", json_type="string",
                           fmt="date-time")) == "date"


def test_unconstrained_string_is_text():
    """A plain string with no pattern/enum is free text (LLM-supplied examples).

    e.g. lipidomics_assay.assay_description should read like a real description.
    """
    assert field_kind(_req(name="assay_description", json_type="string",
                           description="Description of the assay")) == "text"


def test_enum_and_pattern_strings_are_other():
    """Enums and pattern-constrained strings stay on the v1 path (kind 'other').

    sex is an enum (sample its allowed values); sample_source (UBERON pattern)
    and md5sum (hex pattern) need exact formats, so regex generation wins over
    prose — none of these should be sent to the LLM as free text.
    """
    assert field_kind(_req(name="sex", json_type="string", enum=["male", "female"])) == "other"
    assert field_kind(_req(name="sample_source", json_type="string",
                           pattern=r"^UBERON:[0-9]{7}$")) == "other"
    assert field_kind(_req(name="md5sum", json_type="string",
                           pattern=r"^[a-f0-9]{32}$")) == "other"

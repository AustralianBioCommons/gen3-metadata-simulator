"""The ValueProvider interface and the request object passed to it.

A `ValueProvider` turns a single property's schema into a concrete value. The
generator builds a `ValueRequest` describing the property (name, JSON type,
enum, numeric bounds, regex pattern, array item spec) and asks the provider for
a value. Swapping the provider swaps the value-generation strategy without
touching the generator:

* `RandomValueProvider` (v1) — schema-driven random values.
* `LLMValueProvider` (v2)   — realistic clinical values sampled from
  distributions a lightweight model supplies (see ``llm_provider.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass
class ValueRequest:
    """Everything a provider needs to produce a value for one property.

    :param node: Bare node name the property belongs to (e.g. ``demographic``).
    :param name: Property name (e.g. ``bmi_baseline``).
    :param description: Property description from the schema, if any.
    :param json_type: Resolved JSON type — string/integer/number/boolean/array.
        ``None`` if the schema declares none (treated as free-form).
    :param enum: Allowed values, if the property (or array item) is an enum.
    :param item_request: For arrays, the ValueRequest describing one element.
    :param fmt: JSON Schema ``format`` (e.g. ``date-time``), if present.
    :param pattern: JSON Schema ``pattern`` regex the value must match, if any.
    :param minimum: Numeric lower bound, if present.
    :param maximum: Numeric upper bound, if present.
    :param required: Whether the property is required by the node.
    :param fingerprint: md5 of the field's resolved JSON schema. Lets the LLM
        provider's cache detect when a field's schema changed and re-query only
        that field. ``None`` when not computed (e.g. hand-built in tests).
    """

    node: str
    name: str
    description: Optional[str] = None
    json_type: Optional[str] = None
    enum: Optional[list] = None
    item_request: Optional["ValueRequest"] = None
    fmt: Optional[str] = None
    pattern: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    required: bool = False
    fingerprint: Optional[str] = None


class ValueProvider(ABC):
    """Strategy that produces a concrete value for a property schema."""

    @abstractmethod
    def value(self, req: ValueRequest) -> Any:
        """Return a single value satisfying ``req``."""

    def warmup(self, requests: Iterable[ValueRequest]) -> None:
        """Optional pre-pass over every request before generation begins.

        The random provider ignores this. The LLM provider uses it to batch the
        model calls that build its distribution table, so generation itself
        makes no network calls. Default: no-op.
        """
        return None

"""Field specifications: the LLM's semantic knowledge about a field.

A :class:`FieldSpec` captures what a lightweight model knows about one property:

* numeric → ``mean``, ``stddev`` and realistic ``minimum``/``maximum`` limits
* date    → a plausible ``earliest``..``latest`` calendar window
* text    → a pool of realistic ``examples`` strings

:class:`SpecCache` persists specs to JSON so repeat runs make no API calls.
:class:`SpecSource` is the pluggable producer; :class:`AnthropicSpecSource`
implements it against the Anthropic API using structured output.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.classify import field_kind

CHUNK_SIZE = 20
_MODEL_MARKER = "VARIABLES_JSON:"


def spec_key(req: ValueRequest) -> str:
    """The cache key for a field: ``"<node>/<name>"``."""
    return f"{req.node}/{req.name}"


@dataclass(frozen=True)
class FieldSpec:
    """The model's estimate for one field. Only the fields for ``kind`` are set."""

    kind: str  # "numeric" | "date" | "text"
    mean: Optional[float] = None
    stddev: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: Optional[str] = None
    earliest: Optional[str] = None
    latest: Optional[str] = None
    examples: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v not in (None, ())}
        if self.examples:
            d["examples"] = list(self.examples)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FieldSpec":
        examples = tuple(d.get("examples", ()))
        return cls(
            kind=d["kind"],
            mean=d.get("mean"),
            stddev=d.get("stddev"),
            minimum=d.get("minimum"),
            maximum=d.get("maximum"),
            unit=d.get("unit"),
            earliest=d.get("earliest"),
            latest=d.get("latest"),
            examples=examples,
        )


class SpecCache:
    """An in-memory, JSON-persistable table of ``"node/name" -> FieldSpec``."""

    def __init__(self):
        self._specs: dict[str, FieldSpec] = {}

    def get(self, key: str) -> Optional[FieldSpec]:
        return self._specs.get(key)

    def put(self, key: str, spec: FieldSpec) -> None:
        self._specs[key] = spec

    def __contains__(self, key: str) -> bool:
        return key in self._specs

    def load(self, path: str) -> "SpecCache":
        p = Path(path)
        if p.is_file():
            raw = json.loads(p.read_text())
            self._specs = {k: FieldSpec.from_dict(v) for k, v in raw.items()}
        return self

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        serialised = {k: v.to_dict() for k, v in self._specs.items()}
        p.write_text(json.dumps(serialised, indent=2, sort_keys=True))


class SpecSource(ABC):
    """Produces a ``FieldSpec`` for each requested field, keyed ``"node/name"``."""

    @abstractmethod
    def estimate(self, requests: list[ValueRequest], text_pool_size: int) -> dict[str, FieldSpec]:
        ...


# --- Anthropic-backed source ------------------------------------------------

class _FieldSpecModel(BaseModel):
    """Structured-output schema for one field's estimate (the model fills this)."""

    key: str
    kind: str
    mean: Optional[float] = None
    stddev: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: Optional[str] = None
    earliest: Optional[str] = None
    latest: Optional[str] = None
    examples: Optional[list[str]] = None


class _SpecTable(BaseModel):
    specs: list[_FieldSpecModel]


_SYSTEM = (
    "You provide realistic value generation hints for fields in a clinical/biomedical "
    "data dictionary, so a simulator can produce believable synthetic records. "
    "For each variable you are given a key, a kind, and its name/description.\n"
    "- kind 'numeric': give a plausible population `mean` and `stddev`, realistic `minimum` "
    "and `maximum` limits, and a `unit`. Respect real-world bounds (e.g. a month is 1-12, "
    "a human age 0-120, a percentage 0-100).\n"
    "- kind 'date': give `earliest` and `latest` plausible calendar dates (YYYY-MM-DD) for "
    "the field's meaning.\n"
    "- kind 'text': give `examples`, a list of short, realistic, domain-appropriate strings "
    "for the field, using its name, description and node for context.\n"
    "Echo each variable's `key` exactly. Only fill the fields relevant to the kind."
)


class AnthropicSpecSource(SpecSource):
    """Estimate field specs via the Anthropic API using structured output.

    The Anthropic client is injectable for testing; in production it is created
    lazily from an API key so importing this module never requires the SDK.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 client=None, chunk_size: int = CHUNK_SIZE, max_tokens: int = 4096):
        if model is None:
            raise ValueError("AnthropicSpecSource requires an explicit model")
        self.model = model
        self.chunk_size = chunk_size
        self.max_tokens = max_tokens
        if client is not None:
            self._client = client
        else:
            import anthropic  # lazy: only needed for real API calls

            self._client = anthropic.Anthropic(api_key=api_key)

    def estimate(self, requests: list[ValueRequest], text_pool_size: int) -> dict[str, FieldSpec]:
        out: dict[str, FieldSpec] = {}
        for start in range(0, len(requests), self.chunk_size):
            chunk = requests[start:start + self.chunk_size]
            out.update(self._estimate_chunk(chunk, text_pool_size))
        return out

    def _estimate_chunk(self, chunk: list[ValueRequest], text_pool_size: int) -> dict[str, FieldSpec]:
        variables = [self._variable(req) for req in chunk]
        user = (
            f"Provide a spec for each variable. For 'text' variables give about "
            f"{text_pool_size} examples.\n{_MODEL_MARKER}{json.dumps(variables)}"
        )
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_format=_SpecTable,
        )
        table = response.parsed_output
        return {s.key: self._to_field_spec(s) for s in table.specs}

    @staticmethod
    def _variable(req: ValueRequest) -> dict:
        return {
            "key": spec_key(req),
            "kind": field_kind(req),
            "node": req.node,
            "name": req.name,
            "description": req.description or "",
            "minimum": req.minimum,
            "maximum": req.maximum,
        }

    @staticmethod
    def _to_field_spec(model) -> FieldSpec:
        examples = tuple(model.examples) if getattr(model, "examples", None) else ()
        return FieldSpec(
            kind=model.kind,
            mean=model.mean,
            stddev=model.stddev,
            minimum=model.minimum,
            maximum=model.maximum,
            unit=model.unit,
            earliest=model.earliest,
            latest=model.latest,
            examples=examples,
        )

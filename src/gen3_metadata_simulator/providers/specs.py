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
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.classify import field_kind

logger = logging.getLogger(__name__)

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
    """A JSON-persistable table of ``"node/name" -> (FieldSpec, fingerprint)``.

    The fingerprint is an md5 of the field's schema (see ``generator._fingerprint``).
    Storing it lets the LLM provider tell a *cached and unchanged* field from one
    whose schema changed and must be re-estimated.

    On-disk format: ``{"<node/name>": {"fingerprint": "...", "spec": {...}}}``.
    Older flat files (``{"<node/name>": {"kind": ...}}``) still load — their
    entries get ``fingerprint=None`` and are harmlessly rebuilt on the next run.
    """

    def __init__(self):
        self._entries: dict[str, tuple[FieldSpec, Optional[str]]] = {}

    def get(self, key: str) -> Optional[FieldSpec]:
        entry = self._entries.get(key)
        return entry[0] if entry else None

    def fingerprint(self, key: str) -> Optional[str]:
        entry = self._entries.get(key)
        return entry[1] if entry else None

    def matches(self, key: str, fingerprint: Optional[str]) -> bool:
        """True if ``key`` is cached and its stored fingerprint equals ``fingerprint``."""
        return key in self._entries and self._entries[key][1] == fingerprint

    def put(self, key: str, spec: FieldSpec, fingerprint: Optional[str] = None) -> None:
        self._entries[key] = (spec, fingerprint)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def load(self, path: str) -> "SpecCache":
        p = Path(path)
        if p.is_file():
            raw = json.loads(p.read_text())
            for key, value in raw.items():
                if "spec" in value:  # new format: {fingerprint, spec}
                    self._entries[key] = (FieldSpec.from_dict(value["spec"]),
                                          value.get("fingerprint"))
                else:  # legacy flat format: the spec dict itself
                    self._entries[key] = (FieldSpec.from_dict(value), None)
        return self

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        serialised = {
            key: {"fingerprint": fp, "spec": spec.to_dict()}
            for key, (spec, fp) in self._entries.items()
        }
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


class _ChunkedSpecSource(SpecSource):
    """Shared batching/prompt/mapping for API-backed sources.

    Subclasses differ only in ``_call_model`` — the actual vendor SDK call that
    turns a system+user prompt into a ``_SpecTable``. Everything else (chunking,
    the variable payload, logging, mapping back to ``FieldSpec``) is identical.
    """

    def __init__(self, model: str | None, chunk_size: int, max_tokens: int):
        if model is None:
            raise ValueError(f"{type(self).__name__} requires an explicit model")
        self.model = model
        self.chunk_size = chunk_size
        self.max_tokens = max_tokens

    def estimate(self, requests: list[ValueRequest], text_pool_size: int) -> dict[str, FieldSpec]:
        n_calls = (len(requests) + self.chunk_size - 1) // self.chunk_size
        logger.info("LLM estimate: %d field(s) over %d API call(s) [model=%s]",
                    len(requests), n_calls, self.model)
        out: dict[str, FieldSpec] = {}
        for start in range(0, len(requests), self.chunk_size):
            chunk = requests[start:start + self.chunk_size]
            out.update(self._estimate_chunk(chunk, text_pool_size))
        return out

    def _estimate_chunk(self, chunk: list[ValueRequest], text_pool_size: int) -> dict[str, FieldSpec]:
        logger.debug("LLM estimate: requesting %d spec(s): %s",
                     len(chunk), ", ".join(spec_key(r) for r in chunk))
        variables = [self._variable(req) for req in chunk]
        user = (
            f"Provide a spec for each variable. For 'text' variables give about "
            f"{text_pool_size} examples.\n{_MODEL_MARKER}{json.dumps(variables)}"
        )
        table = self._call_model(_SYSTEM, user)
        return {s.key: self._to_field_spec(s) for s in table.specs}

    def _call_model(self, system: str, user: str) -> "_SpecTable":
        """Call the vendor's structured-output API and return the parsed table."""
        raise NotImplementedError

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


class AnthropicSpecSource(_ChunkedSpecSource):
    """Estimate field specs via the Anthropic API using structured output.

    The Anthropic client is injectable for testing; in production it is created
    lazily from an API key so importing this module never requires the SDK.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 client=None, chunk_size: int = CHUNK_SIZE, max_tokens: int = 4096):
        super().__init__(model, chunk_size, max_tokens)
        if client is not None:
            self._client = client
        else:
            import anthropic  # lazy: only needed for real API calls

            self._client = anthropic.Anthropic(api_key=api_key)

    def _call_model(self, system: str, user: str) -> "_SpecTable":
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=_SpecTable,
        )
        return response.parsed_output


class OpenAISpecSource(_ChunkedSpecSource):
    """Estimate field specs via the OpenAI API using structured output.

    Uses ``chat.completions.parse`` with the same ``_SpecTable`` Pydantic schema
    as the Anthropic source. The client is injectable for testing; in production
    it is created lazily so importing this module never requires the SDK.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 client=None, chunk_size: int = CHUNK_SIZE, max_tokens: int = 4096):
        super().__init__(model, chunk_size, max_tokens)
        if client is not None:
            self._client = client
        else:
            import openai  # lazy: only needed for real API calls

            self._client = openai.OpenAI(api_key=api_key)

    def _call_model(self, system: str, user: str) -> "_SpecTable":
        completion = self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=_SpecTable,
        )
        return completion.choices[0].message.parsed

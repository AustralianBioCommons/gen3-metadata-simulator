"""Tests for field specs: the cache, and the Anthropic/OpenAI spec sources.

A FieldSpec is the LLM's semantic knowledge about one field — a numeric
distribution + limits, a plausible date range, or a pool of realistic text
examples. The cache persists specs so repeat runs make no API calls, and a
source turns a batch of fields into specs. Both vendor sources are tested with a
mocked client so no network call or key is needed.
"""

import json
from types import SimpleNamespace

from gen3_metadata_simulator.providers.base import ValueRequest
from gen3_metadata_simulator.providers.specs import (
    AnthropicSpecSource,
    FieldSpec,
    OpenAISpecSource,
    SpecCache,
    spec_key,
)


def _numeric_specs_from_messages(messages):
    """Build a canned numeric spec per variable embedded in the prompt.

    Shared by the fake Anthropic and OpenAI clients so both tests echo a spec for
    exactly the keys the source asked about.
    """
    text = messages[-1]["content"]
    variables = json.loads(text.split("VARIABLES_JSON:", 1)[1])
    return [
        SimpleNamespace(key=v["key"], kind="numeric", mean=1.0, stddev=0.5,
                        minimum=0.0, maximum=2.0, unit="x",
                        earliest=None, latest=None, examples=None)
        for v in variables
    ]


def test_spec_cache_roundtrips_specs_and_fingerprints(tmp_path):
    """A cache of specs + fingerprints saves to JSON and loads back identically.

    Persistence is what makes the LLM provider cheap on repeat runs — the saved
    table must reload to the same specs *and* the per-field fingerprints used to
    detect schema changes.
    """
    cache = SpecCache()
    cache.put("demographic/month_birth",
              FieldSpec(kind="numeric", mean=6.5, stddev=3.4, minimum=1, maximum=12, unit="month"),
              fingerprint="md5-month")
    cache.put("demographic/baseline_date",
              FieldSpec(kind="date", earliest="1990-01-01", latest="2020-12-31"),
              fingerprint="md5-date")
    cache.put("lipidomics_assay/assay_description",
              FieldSpec(kind="text", examples=("LC-MS/MS lipidomics of plasma", "Shotgun lipidomics")),
              fingerprint="md5-text")
    path = tmp_path / "specs.json"
    cache.save(str(path))

    reloaded = SpecCache().load(str(path))
    assert reloaded.get("demographic/month_birth") == cache.get("demographic/month_birth")
    assert reloaded.get("demographic/baseline_date") == cache.get("demographic/baseline_date")
    assert reloaded.fingerprint("demographic/month_birth") == "md5-month"


def test_matches_compares_stored_fingerprint(tmp_path):
    """matches() is true only when the field is cached AND its fingerprint equals.

    This is the gate that decides "reuse" vs "re-estimate": a matching md5 means
    the field's schema is unchanged, a different md5 means it changed.
    """
    cache = SpecCache()
    cache.put("n/p", FieldSpec(kind="numeric", mean=1.0, stddev=0.5), fingerprint="md5-a")
    assert cache.matches("n/p", "md5-a") is True
    assert cache.matches("n/p", "md5-b") is False      # schema changed
    assert cache.matches("absent/key", "md5-a") is False  # never cached


def test_legacy_flat_cache_loads_with_no_fingerprint(tmp_path):
    """An old flat-format cache file still loads, with fingerprints set to None.

    Backward compatibility: existing .cache/distributions.json files (written
    before fingerprinting) must not crash. Their entries carry no fingerprint, so
    they get rebuilt once on the next run rather than reused blindly.
    """
    path = tmp_path / "old.json"
    path.write_text('{"demographic/month_birth": {"kind": "numeric", "mean": 6.5, "stddev": 3.4}}')

    cache = SpecCache().load(str(path))
    assert cache.get("demographic/month_birth").mean == 6.5
    assert cache.fingerprint("demographic/month_birth") is None


class _FakeMessages:
    """A stand-in for client.messages that echoes a spec per requested variable.

    It reads the variable list the source embedded in the prompt and returns a
    numeric FieldSpec for each key, so the source's request-building and
    response-mapping can both be asserted without a real API call.
    """

    def __init__(self, recorder):
        self._recorder = recorder

    def parse(self, **kwargs):
        self._recorder.append(kwargs)
        specs = _numeric_specs_from_messages(kwargs["messages"])
        return SimpleNamespace(parsed_output=SimpleNamespace(specs=specs))


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = _FakeMessages(self.calls)


class _FakeChatCompletions:
    """Stand-in for client.chat.completions, exposing OpenAI's parse() shape."""

    def __init__(self, recorder):
        self._recorder = recorder

    def parse(self, **kwargs):
        self._recorder.append(kwargs)
        specs = _numeric_specs_from_messages(kwargs["messages"])
        message = SimpleNamespace(parsed=SimpleNamespace(specs=specs))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeOpenAIClient:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(self.calls))


def _numeric_req(node, name):
    return ValueRequest(node=node, name=name, json_type="number", description=f"the {name}")


def test_anthropic_source_builds_request_and_maps_response():
    """estimate() sends the model + every key/kind, and maps the reply by key.

    The mocked client records the request: it must carry the chosen model and a
    variable entry (key + kind) for each field. The returned mapping must be
    keyed by 'node/name' with the spec the model 'returned'.
    """
    client = _FakeClient()
    source = AnthropicSpecSource(model="claude-haiku-4-5", client=client)
    reqs = [_numeric_req("demographic", "bmi_baseline"),
            _numeric_req("demographic", "baseline_age")]

    specs = source.estimate(reqs, text_pool_size=5)

    assert set(specs) == {"demographic/bmi_baseline", "demographic/baseline_age"}
    assert specs["demographic/bmi_baseline"].kind == "numeric"
    # request carried the model and both keys with their kind
    sent = client.calls[0]
    assert sent["model"] == "claude-haiku-4-5"
    blob = sent["messages"][-1]["content"]
    assert "demographic/bmi_baseline" in blob and "demographic/baseline_age" in blob
    assert "numeric" in blob


def test_anthropic_source_chunks_large_batches():
    """estimate() splits a batch larger than chunk_size across multiple calls.

    Batching bounds token cost/latency; with chunk_size=2 and five variables the
    source must make three parse calls and still return all five specs.
    """
    client = _FakeClient()
    source = AnthropicSpecSource(model="claude-haiku-4-5", client=client, chunk_size=2)
    reqs = [_numeric_req("n", f"p{i}") for i in range(5)]

    specs = source.estimate(reqs, text_pool_size=3)

    assert len(specs) == 5
    assert len(client.calls) == 3  # ceil(5 / 2)


def test_openai_source_builds_request_and_maps_response():
    """The OpenAI source sends the model + keys and maps the parsed reply by key.

    Mirrors the Anthropic test against OpenAI's response shape
    (choices[0].message.parsed) so both vendors are proven to drive the same
    pipeline from the same _SpecTable schema, with no network.
    """
    client = _FakeOpenAIClient()
    source = OpenAISpecSource(model="gpt-4o-mini", client=client)
    reqs = [_numeric_req("demographic", "bmi_baseline"),
            _numeric_req("demographic", "baseline_age")]

    specs = source.estimate(reqs, text_pool_size=5)

    assert set(specs) == {"demographic/bmi_baseline", "demographic/baseline_age"}
    assert specs["demographic/bmi_baseline"].kind == "numeric"
    sent = client.calls[0]
    assert sent["model"] == "gpt-4o-mini"
    # OpenAI puts the system prompt as the first message and the variables in the last
    assert sent["messages"][0]["role"] == "system"
    blob = sent["messages"][-1]["content"]
    assert "demographic/bmi_baseline" in blob and "demographic/baseline_age" in blob


def test_openai_source_chunks_large_batches():
    """estimate() splits a large batch across multiple OpenAI calls, like Anthropic."""
    client = _FakeOpenAIClient()
    source = OpenAISpecSource(model="gpt-4o-mini", client=client, chunk_size=2)
    specs = source.estimate([_numeric_req("n", f"p{i}") for i in range(5)], text_pool_size=3)
    assert len(specs) == 5
    assert len(client.calls) == 3


def test_spec_key_uses_node_and_name():
    """spec_key composes the cache key as 'node/name' (the cache's addressing)."""
    assert spec_key(_numeric_req("demographic", "bmi_baseline")) == "demographic/bmi_baseline"

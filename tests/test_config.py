"""Tests for API-key loading from a key-file path referenced by .env.

The security model: .env never contains the key itself, only LLM_API_KEY_FILE
pointing at a separate file (kept outside the repo) that holds the key. These
tests pin that indirection so a key is never accidentally read from .env
directly or leaked.
"""

import pytest

from gen3_metadata_simulator.config import load_api_key, load_llm_config
from gen3_metadata_simulator.errors import ConfigError


def _write_env(tmp_path, **vars) -> str:
    """Write a temp .env with a key file, returning its path. Helper for tests."""
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-secret\n")
    lines = [f"LLM_API_KEY_FILE={key_file}"]
    lines += [f"{k}={v}" for k, v in vars.items()]
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(lines) + "\n")
    return str(env_file)


def test_load_llm_config_reads_provider_model_and_key(tmp_path):
    """A full .env yields an LLMConfig with provider, model, and the key.

    This is the canonical setup the user fills in: the three LLM settings live in
    .env (vendor + model + key-file path) and resolve to one config object.
    """
    env = _write_env(tmp_path, LLM_PROVIDER="openai", LLM_MODEL="gpt-4o-mini")
    cfg = load_llm_config(env_path=env)
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.api_key == "sk-secret"


def test_load_llm_config_defaults_provider_to_anthropic(tmp_path):
    """A .env without LLM_PROVIDER defaults to anthropic (back-compat).

    Older single-variable .env files (key path only) must keep working, defaulting
    to the original vendor.
    """
    env = _write_env(tmp_path, LLM_MODEL="claude-haiku-4-5")
    assert load_llm_config(env_path=env).provider == "anthropic"


def test_load_llm_config_rejects_unknown_provider(tmp_path):
    """An unsupported LLM_PROVIDER raises ConfigError listing the valid choices."""
    env = _write_env(tmp_path, LLM_PROVIDER="gemini", LLM_MODEL="x")
    with pytest.raises(ConfigError):
        load_llm_config(env_path=env)


def test_load_llm_config_requires_a_model(tmp_path):
    """A .env with no LLM_MODEL (and no override) raises ConfigError."""
    env = _write_env(tmp_path, LLM_PROVIDER="openai")
    with pytest.raises(ConfigError):
        load_llm_config(env_path=env)


def test_overrides_beat_env(tmp_path):
    """--llm-provider / --llm-model override the .env values.

    Lets a user point an Anthropic-configured .env at OpenAI for one run without
    editing the file.
    """
    env = _write_env(tmp_path, LLM_PROVIDER="anthropic", LLM_MODEL="claude-haiku-4-5")
    cfg = load_llm_config(env_path=env, provider_override="openai", model_override="gpt-4o-mini")
    assert cfg.provider == "openai" and cfg.model == "gpt-4o-mini"


def _write_env_no_key(tmp_path, **vars) -> str:
    """Write a temp .env WITHOUT a key-file path (for env-var fallback tests)."""
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in vars.items()) + "\n")
    return str(env_file)


def test_no_key_file_falls_back_to_vendor_env_var(tmp_path, monkeypatch):
    """With no LLM_API_KEY_FILE but OPENAI_API_KEY set, api_key is None (SDK reads it).

    This is the installed-tool path: a user who already has OPENAI_API_KEY in
    their environment shouldn't be forced to also create a key file. api_key=None
    tells us to let the OpenAI SDK pick the key up itself.
    """
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    env = _write_env_no_key(tmp_path, LLM_PROVIDER="openai", LLM_MODEL="gpt-4o-mini")

    cfg = load_llm_config(env_path=env)
    assert cfg.provider == "openai"
    assert cfg.api_key is None


def test_no_key_anywhere_raises(tmp_path, monkeypatch):
    """With neither a key file nor the vendor env var, load_llm_config errors clearly.

    The user must be told to set one or the other rather than hitting an opaque
    401 from the SDK later.
    """
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = _write_env_no_key(tmp_path, LLM_PROVIDER="openai", LLM_MODEL="gpt-4o-mini")

    with pytest.raises(ConfigError):
        load_llm_config(env_path=env)


def test_load_api_key_reads_key_from_referenced_file(tmp_path):
    """.env points at a key file; load_api_key returns that file's stripped contents.

    Given a .env containing LLM_API_KEY_FILE=<path> and a key file at <path>
    holding 'sk-ant-secret\\n', the loader returns 'sk-ant-secret' — proving it
    follows the path indirection and trims trailing whitespace/newlines.
    """
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-ant-secret\n")
    env_file = tmp_path / ".env"
    env_file.write_text(f"LLM_API_KEY_FILE={key_file}\n")

    assert load_api_key(env_path=str(env_file)) == "sk-ant-secret"


def test_missing_env_var_raises_config_error(tmp_path):
    """An empty .env (no LLM_API_KEY_FILE) raises ConfigError, not a silent None.

    Callers rely on a typed, explanatory error so the CLI can tell the user to
    set the variable rather than failing later with an opaque auth error.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("# nothing here\n")
    with pytest.raises(ConfigError):
        load_api_key(env_path=str(env_file))


def test_missing_key_file_raises_config_error(tmp_path):
    """LLM_API_KEY_FILE pointing at a non-existent file raises ConfigError.

    A typo'd or moved key-file path must fail clearly at load time.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(f"LLM_API_KEY_FILE={tmp_path / 'does_not_exist.txt'}\n")
    with pytest.raises(ConfigError):
        load_api_key(env_path=str(env_file))


def test_empty_key_file_raises_config_error(tmp_path):
    """A key file that exists but is blank raises ConfigError.

    An empty key would otherwise produce a confusing 401 from the API; failing
    here makes the misconfiguration obvious.
    """
    key_file = tmp_path / "key.txt"
    key_file.write_text("   \n")
    env_file = tmp_path / ".env"
    env_file.write_text(f"LLM_API_KEY_FILE={key_file}\n")
    with pytest.raises(ConfigError):
        load_api_key(env_path=str(env_file))

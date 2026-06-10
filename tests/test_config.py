"""Tests for API-key loading from a key-file path referenced by .env.

The security model: .env never contains the key itself, only LLM_API_KEY_FILE
pointing at a separate file (kept outside the repo) that holds the key. These
tests pin that indirection so a key is never accidentally read from .env
directly or leaked.
"""

import pytest

from gen3_metadata_simulator.config import load_api_key
from gen3_metadata_simulator.errors import ConfigError


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

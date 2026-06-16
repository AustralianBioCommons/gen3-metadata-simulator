"""Runtime configuration — the LLM provider, model, and key, read from ``.env``.

``.env`` holds three settings: the LLM ``LLM_PROVIDER`` (vendor — anthropic or
openai), the ``LLM_MODEL`` id, and ``LLM_API_KEY_FILE`` — a *path* to a separate
file (kept outside the repo) that contains the API key. The key itself is never
stored in ``.env`` or the repo; this module resolves the path indirection.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

from gen3_metadata_simulator.errors import ConfigError

logger = logging.getLogger(__name__)

PROVIDER_VAR = "LLM_PROVIDER"
MODEL_VAR = "LLM_MODEL"
KEY_FILE_VAR = "LLM_API_KEY_FILE"

VALID_PROVIDERS = ("anthropic", "openai")
DEFAULT_PROVIDER = "anthropic"

# The standard env var each vendor's SDK reads on its own when no api_key is
# passed. We let the SDK pick these up if no LLM_API_KEY_FILE is configured.
STANDARD_KEY_VAR = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


@dataclass
class LLMConfig:
    """Resolved LLM settings: which vendor, which model, and the API key.

    ``api_key`` is ``None`` when no key file is configured — in that case the
    vendor SDK reads its own standard env var (e.g. ``OPENAI_API_KEY``).
    """

    provider: str
    model: str
    api_key: Optional[str]


def _env(env_path: str) -> dict:
    values = dotenv_values(env_path) if Path(env_path).exists() else {}

    def get(var: str):
        return values.get(var) or os.environ.get(var)

    return get


def load_llm_config(
    env_path: str = ".env",
    provider_override: str | None = None,
    model_override: str | None = None,
) -> LLMConfig:
    """Resolve the LLM provider, model, and API key from ``.env`` (+ overrides).

    Precedence is override argument > ``.env`` > process environment. The
    provider defaults to ``anthropic`` when unset (so older single-variable
    ``.env`` files keep working).

    :raises ConfigError: on an unknown provider, a missing model, or a missing/
        empty key file.
    """
    get = _env(env_path)

    provider = (provider_override or get(PROVIDER_VAR) or DEFAULT_PROVIDER).lower()
    if provider not in VALID_PROVIDERS:
        raise ConfigError(
            f"{PROVIDER_VAR}={provider!r} is not supported. "
            f"Use one of: {', '.join(VALID_PROVIDERS)}."
        )

    model = model_override or get(MODEL_VAR)
    if not model:
        raise ConfigError(
            f"{MODEL_VAR} is not set. Add `{MODEL_VAR}=<model id>` to {env_path} "
            "(or pass --llm-model)."
        )

    api_key = _resolve_api_key(provider, get, env_path)
    logger.debug("LLM config: provider=%s model=%s", provider, model)
    return LLMConfig(provider=provider, model=model, api_key=api_key)


def _resolve_api_key(provider: str, get, env_path: str) -> Optional[str]:
    """Resolve the API key, or return None to let the vendor SDK read its own env var.

    Precedence: ``LLM_API_KEY_FILE`` (a path to the key) → otherwise, if the
    vendor's standard env var (e.g. ``OPENAI_API_KEY``) is present, return None
    so the SDK reads it. If neither is available, raise ConfigError.
    """
    key_file = get(KEY_FILE_VAR)
    if key_file:
        return _read_key_file(key_file, env_path)

    standard_var = STANDARD_KEY_VAR[provider]
    if os.environ.get(standard_var):
        logger.debug("No %s set; using %s from the environment", KEY_FILE_VAR, standard_var)
        return None

    raise ConfigError(
        f"No API key configured. Set {KEY_FILE_VAR}=/path/to/key in {env_path} "
        f"(an absolute path is best when installed), or export {standard_var}."
    )


def load_api_key(env_path: str = ".env", env_var: str = KEY_FILE_VAR) -> str:
    """Resolve just the API key from the file referenced by ``env_var``."""
    return _read_key_file(_env(env_path)(env_var), env_path)


def _read_key_file(key_file: str | None, env_path: str) -> str:
    """Read and return the stripped API key from ``key_file``.

    :raises ConfigError: if the path is unset, missing, or the file is empty.
    """
    if not key_file:
        raise ConfigError(
            f"{KEY_FILE_VAR} is not set. Add `{KEY_FILE_VAR}=/path/to/key` to "
            f"{env_path} (the file should contain your LLM API key)."
        )

    path = Path(key_file).expanduser()
    if not path.is_file():
        raise ConfigError(f"{KEY_FILE_VAR} points at {path}, which is not a readable file.")

    key = path.read_text().strip()
    if not key:
        raise ConfigError(f"The key file {path} is empty.")
    logger.debug("Loaded LLM API key from %s", path)  # never log the key itself
    return key

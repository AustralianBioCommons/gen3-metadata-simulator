"""Runtime configuration — loading the LLM API key indirectly via a key file.

The key is never stored in ``.env`` or the repo. ``.env`` holds only
``LLM_API_KEY_FILE``, a path to a separate file (kept outside the repo) that
contains the key. This module resolves that indirection and returns the key.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import dotenv_values

from gen3_metadata_simulator.errors import ConfigError

logger = logging.getLogger(__name__)

KEY_FILE_VAR = "LLM_API_KEY_FILE"


def load_api_key(env_path: str = ".env", env_var: str = KEY_FILE_VAR) -> str:
    """Resolve the LLM API key from the file referenced by ``env_var``.

    Looks up ``env_var`` first in ``env_path`` (if it exists), then in the
    process environment, reads the file it points to, and returns the stripped
    contents.

    :raises ConfigError: if the variable is unset, or the key file is missing or
        empty.
    """
    values = dotenv_values(env_path) if Path(env_path).exists() else {}
    key_file = values.get(env_var) or os.environ.get(env_var)
    if not key_file:
        raise ConfigError(
            f"{env_var} is not set. Add `{env_var}=/path/to/key` to {env_path} "
            "(the file should contain your LLM API key)."
        )

    path = Path(key_file).expanduser()
    if not path.is_file():
        raise ConfigError(f"{env_var} points at {path}, which is not a readable file.")

    key = path.read_text().strip()
    if not key:
        raise ConfigError(f"The key file {path} is empty.")
    logger.debug("Loaded LLM API key from %s", path)  # never log the key itself
    return key

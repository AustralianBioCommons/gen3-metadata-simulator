"""Custom exceptions for gen3-metadata-simulator."""


class Gen3SimulatorError(Exception):
    """Base class for all errors raised by this package."""


class InvalidGen3SchemaError(Gen3SimulatorError):
    """Raised when an input schema is not a usable Gen3 schema."""


class ConfigError(Gen3SimulatorError):
    """Raised when LLM/runtime configuration (e.g. the API key file) is missing or invalid."""


class MissingParentError(Gen3SimulatorError):
    """Raised when a link points at a parent node that has no generated records yet.

    This should never happen when nodes are generated in topological order; it
    surfaces a bug in the ordering logic rather than a user error.
    """


class ValidationFailedError(Gen3SimulatorError):
    """Raised when generated metadata fails self-validation against the schema."""

    def __init__(self, failures: list[dict]):
        self.failures = failures
        super().__init__(f"Generated metadata failed validation with {len(failures)} error(s)")

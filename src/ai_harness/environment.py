"""Read the one prescribed credential source; never load a .env file."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os

from .errors import EnvironmentValidationError


@dataclass(frozen=True, repr=False)
class ApiCredential:
    _value: str = field(repr=False)

    def __repr__(self) -> str:
        return "ApiCredential(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def reveal(self) -> str:
        """For a future adapter/redactor only; never serialize this object."""
        return self._value


@dataclass(frozen=True)
class RuntimeEnvironment:
    credential: ApiCredential = field(repr=False)
    credential_present: bool = True


def load_environment(env: Mapping[str, str] | None = None) -> RuntimeEnvironment:
    env = os.environ if env is None else env
    value = env.get("AI_API_KEY")
    if not isinstance(value, str) or not value.strip():
        raise EnvironmentValidationError(
            "AI_API_KEY is required in the process environment. "
            "No credential is read from configuration, CLI arguments, or .env files."
        )
    if len(value) > 8192 or not value.isprintable() or any(c.isspace() for c in value):
        raise EnvironmentValidationError(
            "AI_API_KEY must be a nonempty value without whitespace/control characters (maximum 8192 characters)."
        )
    return RuntimeEnvironment(ApiCredential(value))

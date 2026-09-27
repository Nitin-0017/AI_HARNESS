"""The only model contract imported by the controller; no provider imports."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .model_types import HealthCheck, ModelRequest, ModelResponse


@runtime_checkable
class ModelAdapter(Protocol):
    def generate(self, request: ModelRequest, *, before_attempt: Callable[[], None] | None = None) -> ModelResponse:
        """Return a validated response or raise a controlled ModelError.

        Invoke before_attempt immediately before EACH network/script attempt,
        including HTTP retries. Budget exceptions must propagate unchanged.
        """
        ...

    def health_check(self, *, live: bool = False, before_attempt: Callable[[], None] | None = None,
                     timeout_seconds: float | None = None) -> HealthCheck:
        """Local readiness by default. Only an explicit live probe sends a request."""
        ...

    def metadata(self) -> dict:
        """Return non-secret identity/capability data, never credential values."""
        ...

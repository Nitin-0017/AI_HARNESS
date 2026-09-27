"""Explicit development-only adapter. Never imported by the production factory."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace

from .model_types import (HealthCheck, ModelError, ModelRequest, ModelResponse, TokenUsage,
                          strict_json, validate_response)


class MockModelAdapter:
    def __init__(self, script: Iterable[ModelResponse | dict | str | ModelError]):
        self._script = iter(script)
        self.requests: list[ModelRequest] = []

    def metadata(self) -> dict:
        return {'provider': 'mock', 'model_id': 'scripted-development-only', 'is_mock': True,
                'network': False, 'max_actions_per_response': 1}

    def health_check(self, *, live: bool = False, before_attempt: Callable[[], None] | None = None,
                     timeout_seconds: float | None = None) -> HealthCheck:
        return HealthCheck('MOCK_ONLY', True, False, None)

    def generate(self, request: ModelRequest, *, before_attempt: Callable[[], None] | None = None) -> ModelResponse:
        if not isinstance(request, ModelRequest):
            raise ModelError('INVALID_REQUEST', 'generate requires a ModelRequest')
        if before_attempt is not None:
            before_attempt()
        self.requests.append(request)
        try:
            item = next(self._script)
        except StopIteration:
            raise ModelError('MOCK_EXHAUSTED', 'Scripted development responses are exhausted', attempts=1) from None
        if isinstance(item, ModelError):
            raise ModelError(item.code, str(item), retryable=item.retryable, attempts=1)
        try:
            if isinstance(item, str):
                item = strict_json(item)
            if isinstance(item, dict):
                usage = item.get('usage', {})
                item = ModelResponse(**{**item, 'usage': TokenUsage(**usage)})
            result = validate_response(item, request)
            return replace(result, raw_metadata={**result.raw_metadata, 'source': 'mock', 'attempts': 1})
        except ModelError as exc:
            exc.attempts = 1
            raise
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise ModelError('INVALID_RESPONSE', 'Malformed scripted model response', attempts=1) from exc

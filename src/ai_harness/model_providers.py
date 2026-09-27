"""Real DeepSeek/Qwen adapters; provider/HTTP details never enter the controller."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
import json
import os
import time

from .config import BudgetConfig, ModelConfig, validate_model_config
from .environment import load_environment
from .errors import ConfigurationError, EnvironmentValidationError
from .model import ModelAdapter
from .model_protocols import decode_response, encode_request, validate_protocol
from .model_transport import post_json
from .model_types import HealthCheck, ModelError, ModelMessage, ModelRequest, ModelResponse, json_copy, strict_json
from .telemetry import Redactor


class _HttpModelAdapter:
    PROVIDER = ''

    def __init__(self, config: ModelConfig, *, budgets: BudgetConfig | None = None,
                 env: Mapping[str, str] | None = None):
        self.config = config
        self.budgets = budgets or BudgetConfig()
        self._credential = None
        self._credential_error = None
        try:
            self._credential = load_environment(os.environ if env is None else env).credential
        except EnvironmentValidationError:
            self._credential_error = ModelError('CREDENTIAL_MISSING', 'A valid AI_API_KEY must be supplied through the environment')
        self._redactor = Redactor.from_environment(os.environ if env is None else env)

    def _validate(self) -> None:
        try:
            validate_model_config(self.config)
        except ConfigurationError as exc:
            raise ModelError('CONFIGURATION', str(exc)) from None
        if self.config.selected_provider != self.PROVIDER:
            raise ModelError('CONFIGURATION', 'Adapter provider does not match the selected provider')
        if not self.config.model_id or not self.config.endpoint:
            raise ModelError('CONFIGURATION', 'Exact model_id and endpoint are required; no endpoint or model is assumed')
        validate_protocol(self.config)
        if self._credential_error:
            raise self._credential_error
        # HTTP header values must be encodable; never print the value on failure.
        try:
            self._credential.reveal().encode('latin-1')
        except UnicodeError:
            raise ModelError('CONFIGURATION', 'AI_API_KEY cannot be encoded as an HTTP credential header') from None

    def metadata(self) -> dict:
        return self._redactor.clean({
            'provider': self.PROVIDER, 'model_id': self.config.model_id,
            'endpoint_configured': bool(self.config.endpoint),
            'request_format': self.config.request_format, 'response_format': self.config.response_format,
            'is_mock': False, 'streaming': False, 'max_actions_per_response': 1,
        })

    def generate(self, request: ModelRequest, *, before_attempt: Callable[[], None] | None = None) -> ModelResponse:
        self._validate()
        if not isinstance(request, ModelRequest):
            raise ModelError('INVALID_REQUEST', 'generate requires a ModelRequest')
        payload = self._redactor.clean(encode_request(self.config, request))
        payload = json_copy(payload)
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(body) > self.budgets.max_context_bytes:
            raise ModelError('REQUEST_LIMIT', 'Encoded model request exceeds max_context_bytes')
        credential = self._credential.reveal()
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'Connection': 'close', self.config.auth_header: ((self.config.auth_scheme + ' ') if self.config.auth_scheme else '') + credential}
        per_attempt = min(self.config.timeout_seconds or self.budgets.model_timeout_seconds,
                          self.budgets.model_timeout_seconds)
        retry_limit = min(self.config.max_retries if self.config.max_retries is not None else self.budgets.max_retries,
                          self.budgets.max_retries, 20)
        if request.max_retries is not None:
            retry_limit = min(retry_limit, request.max_retries)
        total_timeout = request.timeout_seconds if request.timeout_seconds is not None else per_attempt * (retry_limit + 1) + self.config.retry_backoff_seconds * (2**retry_limit - 1)
        deadline = time.monotonic() + total_timeout
        for attempt in range(retry_limit + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError('TIMEOUT', 'Model request deadline exhausted', attempts=attempt)
            if before_attempt is not None:
                before_attempt()
            try:
                raw, metadata = post_json(self.config.endpoint, body, headers,
                                          timeout=min(per_attempt, remaining), max_bytes=self.config.max_response_bytes)
                document = strict_json(raw, limit=self.config.max_response_bytes)
                response = decode_response(self.config, document, request)
                # No complete provider body, reasoning trace, headers or credential
                # are retained. Allowed identity/transport fields are bounded.
                response = replace(response, raw_metadata={**response.raw_metadata, **metadata,
                                   'provider': self.PROVIDER, 'attempts': attempt + 1})
                clean = self._redactor.clean(response.to_dict())
                from .model_types import TokenUsage
                return ModelResponse(**{**clean, 'usage': TokenUsage(**clean['usage'])})
            except ModelError as exc:
                exc.attempts = attempt + 1
                if not exc.retryable or attempt == retry_limit:
                    raise
                delay = min(self.config.retry_backoff_seconds * 2**attempt, 60.0)
                if time.monotonic() + delay >= deadline:
                    raise ModelError('TIMEOUT', 'Model request deadline exhausted before retry', attempts=attempt + 1) from None
                if delay:
                    time.sleep(delay)
        raise ModelError('INTERNAL', 'Unreachable adapter state')

    def health_check(self, *, live: bool = False, before_attempt: Callable[[], None] | None = None,
                     timeout_seconds: float | None = None) -> HealthCheck:
        try:
            self._validate()
        except ModelError as exc:
            return HealthCheck('BLOCKED', False, error=exc.to_dict())
        if not live:
            return HealthCheck('CONFIGURED_NOT_PROBED', True)
        try:
            response = self.generate(ModelRequest((ModelMessage('user', 'Reply with a short ready message. Do not request tools.'),),
                                                  max_tokens=64, max_retries=0,
                                                  timeout_seconds=timeout_seconds or self.budgets.max_seconds), before_attempt=before_attempt)
            valid = response.requested_action is None and response.finish_reason == 'stop'
            return HealthCheck('REACHABLE' if valid else 'INVALID_PROBE', True, True, valid, usage=response.usage)
        except ModelError as exc:
            return HealthCheck('UNAVAILABLE', True, exc.attempts > 0, False, error=exc.to_dict())


class DeepSeekAdapter(_HttpModelAdapter):
    PROVIDER = 'deepseek'


class QwenAdapter(_HttpModelAdapter):
    PROVIDER = 'qwen'


def create_model_adapter(config: ModelConfig, *, budgets: BudgetConfig | None = None,
                         env: Mapping[str, str] | None = None) -> ModelAdapter:
    """Production composition root. No mock selection or fallback is possible."""
    cls = {'deepseek': DeepSeekAdapter, 'qwen': QwenAdapter}.get(config.selected_provider)
    if cls is None:
        raise ModelError('CONFIGURATION', 'Select deepseek or qwen explicitly; mock is not a production provider')
    return cls(config, budgets=budgets, env=env)

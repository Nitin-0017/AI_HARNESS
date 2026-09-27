"""Provider-neutral request/response types and controlled model failures."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from typing import Any

from .errors import HarnessError


class ModelError(HarnessError):
    """Messages are deliberately static; provider bodies/credentials never appear."""
    def __init__(self, code: str, message: str, *, retryable: bool = False, attempts: int = 0):
        super().__init__(message)
        self.code, self.retryable, self.attempts = code, retryable, attempts

    def to_dict(self) -> dict:
        return {'code': self.code, 'message': str(self), 'retryable': self.retryable, 'attempts': self.attempts}


def invalid(message: str = 'Malformed model response') -> ModelError:
    return ModelError('INVALID_RESPONSE', message)


def json_copy(value: Any, *, limit: int = 1048576) -> Any:
    """Reject non-JSON objects, nonfinite numbers, deep nesting and oversized data."""
    def visit(item: Any, depth: int = 0) -> None:
        if depth > 32:
            raise invalid('Model data is nested too deeply')
        if isinstance(item, dict):
            if any(not isinstance(k, str) for k in item):
                raise invalid('Model JSON object keys must be strings')
            for v in item.values():
                visit(v, depth + 1)
        elif isinstance(item, (list, tuple)):
            for v in item:
                visit(v, depth + 1)
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise invalid('Model data must be JSON values')
    visit(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > limit:
            raise invalid('Model data exceeds the configured size limit')
        return json.loads(encoded)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise invalid('Model data is not bounded valid JSON') from exc


def strict_json(text: str | bytes, *, limit: int = 1048576) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise invalid('Duplicate JSON keys are not accepted')
            result[key] = value
        return result
    def constant(_):
        raise invalid('Nonfinite JSON numbers are not accepted')
    try:
        if isinstance(text, bytes):
            if len(text) > limit:
                raise invalid('Model response exceeds the configured size limit')
            text = text.decode('utf-8')
        if not isinstance(text, str) or len(text.encode('utf-8')) > limit:
            raise invalid('Model response exceeds the configured size limit')
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        return json_copy(data, limit=limit)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise invalid('Model response is not valid UTF-8 JSON') from exc


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str

    def __post_init__(self):
        if self.role not in {'system', 'user', 'assistant'} or not isinstance(self.content, str):
            raise ModelError('INVALID_REQUEST', 'Messages must contain a supported role and text')
        json_copy(self.content)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name or len(self.name) > 100 or not all(c.isalnum() or c == '_' for c in self.name):
            raise ModelError('INVALID_REQUEST', 'Invalid tool definition name')
        if not isinstance(self.description, str) or not isinstance(self.parameters, dict):
            raise ModelError('INVALID_REQUEST', 'Invalid tool definition')
        object.__setattr__(self, 'parameters', json_copy(self.parameters))


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None

    def __post_init__(self):
        if not isinstance(self.messages, (list, tuple)) or not self.messages or len(self.messages) > 128 or any(not isinstance(m, ModelMessage) for m in self.messages):
            raise ModelError('INVALID_REQUEST', 'A bounded nonempty message sequence is required')
        if not isinstance(self.tools, (list, tuple)) or len(self.tools) > 32 or any(not isinstance(t, ToolDefinition) for t in self.tools):
            raise ModelError('INVALID_REQUEST', 'Invalid tool definitions')
        if len({t.name for t in self.tools}) != len(self.tools):
            raise ModelError('INVALID_REQUEST', 'Tool names must be unique')
        for name in ('max_tokens', 'timeout_seconds', 'max_retries'):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                good = type(value) in ({int, float} if name == 'timeout_seconds' else {int}) and math.isfinite(value) and value >= (0 if name == 'max_retries' else 1e-12)
            except (TypeError, OverflowError):
                good = False
            if not good or (name == 'max_retries' and value > 20):
                raise ModelError('INVALID_REQUEST', 'Invalid request resource limit')
        object.__setattr__(self, 'messages', tuple(self.messages))
        object.__setattr__(self, 'tools', tuple(self.tools))
        json_copy(self.to_dict())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self):
        for value in asdict(self).values():
            if value is not None and (type(value) is not int or value < 0 or value > 10**12):
                raise invalid('Token usage must contain nonnegative integers or null')
        if self.total_tokens is not None and self.total_tokens < sum(v or 0 for v in (self.input_tokens, self.output_tokens)):
            raise invalid('Inconsistent token usage')

    @property
    def known(self) -> bool:
        return self.total_tokens is not None


FINISH_REASONS = {'stop', 'tool_call', 'length', 'refused', 'error', 'unknown'}


@dataclass(frozen=True)
class ModelResponse:
    message: str
    requested_action: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = 'stop'
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.message, str) or not isinstance(self.arguments, dict) or not isinstance(self.raw_metadata, dict):
            raise invalid('Response message, arguments or metadata has the wrong type')
        if not isinstance(self.finish_reason, str) or self.finish_reason not in FINISH_REASONS or not isinstance(self.usage, TokenUsage):
            raise invalid('Invalid finish reason or token usage')
        if self.requested_action is not None and (not isinstance(self.requested_action, str) or not self.requested_action):
            raise invalid('Requested action must be a nonempty string or null')
        if self.requested_action is None:
            if self.arguments or self.finish_reason == 'tool_call':
                raise invalid('Arguments/tool finish reason require an action')
            if not self.message and self.finish_reason == 'stop':
                raise invalid('Empty completed response')
        elif self.finish_reason != 'tool_call':
            raise invalid('Only a complete tool-call response may request an action')
        object.__setattr__(self, 'arguments', json_copy(self.arguments))
        object.__setattr__(self, 'raw_metadata', json_copy(self.raw_metadata, limit=16384))
        json_copy(self.to_dict())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HealthCheck:
    status: str
    configured: bool
    network_checked: bool = False
    response_valid: bool | None = None
    error: dict[str, Any] | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)

    def to_dict(self) -> dict:
        return asdict(self)


def validate_arguments(value: Any, schema: dict, depth: int = 0) -> None:
    """Validate the small JSON Schema subset used by the existing six tools."""
    if depth > 24:
        raise invalid('Tool arguments are nested too deeply')
    kind = schema.get('type')
    if isinstance(kind, list):
        for member in kind:
            try:
                validate_arguments(value, {**schema, 'type': member}, depth + 1)
                return
            except ModelError:
                pass
        raise invalid('Tool argument has an invalid type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int,
             'boolean': type(value) is bool, 'null': value is None,
             'number': type(value) in {int, float}}
    if kind not in valid or not valid[kind]:
        raise invalid('Tool argument has an invalid type')
    if 'enum' in schema and value not in schema['enum']:
        raise invalid('Tool argument is outside the allowed values')
    if kind == 'object':
        props = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value) or (schema.get('additionalProperties') is False and set(value) - set(props)):
            raise invalid('Missing or unknown tool argument')
        for key, child in value.items():
            if key in props:
                validate_arguments(child, props[key], depth + 1)
    if kind == 'array':
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 10000):
            raise invalid('Tool argument list has an invalid size')
        for child in value:
            validate_arguments(child, schema.get('items', {}), depth + 1)
    if kind == 'string' and (len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 1048576)):
        raise invalid('Tool argument string has an invalid size')
    if kind in {'integer', 'number'} and ('minimum' in schema and value < schema['minimum']):
        raise invalid('Tool argument is below its minimum')


def validate_response(response: ModelResponse, request: ModelRequest) -> ModelResponse:
    if not isinstance(response, ModelResponse):
        raise invalid('Adapter did not return a ModelResponse')
    # Reconstruct to catch mutation of nested containers by an external adapter.
    response = ModelResponse(**{**response.to_dict(), 'usage': TokenUsage(**asdict(response.usage))})
    if response.requested_action:
        definition = next((t for t in request.tools if t.name == response.requested_action), None)
        if definition is None:
            raise invalid('Model requested an unavailable tool')
        validate_arguments(response.arguments, definition.parameters)
    return response

"""Explicit, configurable HTTP codecs. Nothing selects a provider endpoint."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .config import ModelConfig
from .model_types import (ModelError, ModelRequest, ModelResponse, TokenUsage, invalid,
                          json_copy, strict_json, validate_response)


REQUEST_FORMATS = {'chat_completions', 'json_template'}
RESPONSE_FORMATS = {'chat_json', 'chat_tools', 'mapped_json'}
MAPPING_FIELDS = {'message', 'requested_action', 'arguments', 'finish_reason',
                  'input_tokens', 'output_tokens', 'total_tokens', 'response_id', 'returned_model'}
PLACEHOLDERS = {'model_id', 'messages', 'tools', 'temperature', 'max_tokens'}
JSON_INSTRUCTION = (
    'Return exactly one JSON object with message (string), requested_action '
    '(one available tool name or null), and arguments (object). '
    'Do not use markdown fences or claim a tool ran. Request at most one action. '
    'For a message without an action, use requested_action:null and arguments:{}.'
)


def validate_protocol(config: ModelConfig) -> None:
    if config.request_format not in REQUEST_FORMATS or config.response_format not in RESPONSE_FORMATS:
        raise ModelError('CONFIGURATION', 'Select an explicitly supported request_format and response_format; no format is inferred')
    if config.response_format == 'chat_tools' and config.request_format != 'chat_completions':
        raise ModelError('CONFIGURATION', 'chat_tools requires chat_completions requests')
    if config.request_format == 'json_template':
        if not isinstance(config.request_template, dict) or not config.request_template:
            raise ModelError('CONFIGURATION', 'json_template requires a nonempty request_template')
        used = set()
        def inspect(value):
            if isinstance(value, str) and ('{{' in value or '}}' in value):
                if not value.startswith('{{') or not value.endswith('}}') or value[2:-2] not in PLACEHOLDERS:
                    raise ModelError('CONFIGURATION', 'Templates support only whole-value named placeholders')
                used.add(value[2:-2])
            elif isinstance(value, dict):
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        inspect(config.request_template)
        if not {'model_id', 'messages', 'max_tokens'} <= used:
            raise ModelError('CONFIGURATION', 'Request template must include model_id, messages and max_tokens placeholders')
    elif config.request_template is not None:
        raise ModelError('CONFIGURATION', 'request_template is only used by json_template')
    if config.response_format == 'mapped_json':
        mapping = config.response_mapping
        if not mapping or set(mapping) - MAPPING_FIELDS or 'finish_reason' not in mapping or not ({'message', 'requested_action'} & set(mapping)):
            raise ModelError('CONFIGURATION', 'mapped_json requires supported response_mapping fields including finish_reason')
        for value in mapping.values():
            if not isinstance(value, str) or (value != '' and not value.startswith('/')):
                raise ModelError('CONFIGURATION', 'Response paths must be JSON pointers')
            for part in value.split('/')[1:]:
                rest = part.replace('~0', '').replace('~1', '')
                if '~' in rest:
                    raise ModelError('CONFIGURATION', 'Invalid JSON pointer escape')
    elif config.response_mapping is not None:
        raise ModelError('CONFIGURATION', 'response_mapping is only used by mapped_json')
    protected = {'model', 'messages', 'tools', 'stream', 'max_tokens', 'temperature'}
    if protected & set(config.extra_body):
        raise ModelError('CONFIGURATION', 'extra_body may not override controlled generation fields')
    if config.request_format == 'json_template' and config.extra_body:
        raise ModelError('CONFIGURATION', 'Put custom fields directly in request_template, not extra_body')


def encode_request(config: ModelConfig, request: ModelRequest) -> dict:
    validate_protocol(config)
    messages = [asdict(m) for m in request.messages]
    tools = [{'type': 'function', 'function': asdict(t)} for t in request.tools]
    # The generated JSON contract is an explicit chosen harness protocol, not
    # an inferred organizer API. Native tool mode never injects this contract.
    if config.response_format in {'chat_json', 'mapped_json'}:
        import json
        contract = JSON_INSTRUCTION
        if request.tools:
            contract += '\nAvailable tools: ' + json.dumps([asdict(t) for t in request.tools], ensure_ascii=False)
        messages.insert(0, {'role': 'system', 'content': contract})
    max_tokens = min(config.max_tokens, request.max_tokens or config.max_tokens)
    if config.request_format == 'chat_completions':
        body = {'model': config.model_id, 'messages': messages, 'max_tokens': max_tokens, 'stream': False}
        if config.temperature is not None:
            body['temperature'] = config.temperature
        if config.response_format == 'chat_tools' and tools:
            body['tools'] = tools
        body.update(json_copy(config.extra_body))
        return body
    context = {'model_id': config.model_id, 'messages': messages, 'tools': tools,
               'temperature': config.temperature, 'max_tokens': max_tokens}
    def substitute(value):
        if isinstance(value, str) and value.startswith('{{'):
            return json_copy(context[value[2:-2]])
        if isinstance(value, dict):
            return {key: substitute(child) for key, child in value.items()}
        if isinstance(value, list):
            return [substitute(child) for child in value]
        return value
    return substitute(config.request_template)


def pointer(document: Any, path: str) -> Any:
    current = document
    try:
        for part in path.split('/')[1:] if path else ():
            key = part.replace('~1', '/').replace('~0', '~')
            if isinstance(current, list):
                if not key.isascii() or not key.isdigit() or (len(key) > 1 and key[0] == '0'):
                    raise ValueError
                current = current[int(key)]
            elif isinstance(current, dict):
                current = current[key]
            else:
                raise ValueError
        return current
    except (KeyError, IndexError, ValueError, TypeError, OverflowError) as exc:
        raise invalid('Configured response field is missing or has the wrong structure') from exc


def _finish(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise invalid('Missing or invalid model finish reason')
    return {'tool_calls': 'tool_call', 'function_call': 'tool_call', 'content_filter': 'refused',
            'insufficient_system_resource': 'error'}.get(value, value if value in {'stop', 'tool_call', 'length', 'refused', 'error'} else 'unknown')


def _usage(value: Any) -> TokenUsage:
    if value is None:
        return TokenUsage()
    if not isinstance(value, dict):
        raise invalid('Usage must be an object or null')
    return TokenUsage(value.get('prompt_tokens'), value.get('completion_tokens'), value.get('total_tokens'))


def _bounded_metadata(document: dict) -> dict:
    result = {}
    for source, name in (('id', 'response_id'), ('model', 'returned_model')):
        if source in document:
            value = document[source]
            if not isinstance(value, str) or len(value) > 512:
                raise invalid('Invalid response identity metadata')
            result[name] = value
    return result


def decode_response(config: ModelConfig, document: Any, request: ModelRequest) -> ModelResponse:
    if not isinstance(document, dict) or document.get('error') is not None:
        raise invalid('API returned an error envelope or non-object response')
    if config.response_format == 'mapped_json':
        fields = {name: pointer(document, path) for name, path in config.response_mapping.items()}
        reason = _finish(fields['finish_reason'])
        message, action, arguments = fields.get('message', ''), fields.get('requested_action'), fields.get('arguments', {})
        if isinstance(arguments, str):
            arguments = strict_json(arguments, limit=config.max_response_bytes)
        if reason in {'length', 'refused', 'error', 'unknown'}:
            action, arguments = None, {}
        elif action:
            reason = 'tool_call'
        usage = TokenUsage(fields.get('input_tokens'), fields.get('output_tokens'), fields.get('total_tokens'))
        metadata = {key: fields[key] for key in ('response_id', 'returned_model') if key in fields}
        return validate_response(ModelResponse(message, action, arguments, reason, usage, metadata), request)
    choices = document.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise invalid('Exactly one complete model choice is required')
    choice = choices[0]
    reason = _finish(choice.get('finish_reason'))
    message = choice.get('message')
    if not isinstance(message, dict) or message.get('role', 'assistant') != 'assistant':
        raise invalid('Missing assistant message')
    content = message.get('content')
    if content is not None and not isinstance(content, str):
        raise invalid('Only text message content is supported')
    metadata = _bounded_metadata(document)
    metadata['provider_finish_reason'] = choice['finish_reason']
    usage = _usage(document.get('usage'))
    if message.get('refusal'):
        reason = 'refused'
    if reason in {'length', 'refused', 'error', 'unknown'}:
        return validate_response(ModelResponse(content or '', finish_reason=reason, usage=usage, raw_metadata=metadata), request)
    action, arguments = None, {}
    if config.response_format == 'chat_tools':
        calls = message.get('tool_calls')
        if calls is not None and not isinstance(calls, list):
            raise invalid('tool_calls must be an array')
        if calls:
            if len(calls) != 1 or not isinstance(calls[0], dict) or calls[0].get('type') != 'function':
                raise invalid('Exactly one function tool call per response is supported')
            call = calls[0]
            function = call.get('function')
            if not isinstance(function, dict) or not isinstance(function.get('arguments'), str):
                raise invalid('Invalid native tool call')
            if not isinstance(call.get('id'), str) or not call['id'] or len(call['id']) > 512:
                raise invalid('Missing native tool call ID')
            if reason != 'tool_call':
                raise invalid('Native tool call has an inconsistent finish reason')
            action, arguments = function.get('name'), strict_json(function['arguments'], limit=config.max_response_bytes)
            metadata['tool_call_id'] = call['id']
        return validate_response(ModelResponse(content or '', action, arguments, reason, usage, metadata), request)
    if message.get('tool_calls'):
        raise invalid('Native tool calls were returned for the selected JSON-content protocol')
    data = strict_json(content, limit=config.max_response_bytes)
    if not isinstance(data, dict) or set(data) - {'message', 'requested_action', 'arguments'}:
        raise invalid('JSON content must contain only message, requested_action and arguments')
    action, arguments = data.get('requested_action'), data.get('arguments', {})
    if action:
        reason = 'tool_call'
    return validate_response(ModelResponse(data.get('message', ''), action, arguments, reason, usage, metadata), request)

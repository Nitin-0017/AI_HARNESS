"""Local HTTP protocol fixture. Never a DeepSeek/Qwen service or quality test."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from ai_harness.config import ModelConfig
from ai_harness.model_types import ModelMessage, ModelRequest
from ai_harness.model_tools import repository_tool_definitions


def request():
    return ModelRequest((ModelMessage('user', 'Inspect calculator.py'),), repository_tool_definitions())


def json_response(action='read_file', arguments=None, message='Inspect the file', finish='stop', usage=True):
    payload = {'choices': [{'finish_reason': finish, 'message': {
        'role': 'assistant', 'content': json.dumps({'message': message, 'requested_action': action,
                    'arguments': ({'path': 'calculator.py'} if action else {}) if arguments is None else arguments})}}],
        'id': 'local-protocol-fixture', 'model': 'organizer-id'}
    if usage:
        payload['usage'] = {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}
    return payload


def native_response(action='read_file', arguments=None):
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'call-local', 'type': 'function', 'function': {'name': action,
                 'arguments': json.dumps({'path': 'calculator.py'} if arguments is None else arguments)}}]}}]}


class ModelServer:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []
        self.lock = threading.Lock()
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                with parent.lock:
                    parent.requests.append({'path': self.path, 'headers': dict(self.headers), 'body': json.loads(raw)})
                    response = parent.responses.popleft() if parent.responses else {'status': 500, 'body': {}}
                try:
                    time.sleep(response.get('delay', 0))
                    status = response.get('status', 200)
                    body = response.get('body', json_response())
                    body = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
                    self.send_response(status)
                    self.send_header('Content-Type', response.get('content_type', 'application/json'))
                    self.send_header('Content-Length', str(response.get('content_length', len(body))))
                    for key, value in response.get('headers', {}).items():
                        self.send_header(key, value)
                    self.end_headers()
                    if response.get('drip'):
                        for byte in body:
                            self.wfile.write(bytes([byte])); self.wfile.flush()
                            time.sleep(response['drip'])
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/explicit/provided-path'

    def config(self, provider='deepseek', **kwargs):
        return replace(ModelConfig(provider=provider, model_id='organizer-id', endpoint=self.endpoint,
                          request_format='chat_completions', response_format='chat_json',
                          max_retries=0, retry_backoff_seconds=0, timeout_seconds=2), **kwargs)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

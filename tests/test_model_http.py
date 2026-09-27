from dataclasses import replace
import json
import time
from unittest.mock import patch

from ai_harness.config import BudgetConfig
from ai_harness.errors import BudgetExceeded
from ai_harness.model_providers import DeepSeekAdapter, QwenAdapter
from ai_harness.model_protocols import decode_response, encode_request, pointer, validate_protocol
from ai_harness.model_types import ModelError, ModelRequest
from model_support import ModelServer, json_response, native_response, request
from support import FoundationTestCase


class HttpAdapterTests(FoundationTestCase):
    def server(self, *responses):
        server = ModelServer(list(responses))
        self.addCleanup(server.close)
        return server

    def test_both_providers_make_actual_http_requests(self):
        for cls, provider in [(DeepSeekAdapter, 'deepseek'), (QwenAdapter, 'qwen')]:
            with self.subTest(provider=provider):
                server = self.server({'body': json_response()})
                model = cls(server.config(provider, temperature=0.2, max_tokens=222), env=self.env)
                response = model.generate(request())
                self.assertEqual(response.requested_action, 'read_file')
                self.assertEqual(response.usage.total_tokens, 20)
                recorded = server.requests[0]
                self.assertEqual(recorded['path'], '/explicit/provided-path')
                self.assertEqual(recorded['headers']['Authorization'], 'Bearer ' + self.secret)
                self.assertEqual(recorded['body']['model'], 'organizer-id')
                self.assertEqual(recorded['body']['max_tokens'], 222)
                self.assertEqual(recorded['body']['temperature'], 0.2)
                self.assertFalse(recorded['body']['stream'])

    def test_temperature_omitted_when_not_configured(self):
        server = self.server({'body': json_response()})
        DeepSeekAdapter(server.config(), env=self.env).generate(request())
        self.assertNotIn('temperature', server.requests[0]['body'])

    def test_native_tools_decoded_and_schema_sent(self):
        server = self.server({'body': native_response()})
        response = QwenAdapter(server.config('qwen', response_format='chat_tools'), env=self.env).generate(request())
        self.assertEqual(response.arguments, {'path': 'calculator.py'})
        self.assertEqual(response.raw_metadata['tool_call_id'], 'call-local')
        self.assertEqual(len(server.requests[0]['body']['tools']), 6)

    def test_native_final_text_without_action(self):
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'Finished explaining'}}]}
        server = self.server({'body': payload})
        result = QwenAdapter(server.config('qwen', response_format='chat_tools'), env=self.env).generate(request())
        self.assertIsNone(result.requested_action)
        self.assertEqual(result.message, 'Finished explaining')

    def test_multiple_tool_calls_not_silently_dropped(self):
        payload = native_response()
        payload['choices'][0]['message']['tool_calls'] *= 2
        server = self.server({'body': payload})
        with self.assertRaises(ModelError):
            QwenAdapter(server.config('qwen', response_format='chat_tools'), env=self.env).generate(request())

    def test_truncated_response_cannot_execute_an_action(self):
        server = self.server({'body': json_response(finish='length')})
        result = DeepSeekAdapter(server.config(), env=self.env).generate(request())
        self.assertIsNone(result.requested_action)
        self.assertEqual(result.finish_reason, 'length')

    def test_refusal_is_not_successful_action(self):
        payload = native_response(); payload['choices'][0]['message']['refusal'] = 'Not allowed'
        server = self.server({'body': payload})
        result = QwenAdapter(server.config('qwen', response_format='chat_tools'), env=self.env).generate(request())
        self.assertEqual(result.finish_reason, 'refused')
        self.assertIsNone(result.requested_action)

    def test_missing_usage_is_unknown_not_zero(self):
        server = self.server({'body': json_response(usage=False)})
        result = DeepSeekAdapter(server.config(), env=self.env).generate(request())
        self.assertIsNone(result.usage.total_tokens)

    def test_malformed_json_is_not_retried(self):
        server = self.server({'body': b'{broken'})
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(server.config(max_retries=2), env=self.env).generate(request())
        self.assertEqual(failure.exception.code, 'INVALID_RESPONSE')
        self.assertEqual(len(server.requests), 1)

    def test_invalid_shapes_are_controlled(self):
        for payload in [[], {}, {'choices': []}, {'choices': [1]}, {'choices': [{'message': {}}]},
                        {'error': {'message': self.secret}}, {'choices': [{'finish_reason': 'stop', 'message': {'content': 2}}]}]:
            with self.subTest(payload_type=type(payload).__name__):
                server = self.server({'body': payload})
                with self.assertRaises(ModelError) as failure:
                    DeepSeekAdapter(server.config(), env=self.env).generate(request())
                self.assertNotIn(self.secret, str(failure.exception))

    def test_invalid_native_arguments_are_controlled(self):
        payload = native_response(); payload['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = 'not json'
        server = self.server({'body': payload})
        with self.assertRaises(ModelError):
            DeepSeekAdapter(server.config(response_format='chat_tools'), env=self.env).generate(request())

    def test_response_usage_type_validated(self):
        payload = json_response(); payload['usage']['total_tokens'] = -1
        server = self.server({'body': payload})
        with self.assertRaises(ModelError):
            DeepSeekAdapter(server.config(), env=self.env).generate(request())

    def test_rate_limit_retries_are_real_and_counted(self):
        server = self.server({'status': 429}, {'body': json_response()})
        counts = []
        result = DeepSeekAdapter(server.config(max_retries=1), env=self.env).generate(request(), before_attempt=lambda: counts.append(1))
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(len(counts), 2)
        self.assertEqual(result.raw_metadata['attempts'], 2)

    def test_transient_server_failure_retry_limit(self):
        server = self.server({'status': 503}, {'status': 503}, {'status': 503})
        with self.assertRaises(ModelError) as error:
            DeepSeekAdapter(server.config(max_retries=1), env=self.env).generate(request())
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(error.exception.attempts, 2)

    def test_auth_failure_never_retries_or_echoes_body(self):
        for status in (401, 403, 400):
            with self.subTest(status=status):
                server = self.server({'status': status, 'body': {'secret': self.secret}})
                with self.assertRaises(ModelError) as failure:
                    DeepSeekAdapter(server.config(max_retries=2), env=self.env).generate(request())
                self.assertEqual(len(server.requests), 1)
                self.assertNotIn(self.secret, str(failure.exception))

    def test_redirect_does_not_forward_credential(self):
        destination = self.server({'body': json_response()})
        server = self.server({'status': 307, 'headers': {'Location': destination.endpoint}})
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(server.config(), env=self.env).generate(request())
        self.assertEqual(failure.exception.code, 'REDIRECT_REJECTED')
        self.assertEqual(destination.requests, [])

    def test_timeout_is_actual_not_simulated_success(self):
        server = self.server({'delay': 0.25, 'body': json_response()})
        start = time.monotonic()
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(server.config(timeout_seconds=0.04), env=self.env).generate(request())
        self.assertEqual(failure.exception.code, 'TIMEOUT')
        self.assertLess(time.monotonic() - start, 1)

    def test_slow_body_deadline(self):
        server = self.server({'body': json_response(), 'drip': 0.01})
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(server.config(timeout_seconds=0.05), env=self.env).generate(request())
        self.assertEqual(failure.exception.code, 'TIMEOUT')

    def test_retry_is_capped_by_global_budget(self):
        server = self.server({'status': 503}, {'body': json_response()})
        with self.assertRaises(ModelError):
            DeepSeekAdapter(server.config(max_retries=2), budgets=BudgetConfig(max_retries=0), env=self.env).generate(request())
        self.assertEqual(len(server.requests), 1)

    def test_budget_callback_prevents_retry(self):
        server = self.server({'status': 503}, {'body': json_response()})
        counter = []
        def before():
            if counter:
                raise BudgetExceeded('stop')
            counter.append(1)
        with self.assertRaises(BudgetExceeded):
            DeepSeekAdapter(server.config(max_retries=1), env=self.env).generate(request(), before_attempt=before)
        self.assertEqual(len(server.requests), 1)

    def test_oversized_body_rejected(self):
        server = self.server({'body': b' ' * 500})
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(server.config(max_response_bytes=100), env=self.env).generate(request())
        self.assertEqual(failure.exception.code, 'INVALID_RESPONSE')

    def test_non_json_response_rejected(self):
        server = self.server({'body': b'error', 'content_type': 'text/html'})
        with self.assertRaises(ModelError):
            DeepSeekAdapter(server.config(), env=self.env).generate(request())

    def test_response_redacts_credential_and_drops_raw_reasoning(self):
        payload = json_response(message=self.secret)
        payload['choices'][0]['message']['reasoning_content'] = self.secret
        server = self.server({'body': payload})
        response = DeepSeekAdapter(server.config(), env=self.env).generate(request())
        self.assertNotIn(self.secret, json.dumps(response.to_dict()))
        self.assertNotIn('reasoning_content', response.raw_metadata)

    def test_outbound_context_redacts_key(self):
        from ai_harness.model_types import ModelMessage
        server = self.server({'body': json_response()})
        req = replace(request(), messages=(ModelMessage('user', 'accidental ' + self.secret),))
        DeepSeekAdapter(server.config(), env=self.env).generate(req)
        self.assertNotIn(self.secret, json.dumps(server.requests[0]['body']))

    def test_health_default_is_config_only(self):
        server = self.server()
        result = DeepSeekAdapter(server.config(), env=self.env).health_check()
        self.assertEqual(result.status, 'CONFIGURED_NOT_PROBED')
        self.assertIsNone(result.response_valid)
        self.assertFalse(result.network_checked)
        self.assertEqual(server.requests, [])

    def test_live_health_uses_real_post_no_invented_health_path(self):
        server = self.server({'body': json_response(action=None, message='ready')})
        result = QwenAdapter(server.config('qwen'), env=self.env).health_check(live=True)
        self.assertTrue(result.network_checked)
        self.assertTrue(result.response_valid)
        self.assertEqual(result.status, 'REACHABLE')
        self.assertEqual(server.requests[0]['path'], '/explicit/provided-path')

    def test_failed_live_health_reports_failure(self):
        server = self.server({'status': 401})
        result = DeepSeekAdapter(server.config(), env=self.env).health_check(live=True)
        self.assertFalse(result.response_valid)
        self.assertEqual(result.status, 'UNAVAILABLE')

    def test_custom_template_and_json_pointer_response(self):
        server = self.server({'body': {'reply': {'text': 'read it', 'action': 'read_file', 'args': {'path': 'calculator.py'}, 'finish': 'stop'},
                                        'counts': {'input': 5, 'output': 2, 'total': 7}}})
        config = server.config(request_format='json_template', response_format='mapped_json',
            request_template={'engine': '{{model_id}}', 'input': {'conversation': '{{messages}}'},
                              'options': {'limit': '{{max_tokens}}', 'temperature': '{{temperature}}'}},
            response_mapping={'message': '/reply/text', 'requested_action': '/reply/action', 'arguments': '/reply/args',
                              'finish_reason': '/reply/finish', 'input_tokens': '/counts/input',
                              'output_tokens': '/counts/output', 'total_tokens': '/counts/total'})
        result = DeepSeekAdapter(config, env=self.env).generate(request())
        self.assertEqual(result.requested_action, 'read_file')
        self.assertEqual(result.usage.total_tokens, 7)
        body = server.requests[0]['body']
        self.assertEqual(body['engine'], 'organizer-id')
        self.assertIsInstance(body['input']['conversation'], list)
        self.assertNotIn('messages', body)

    def test_configurable_credential_header(self):
        server = self.server({'body': json_response()})
        DeepSeekAdapter(server.config(auth_header='X-API-Key', auth_scheme=''), env=self.env).generate(request())
        self.assertEqual(server.requests[0]['headers']['X-API-Key'], self.secret)
        self.assertNotIn('Authorization', server.requests[0]['headers'])

    def test_provider_options_are_only_sent_when_explicit(self):
        server = self.server({'body': json_response()})
        QwenAdapter(server.config('qwen', extra_body={'enable_thinking': False}), env=self.env).generate(request())
        self.assertFalse(server.requests[0]['body']['enable_thinking'])

    def test_bad_template_placeholder_rejected_before_network(self):
        server = self.server()
        config = server.config(request_format='json_template', request_template={'model': '{{unknown}}'})
        with self.assertRaises(ModelError):
            DeepSeekAdapter(config, env=self.env).generate(request())
        self.assertEqual(server.requests, [])

    def test_missing_mapped_response_path_is_controlled(self):
        server = self.server({'body': {}})
        config = server.config(response_format='mapped_json', response_mapping={'message': '/missing', 'finish_reason': '/finish'})
        with self.assertRaises(ModelError):
            DeepSeekAdapter(config, env=self.env).generate(request())

    def test_json_pointer_escapes_and_array_indices(self):
        self.assertEqual(pointer({'a/b': [{'~': 7}]}, '/a~1b/0/~0'), 7)
        with self.assertRaises(ModelError):
            pointer({'a': []}, '/a/-1')

    def test_network_error_is_controlled(self):
        server = self.server()
        config = server.config(timeout_seconds=0.1)
        server.close()
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(config, env=self.env).generate(request())
        self.assertEqual(failure.exception.code, 'NETWORK_ERROR')

    def test_retries_obey_request_deadline(self):
        server = self.server({'status': 503}, {'body': json_response()})
        req = replace(request(), timeout_seconds=0.03)
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(server.config(max_retries=1, retry_backoff_seconds=0.1), env=self.env).generate(req)
        self.assertEqual(failure.exception.code, 'TIMEOUT')
        self.assertEqual(len(server.requests), 1)

    def test_declared_incomplete_body_is_rejected(self):
        server = self.server({'body': b'{}', 'content_length': 15})
        with self.assertRaises(ModelError) as failure:
            DeepSeekAdapter(server.config(), env=self.env).generate(request())
        self.assertEqual(failure.exception.code, 'INVALID_RESPONSE')

    def test_json_duplicate_actions_are_rejected(self):
        payload = json_response()
        payload['choices'][0]['message']['content'] = '{"message":"x","requested_action":"read_file","requested_action":"apply_patch","arguments":{}}'
        server = self.server({'body': payload})
        with self.assertRaises(ModelError):
            DeepSeekAdapter(server.config(), env=self.env).generate(request())

    def test_extra_reasoning_is_not_a_tool_argument(self):
        payload = native_response()
        payload['choices'][0]['message']['reasoning_content'] = 'not part of the tool protocol'
        server = self.server({'body': payload})
        result = QwenAdapter(server.config('qwen', response_format='chat_tools'), env=self.env).generate(request())
        self.assertEqual(result.arguments, {'path': 'calculator.py'})
        self.assertNotIn('reasoning_content', json.dumps(result.to_dict()))

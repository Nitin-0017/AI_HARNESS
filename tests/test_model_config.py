from dataclasses import replace
import json
from unittest.mock import patch

from ai_harness.config import ModelConfig, load_config, validate_model_config
from ai_harness.errors import ConfigurationError
from ai_harness.model_providers import DeepSeekAdapter, QwenAdapter, create_model_adapter
from ai_harness.model_protocols import validate_protocol
from ai_harness.model_types import ModelError
from model_support import ModelServer, request
from support import FoundationTestCase


class ModelConfigurationTests(FoundationTestCase):
    def load(self, **kwargs):
        return load_config(self.project, env=kwargs.pop('env', {}), cwd=self.base, **kwargs)

    def test_no_endpoint_or_protocol_is_invented(self):
        config = self.load()
        self.assertIsNone(config.model.endpoint)
        self.assertIsNone(config.model.request_format)
        self.assertIsNone(config.model.response_format)
        self.assertIsNone(config.model.selected_provider)

    def test_numeric_and_provider_environment(self):
        config = self.load(env={'AI_PROVIDER': 'qwen', 'AI_TEMPERATURE': '0.4', 'AI_MAX_TOKENS': '700',
            'AI_TIMEOUT_SECONDS': '3.5', 'AI_MAX_RETRIES': '1', 'AI_RETRY_BACKOFF_SECONDS': '0.2', 'AI_MAX_RESPONSE_BYTES': '8000'})
        self.assertEqual((config.model.selected_provider, config.model.temperature, config.model.max_tokens,
                          config.model.timeout_seconds, config.model.max_retries), ('qwen', .4, 700, 3.5, 1))

    def test_all_config_sources_and_alias_precedence(self):
        self.config_file('[model]\nfamily="deepseek"\ntemperature=0.1\nmax_tokens=100\n')
        config = self.load(env={'AI_PROVIDER': 'qwen', 'AI_TEMPERATURE': '0.2'},
                           overrides={'model': {'temperature': 0.3}})
        self.assertEqual(config.model.selected_provider, 'qwen')
        self.assertEqual(config.model.temperature, 0.3)
        self.assertEqual(config.model.max_tokens, 100)

    def test_legacy_alias_still_works(self):
        self.assertEqual(self.load(env={'AI_MODEL_FAMILY': 'deepseek'}).model.selected_provider, 'deepseek')

    def test_conflicting_same_layer_aliases_rejected(self):
        with self.assertRaises(ConfigurationError):
            self.load(env={'AI_PROVIDER': 'qwen', 'AI_MODEL_FAMILY': 'deepseek'})

    def test_typed_numeric_validation(self):
        for name, value in [('temperature', float('nan')), ('temperature', True), ('temperature', -1),
                            ('max_tokens', 0), ('max_tokens', 1.5), ('max_retries', -1), ('max_retries', 21),
                            ('timeout_seconds', 0), ('max_response_bytes', 0), ('retry_backoff_seconds', -1)]:
            with self.subTest(name=name, value=value), self.assertRaises(ConfigurationError):
                validate_model_config(replace(ModelConfig(), **{name: value}))

    def test_bad_environment_numeric_is_controlled(self):
        with self.assertRaises(ConfigurationError):
            self.load(env={'AI_MAX_TOKENS': 'many'})

    def test_credentials_rejected_in_nested_templates(self):
        for name in ('request_template', 'extra_body'):
            with self.subTest(name=name), self.assertRaises(ConfigurationError):
                validate_model_config(replace(ModelConfig(), **{name: {'nested': {'api_key': self.secret}}}))

    def test_template_and_mapping_not_serialized_in_public_config(self):
        cfg = replace(self.load(), model=replace(ModelConfig(), request_template={'x': 'private'}, response_mapping={'message': '/x'}))
        text = json.dumps(cfg.public_dict())
        self.assertNotIn('private', text)
        self.assertTrue(cfg.public_dict()['model']['request_template_configured'])

    def test_auth_header_injection_rejected(self):
        for value in ('Host', 'Content-Length', 'Authorization\r\nX-Evil', '', 'has space'):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                validate_model_config(replace(ModelConfig(), auth_header=value))

    def test_invalid_auth_scheme_rejected(self):
        with self.assertRaises(ConfigurationError):
            validate_model_config(replace(ModelConfig(), auth_scheme='Bearer\nBad'))

    def test_real_adapters_missing_configuration_do_not_connect(self):
        with patch('ai_harness.model_providers.post_json') as post:
            for cls, provider in [(DeepSeekAdapter, 'deepseek'), (QwenAdapter, 'qwen')]:
                model = cls(ModelConfig(provider=provider), env=self.env)
                self.assertFalse(model.health_check().configured)
                with self.assertRaises(ModelError):
                    model.generate(request())
            post.assert_not_called()

    def test_missing_key_fails_without_network(self):
        config = ModelConfig(provider='qwen', model_id='provided', endpoint='https://example.invalid/provided',
                             request_format='chat_completions', response_format='chat_json')
        model = QwenAdapter(config, env={})
        with patch('ai_harness.model_providers.post_json') as post:
            self.assertFalse(model.health_check(live=True).configured)
            with self.assertRaises(ModelError) as failure:
                model.generate(request())
            self.assertEqual(failure.exception.code, 'CREDENTIAL_MISSING')
            post.assert_not_called()

    def test_protocol_not_selected_is_not_assumed(self):
        model = DeepSeekAdapter(ModelConfig(provider='deepseek', model_id='provided', endpoint='https://example.invalid/x'), env=self.env)
        self.assertEqual(model.health_check().status, 'BLOCKED')
        with self.assertRaises(ModelError):
            model.generate(request())

    def test_factory_never_selects_mock(self):
        for provider in (None, 'mock', 'other'):
            with self.subTest(provider=provider), self.assertRaises(ModelError):
                create_model_adapter(ModelConfig(provider=provider), env=self.env)

    def test_provider_mismatch_fails_honestly(self):
        model = DeepSeekAdapter(ModelConfig(provider='qwen'), env=self.env)
        self.assertFalse(model.health_check().configured)

    def test_metadata_never_contains_key_or_endpoint(self):
        model = DeepSeekAdapter(ModelConfig(provider='deepseek', endpoint='https://example.invalid/x'), env=self.env)
        text = json.dumps(model.metadata()) + repr(model)
        self.assertNotIn(self.secret, text)
        self.assertNotIn('https://', text)

    def test_generation_settings_cannot_be_overridden_in_extra_body(self):
        for name in ('model', 'messages', 'tools', 'stream', 'max_tokens'):
            with self.subTest(name=name), self.assertRaises(ModelError):
                validate_protocol(ModelConfig(request_format='chat_completions', response_format='chat_json', extra_body={name: 'override'}))

    def test_unknown_formats_preserved_but_fail_at_integration(self):
        cfg = self.load(env={'AI_REQUEST_FORMAT': 'organizer-future-format'})
        self.assertEqual(cfg.model.request_format, 'organizer-future-format')
        with self.assertRaises(ModelError):
            validate_protocol(cfg.model)

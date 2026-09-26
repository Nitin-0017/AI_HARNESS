from dataclasses import fields, replace
import json
from pathlib import Path

from ai_harness.config import AppConfig, BudgetConfig, ModelConfig, load_config, validate_config
from ai_harness.errors import ConfigurationError
from support import FoundationTestCase


class ConfigurationTests(FoundationTestCase):
    def load(self, **kwargs):
        defaults = dict(env={}, cwd=self.base)
        defaults.update(kwargs)
        return load_config(self.project, **defaults)

    def test_defaults_do_not_assume_a_model_or_workspace(self):
        config = self.load()
        self.assertIsNone(config.workspace)
        self.assertEqual(config.model, ModelConfig())
        self.assertEqual(config.output_dir, self.project / ".runs")
        self.assertIsNone(config.config_file)

    def test_bundled_configuration_loads(self):
        from support import PROJECT
        config = load_config(PROJECT, env={})
        self.assertEqual(config.budgets.max_iterations, 30)
        self.assertIsNone(config.workspace)
        self.assertIsNone(config.model.endpoint)

    def test_toml_is_typed(self):
        self.config_file('[budgets]\nmax_iterations = 8\nmax_seconds = 2.5\nmax_retries = 0\n')
        config = self.load()
        self.assertEqual(config.budgets.max_iterations, 8)
        self.assertEqual(config.budgets.max_seconds, 2.5)
        self.assertEqual(config.budgets.max_retries, 0)

    def test_cli_beats_environment_beats_toml(self):
        self.config_file('[budgets]\nmax_iterations = 8\n[model]\nfamily="deepseek"\n')
        config = self.load(env={"HARNESS_MAX_ITERATIONS": "12", "AI_MODEL_FAMILY": "qwen"},
                           overrides={"budgets": {"max_iterations": 3}})
        self.assertEqual(config.budgets.max_iterations, 3)
        self.assertEqual(config.model.family, "qwen")

    def test_all_budget_environment_variables_are_supported(self):
        env = {"HARNESS_" + f.name.upper(): "7" for f in fields(BudgetConfig)}
        config = self.load(env=env)
        for item in fields(BudgetConfig):
            self.assertEqual(getattr(config.budgets, item.name), 7)

    def test_config_paths_are_relative_to_file_not_cwd(self):
        file = self.config_file('[workspace]\npath="../target"\n[logging]\noutput_dir="local-runs"\n')
        config = self.load(config_path=file, cwd=self.target)
        self.assertEqual(config.workspace.resolve(), self.target)
        self.assertEqual(config.output_dir, self.project / "local-runs")

    def test_environment_and_cli_paths_are_relative_to_invocation(self):
        config = self.load(env={"HARNESS_WORKSPACE": "target"}, overrides={"output_dir": "logs"})
        self.assertEqual(config.workspace, self.target)
        self.assertEqual(config.output_dir, self.base / "logs")

    def test_env_config_can_be_overridden_by_cli(self):
        file = self.config_file('[budgets]\nmax_iterations=4\n')
        config = self.load(config_path=file, env={"HARNESS_CONFIG": str(self.base / "missing.toml")})
        self.assertEqual(config.budgets.max_iterations, 4)

    def test_never_autoload_target_configuration(self):
        (self.target / "harness.toml").write_text('[budgets]\nmax_iterations=1\n')
        config = self.load(cwd=self.target)
        self.assertEqual(config.budgets.max_iterations, 30)
        self.assertIsNone(config.config_file)

    def test_missing_explicit_config_is_an_error(self):
        with self.assertRaises(ConfigurationError):
            self.load(config_path=self.base / "missing.toml")

    def test_directory_config_is_an_error(self):
        with self.assertRaises(ConfigurationError):
            self.load(config_path=self.target)

    def test_large_config_is_rejected(self):
        file = self.config_file("#" + "x" * 128001)
        with self.assertRaises(ConfigurationError):
            self.load(config_path=file)

    def test_invalid_toml_does_not_echo_its_values(self):
        self.config_file(f'key = "{self.secret}\n')
        with self.assertRaises(ConfigurationError) as error:
            self.load()
        self.assertNotIn(self.secret, str(error.exception))

    def test_unknown_sections_keys_and_credentials_are_rejected(self):
        for text in ('[modle]\n', '[model]\napi_key="forbidden"\n',
                     'AI_API_KEY="forbidden"\n', '[budgets]\nmax_itertions=2\n',
                     '[workspace]\nunknown=1\n', '[logging]\npassword="forbidden"\n'):
            with self.subTest(text=text):
                self.config_file(text)
                with self.assertRaises(ConfigurationError):
                    self.load()

    def test_invalid_table_types_are_rejected(self):
        for text in ('model=[]', 'budgets="wrong"', 'workspace=true', 'logging=2'):
            with self.subTest(text=text):
                self.config_file(text)
                with self.assertRaises(ConfigurationError):
                    self.load()

    def test_invalid_budget_types_and_ranges_are_rejected(self):
        for value in (True, -1, 0, 1.5, "3", float("nan"), float("inf"), 10**1000):
            with self.subTest(value_type=type(value).__name__):
                cfg = replace(self.load(), budgets=replace(BudgetConfig(), max_iterations=value))
                with self.assertRaises(ConfigurationError):
                    validate_config(cfg)

    def test_positive_fractional_timeouts_are_allowed(self):
        cfg = replace(self.load(), budgets=replace(BudgetConfig(), max_seconds=0.5))
        validate_config(cfg)

    def test_invalid_numeric_environment_is_rejected(self):
        for value in ("", "many", "nan", "1.5"):
            with self.subTest(value=value):
                with self.assertRaises(ConfigurationError):
                    self.load(env={"HARNESS_MAX_ITERATIONS": value})

    def test_nonfinite_seconds_environment_is_rejected(self):
        for value in ("nan", "inf", "-inf"):
            with self.assertRaises(ConfigurationError):
                self.load(env={"HARNESS_MAX_SECONDS": value})

    def test_invalid_logging_levels_are_rejected(self):
        for level in ("TRACE", [], 1, True, None):
            with self.subTest(level=level):
                with self.assertRaises(ConfigurationError):
                    validate_config(replace(self.load(), log_level=level))

    def test_invalid_model_types_are_rejected(self):
        for value in ([], {}, True, 1, "", "line\nbreak"):
            with self.subTest(value=value):
                with self.assertRaises(ConfigurationError):
                    validate_config(replace(self.load(), model=ModelConfig(model_id=value)))

    def test_only_selected_model_families_are_allowed(self):
        for family in ("deepseek", "qwen"):
            config = self.load(env={"AI_MODEL_FAMILY": family})
            self.assertEqual(config.model.family, family)
        with self.assertRaises(ConfigurationError):
            self.load(env={"AI_MODEL_FAMILY": "other"})

    def test_model_formats_are_not_invented(self):
        config = self.load(env={"AI_MODEL_ID": "organizer-model", "AI_REQUEST_FORMAT": "provided-protocol"})
        self.assertEqual(config.model.request_format, "provided-protocol")
        self.assertIsNone(config.model.response_format)
        self.assertIsNone(config.model.endpoint)

    def test_unsafe_or_malformed_endpoints_are_rejected(self):
        for endpoint in ("not-a-url", "http://example.invalid/api", "https://name:pass@example.invalid/api",
                         "https://example.invalid/api?key=value", "https://example.invalid/#fragment",
                         "https://example.invalid:wrong", "https://exa mple.invalid", "https://[invalid"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ConfigurationError):
                    self.load(env={"AI_API_ENDPOINT": endpoint})

    def test_https_and_loopback_endpoints_are_accepted_but_not_contacted(self):
        for endpoint in ("https://example.invalid/api", "http://127.0.0.1:9000/api", "http://[::1]:9000/api"):
            config = self.load(env={"AI_API_ENDPOINT": endpoint})
            self.assertEqual(config.model.endpoint, endpoint)

    def test_public_config_never_contains_api_key_or_endpoint_value(self):
        config = self.load(env={**self.env, "AI_API_ENDPOINT": "https://example.invalid/api"})
        serialized = json.dumps(config.public_dict())
        self.assertNotIn(self.secret, serialized)
        self.assertNotIn("https://example.invalid/api", serialized)
        self.assertTrue(config.public_dict()["model"]["endpoint_configured"])
        self.assertFalse(hasattr(config, "api_key"))

    def test_blank_paths_are_rejected(self):
        for name in ("HARNESS_WORKSPACE", "HARNESS_OUTPUT_DIR", "HARNESS_CONFIG"):
            with self.assertRaises(ConfigurationError):
                self.load(env={name: ""})

    def test_unknown_programmatic_overrides_are_rejected(self):
        with self.assertRaises(ConfigurationError):
            self.load(overrides={"api_key": self.secret})
        with self.assertRaises(ConfigurationError):
            self.load(overrides={"model": {"api_key": self.secret}})

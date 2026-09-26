from ai_harness.environment import load_environment
from ai_harness.errors import EnvironmentValidationError
from support import FoundationTestCase


class EnvironmentTests(FoundationTestCase):
    def test_reads_only_ai_api_key(self):
        result = load_environment(self.env)
        self.assertEqual(result.credential.reveal(), self.secret)
        self.assertTrue(result.credential_present)

    def test_missing_key_is_rejected(self):
        with self.assertRaises(EnvironmentValidationError):
            load_environment({})

    def test_other_provider_keys_are_not_a_fallback(self):
        with self.assertRaises(EnvironmentValidationError):
            load_environment({"DEEPSEEK_API_KEY": self.secret, "QWEN_API_KEY": self.secret})

    def test_empty_whitespace_or_invalid_keys_are_rejected(self):
        for value in ("", " ", "\n", self.secret + "\n", self.secret + " ", self.secret + "\0", "x" * 8193):
            with self.subTest(length=len(value)):
                with self.assertRaises(EnvironmentValidationError) as error:
                    load_environment({"AI_API_KEY": value})
                self.assertNotIn(self.secret, str(error.exception))

    def test_nonstring_is_rejected_by_validator(self):
        with self.assertRaises(EnvironmentValidationError):
            load_environment({"AI_API_KEY": None})

    def test_repr_and_str_do_not_disclose_credential(self):
        result = load_environment(self.env)
        for value in (repr(result), repr(result.credential), str(result.credential)):
            self.assertNotIn(self.secret, value)

    def test_dotenv_is_not_loaded(self):
        (self.project / ".env").write_text("AI_API_KEY=" + self.secret + "\n")
        code, out, err = self.cli(env={})
        self.assertEqual(code, 2)
        self.assertIn("AI_API_KEY", err)
        self.assertNotIn(self.secret, out + err)
        self.assertFalse((self.project / ".runs").exists())

    def test_environment_is_not_mutated(self):
        before = dict(self.env)
        load_environment(self.env)
        self.assertEqual(self.env, before)

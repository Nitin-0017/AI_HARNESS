import io
import json
import os
from pathlib import Path
from unittest import mock

from ai_harness.errors import InputError, WorkspaceError
from ai_harness.startup import StartupOptions
from support import FoundationTestCase


class InteractiveInput(io.StringIO):
    def isatty(self):
        return True


class StartupTests(FoundationTestCase):
    def test_plain_startup_without_task_or_workspace_waits_honestly(self):
        code, out, err = self.cli()
        self.assertEqual(code, 0, err)
        self.assertIn("AWAITING_INPUT", out)
        self.assertIn("NOT_RUN", out)
        self.assertIn("Model calls:  0", out)
        self.assertNotIn(self.secret, out + err)

    def test_startup_with_separate_target_and_task_is_ready(self):
        result = self.initialize(options=self.ready_options())
        self.assertEqual(result.snapshot["status"], "READY")
        self.assertEqual(result.snapshot["workspace"], str(self.target))
        self.assertTrue(result.state_file.is_file())
        self.assertEqual(json.loads(result.state_file.read_text()), result.snapshot)

    def test_startup_does_not_modify_target_or_change_cwd(self):
        before, cwd = self.digest_target(), Path.cwd()
        self.initialize(options=self.ready_options())
        self.assertEqual(self.digest_target(), before)
        self.assertEqual(Path.cwd(), cwd)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["main.py"])

    def test_zero_model_calls_without_endpoint(self):
        result = self.initialize(options=self.ready_options())
        self.assertIsNone(result.snapshot["model"]["family"])
        self.assertEqual(result.snapshot["usage"]["model_calls"], 0)
        self.assertEqual(result.snapshot["model_execution"], "NOT_RUN")

    def test_supplied_endpoint_is_not_contacted(self):
        env = {**self.env, "AI_MODEL_FAMILY": "deepseek", "AI_MODEL_ID": "organizer-id",
               "AI_API_ENDPOINT": "https://example.invalid/api"}
        with mock.patch("socket.socket", side_effect=AssertionError("No network in Phase 1")):
            result = self.initialize(options=self.ready_options(), env=env)
        self.assertEqual(result.snapshot["model"]["model_id"], "organizer-id")
        self.assertEqual(result.snapshot["usage"]["model_calls"], 0)

    def test_no_target_code_or_subprocess_is_executed(self):
        (self.target / "sitecustomize.py").write_text('raise RuntimeError("must not import target")\n')
        with mock.patch("subprocess.run", side_effect=AssertionError("No subprocess in Phase 1")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("No subprocess in Phase 1")):
            result = self.initialize(options=self.ready_options())
        self.assertEqual(result.snapshot["status"], "READY")

    def test_task_and_workspace_environment_input(self):
        env = {**self.env, "HARNESS_WORKSPACE": str(self.target), "HARNESS_TASK": "Fix addition"}
        code, out, err = self.cli(["--json"], env=env)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "READY")

    def test_strict_mode_rejects_missing_input(self):
        code, out, err = self.cli(["--require-input"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["status"], "BLOCKED")
        self.assertFalse((self.project / ".runs").exists())

    def test_just_one_input_is_still_awaiting_input(self):
        result = self.initialize(options=StartupOptions(task="A task"))
        self.assertEqual(result.snapshot["missing_inputs"], ["workspace"])
        self.assertEqual(result.snapshot["status"], "AWAITING_INPUT")

    def test_interactive_prompts_collect_task_and_workspace(self):
        stream = InteractiveInput(str(self.target) + "\nInspect addition\n")
        prompt = io.StringIO()
        result = self.initialize(stdin=stream, prompt_output=prompt)
        self.assertEqual(result.snapshot["status"], "READY")
        self.assertEqual(result.snapshot["task"]["source"], "interactive")
        self.assertNotIn("API key", prompt.getvalue())

    def test_interactive_eof_is_not_fake_success(self):
        result = self.initialize(stdin=InteractiveInput())
        self.assertEqual(result.snapshot["status"], "AWAITING_INPUT")

    def test_noninteractive_option_disables_prompts(self):
        stream = InteractiveInput(str(self.target) + "\nTask\n")
        prompt = io.StringIO()
        result = self.initialize(options=StartupOptions(non_interactive=True), stdin=stream, prompt_output=prompt)
        self.assertEqual(prompt.getvalue(), "")
        self.assertEqual(result.snapshot["status"], "AWAITING_INPUT")

    def test_stdin_task_disables_interactive_prompt(self):
        result = self.initialize(options=self.ready_options(task=None, task_stdin=True),
                                 stdin=InteractiveInput("A multiline\ntask"))
        self.assertEqual(result.snapshot["task"]["source"], "stdin")

    def test_invalid_workspace_fails_before_creating_output(self):
        code, out, err = self.cli(["--workspace", str(self.project), "--task", "A task"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error_type"], "WorkspaceError")
        self.assertFalse((self.project / ".runs").exists())

    def test_output_in_target_fails_without_any_changes(self):
        before = self.digest_target()
        code, out, err = self.cli(["--workspace", str(self.target), "--task", "A task",
                                   "--output-dir", str(self.target / "logs")])
        self.assertEqual(code, 2)
        self.assertEqual(before, self.digest_target())
        self.assertFalse((self.target / "logs").exists())

    def test_context_limit_is_enforced_during_startup(self):
        options = self.ready_options(overrides={"workspace": self.target, "budgets": {"max_context_chars": 2}})
        with self.assertRaises(InputError):
            self.initialize(options=options)
        self.assertFalse((self.project / ".runs").exists())

    def test_secret_in_task_is_redacted_from_state_logs_and_stdout(self):
        code, out, err = self.cli(["--workspace", str(self.target), "--task", "Never print " + self.secret, "--json"])
        self.assertEqual(code, 0, err)
        self.assertNotIn(self.secret, out + err)
        for file in (self.project / ".runs").rglob("*"):
            if file.is_file():
                self.assertNotIn(self.secret, file.read_text())
        self.assertIn("[REDACTED]", json.loads(out)["task"]["text"])

    def test_bad_numeric_cli_value_does_not_disclose_secret(self):
        code, out, err = self.cli(["--max-iterations", self.secret])
        self.assertEqual(code, 2)
        self.assertNotIn(self.secret, out + err)

    def test_credential_cli_argument_is_not_supported(self):
        code, out, err = self.cli(["--api-key", self.secret])
        self.assertEqual(code, 2)
        self.assertNotIn(self.secret, out + err)

    def test_log_events_show_actual_foundation_sequence(self):
        result = self.initialize(options=self.ready_options())
        records = [json.loads(line) for line in (result.run_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([r["event"] for r in records], ["startup.begin", "environment.validated", "configuration.loaded",
                                                       "input.loaded", "startup.complete"])
        self.assertEqual(records[-1]["data"]["model_calls"], 0)

    def test_keyboard_interrupt_has_explicit_nonzero_exit(self):
        with mock.patch("ai_harness.cli.initialize", side_effect=KeyboardInterrupt):
            code, out, err = self.cli()
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(err)["status"], "INCOMPLETE")

    def test_filesystem_error_is_not_ignored(self):
        with mock.patch("ai_harness.startup.create_run_dir", side_effect=OSError("disk unavailable")):
            code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["status"], "BLOCKED")

    def test_terminal_output_escapes_task_control_sequences(self):
        code, out, err = self.cli(["--workspace", str(self.target), "--task", "Task \x1b[2J hidden"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("\x1b", out)
        self.assertIn("\\x1b", out)

    def test_unknown_cli_credential_is_not_echoed_even_when_not_environment_key(self):
        import secrets
        other = secrets.token_urlsafe(48)
        code, out, err = self.cli(["--api-key=" + other])
        self.assertEqual(code, 2)
        self.assertNotIn(other, out + err)

    def test_nonprintable_credential_causes_controlled_failure(self):
        code, out, err = self.cli(env={"AI_API_KEY": "\ud800"})
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error_type"], "EnvironmentValidationError")

from dataclasses import replace
import json
import logging
import os
from pathlib import Path
from unittest import mock

from ai_harness.config import BudgetConfig
from ai_harness.errors import BudgetExceeded
from ai_harness.inputs import validate_task
from ai_harness.state import RunState, StartupStatus
from ai_harness.telemetry import EventLog, JsonFormatter, Redactor, create_run_dir, write_state
from support import FoundationTestCase


class StateTests(FoundationTestCase):
    def test_new_state_has_zero_usage_and_no_verification(self):
        state = RunState(BudgetConfig())
        snapshot = state.to_dict()
        self.assertEqual(state.status, StartupStatus.STARTING)
        self.assertEqual(snapshot["verification_status"], "NOT_RUN")
        self.assertEqual(snapshot["usage"]["model_calls"], 0)
        self.assertEqual(snapshot["usage"]["total_tokens"], 0)
        self.assertIsNone(snapshot["task_result"])

    def test_missing_input_is_not_ready(self):
        state = RunState(BudgetConfig())
        state.finish_startup()
        self.assertEqual(state.status, StartupStatus.AWAITING_INPUT)
        self.assertEqual(state.missing_inputs, ["workspace", "task"])

    def test_ready_is_not_verified(self):
        state = RunState(BudgetConfig(), workspace=str(self.target), task=validate_task("Task", "cli", 100))
        state.finish_startup()
        self.assertEqual(state.status, StartupStatus.READY)
        self.assertEqual(state.to_dict()["verification_status"], "NOT_RUN")
        self.assertEqual(state.to_dict()["model_execution"], "NOT_RUN")

    def test_ids_are_unique_and_timestamp_has_timezone(self):
        first, second = RunState(BudgetConfig()), RunState(BudgetConfig())
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertIn("+00:00", first.started_at)

    def test_elapsed_time_uses_injected_monotonic_clock(self):
        clock = [10.0]
        state = RunState(BudgetConfig(), _clock=lambda: clock[0])
        clock[0] += 2.5
        self.assertEqual(state.elapsed_seconds, 2.5)
        self.assertEqual(state.to_dict()["usage"]["elapsed_seconds"], 2.5)

    def test_startup_time_budget_is_checked(self):
        clock = [10.0]
        state = RunState(replace(BudgetConfig(), max_seconds=1), _clock=lambda: clock[0])
        clock[0] = 11.0
        with self.assertRaises(BudgetExceeded):
            state.finish_startup()
        self.assertEqual(state.status, StartupStatus.STARTING)


class TelemetryTests(FoundationTestCase):
    def test_redaction_handles_nested_values_and_sensitive_fields(self):
        redactor = Redactor(self.secret)
        payload = redactor.clean({"message": self.secret, "nested": [{"value": "before " + self.secret}],
                                  "AI_API_KEY": "anything", "credential_present": True})
        self.assertNotIn(self.secret, json.dumps(payload))
        self.assertEqual(payload["AI_API_KEY"], "[REDACTED]")
        self.assertTrue(payload["credential_present"])

    def test_redactor_repr_hides_its_inputs(self):
        self.assertNotIn(self.secret, repr(Redactor(self.secret)))

    def test_log_is_json_lines_with_run_context_and_redaction(self):
        directory = create_run_dir(self.project / ".runs", "local-run")
        with EventLog(directory, "local-run", Redactor(self.secret), "INFO") as log:
            log.emit("startup.test", nested={"value": self.secret})
            log.emit("startup.next", count=1)
        text = (directory / "events.jsonl").read_text()
        self.assertNotIn(self.secret, text)
        lines = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["run_id"], "local-run")
        self.assertEqual(lines[0]["event"], "startup.test")
        self.assertIn("timestamp", lines[0])

    def test_log_level_filters_debug(self):
        directory = create_run_dir(self.project / ".runs", "local-run")
        with EventLog(directory, "local-run", Redactor(), "INFO") as log:
            log.emit("debug.event", level="DEBUG")
            log.emit("info.event")
        text = (directory / "events.jsonl").read_text()
        self.assertNotIn("debug.event", text)
        self.assertIn("info.event", text)

    def test_state_is_redacted_and_atomically_written(self):
        directory = create_run_dir(self.project / ".runs", "local-run")
        result = write_state(directory, {"task": self.secret, "status": "READY"}, Redactor(self.secret))
        self.assertEqual(json.loads(result.read_text())["task"], "[REDACTED]")
        self.assertEqual(list(directory.glob("*.tmp")), [])

    def test_existing_run_directory_is_not_overwritten(self):
        create_run_dir(self.project / ".runs", "local-run")
        with self.assertRaises(FileExistsError):
            create_run_dir(self.project / ".runs", "local-run")

    def test_posix_permissions_are_private(self):
        if os.name != "posix":
            self.skipTest("POSIX permissions only")
        directory = create_run_dir(self.project / ".runs", "local-run")
        path = write_state(directory, {}, Redactor())
        with EventLog(directory, "local-run", Redactor(), "INFO") as log:
            log.emit("event")
        self.assertEqual(directory.stat().st_mode & 0o077, 0)
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertEqual((directory / "events.jsonl").stat().st_mode & 0o077, 0)

    def test_logging_io_failure_is_not_silently_ignored(self):
        directory = create_run_dir(self.project / ".runs", "local-run")
        with EventLog(directory, "local-run", Redactor(), "INFO") as log:
            with mock.patch.object(log._stream, "write", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    log.emit("event")

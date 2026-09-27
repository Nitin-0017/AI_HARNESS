import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from support import FoundationTestCase, PROJECT


class CliProcessTests(FoundationTestCase):
    def run_cli(self, *args, env=None, cwd=None, input_text=""):
        return subprocess.run([sys.executable, "-I", "-m", "ai_harness", *args],
                              cwd=cwd or self.base, env=self.subprocess_env() if env is None else env,
                              input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)

    def test_help_runs_without_credentials(self):
        env = self.subprocess_env()
        env.pop("AI_API_KEY")
        result = self.run_cli("--help", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No model execution", result.stdout)

    def test_version(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0.3.0")

    def test_real_process_accepts_target_and_task(self):
        result = self.run_cli("--workspace", str(self.target), "--task", "Fix addition", "--json", "--require-input",
                              "--output-dir", str(self.base / "logs"))
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = json.loads(result.stdout)
        self.assertEqual(snapshot["status"], "READY")
        self.assertEqual(snapshot["usage"]["model_calls"], 0)

    def test_real_process_task_file(self):
        issue = self.base / "issue.txt"
        issue.write_text("A real input file\nwith multiple lines\n")
        result = self.run_cli("--repo", str(self.target), "--task-file", str(issue), "--json",
                              "--output-dir", str(self.base / "logs"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["task"]["source"], "file")

    def test_real_process_stdin_input(self):
        result = self.run_cli("--workspace", str(self.target), "--task-stdin", "--json",
                              "--output-dir", str(self.base / "logs"), input_text="Task from a pipe\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["task"]["source"], "stdin")

    def test_missing_key_has_controlled_error_and_no_traceback(self):
        env = self.subprocess_env()
        env.pop("AI_API_KEY")
        result = self.run_cli("--json", env=env)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "BLOCKED")
        self.assertNotIn("Traceback", result.stderr)

    def test_no_implicit_cwd_workspace_even_when_cwd_is_target(self):
        result = self.run_cli("--json", "--output-dir", str(self.base / "logs"), cwd=self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["workspace"])
        self.assertEqual(json.loads(result.stdout)["status"], "AWAITING_INPUT")

    def test_target_import_shadowing_is_not_executed(self):
        (self.target / "ai_harness.py").write_text('raise RuntimeError("untrusted shadow import")\n')
        (self.target / "sitecustomize.py").write_text('raise RuntimeError("untrusted site customizer")\n')
        env = self.subprocess_env()
        env["PYTHONPATH"] = str(self.target)
        result = self.run_cli("--workspace", str(self.target), "--task", "Task", "--json",
                              "--output-dir", str(self.base / "logs"), cwd=self.target, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "READY")

    def test_json_state_and_logs_do_not_contain_environment_credential(self):
        result = self.run_cli("--workspace", str(self.target), "--task", self.secret, "--json",
                              "--output-dir", str(self.base / "logs"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.secret, result.stdout + result.stderr)
        for path in (self.base / "logs").rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret, path.read_text())


class LifecycleSafetyTests(FoundationTestCase):
    def copy_scripts(self):
        (self.project / "scripts").mkdir()
        shutil.copy2(PROJECT / "scripts" / "clean.py", self.project / "scripts" / "clean.py")
        shutil.copy2(PROJECT / "scripts" / "setup.py", self.project / "scripts" / "setup.py")

    def test_clean_preserves_logs_target_and_source(self):
        self.copy_scripts()
        for directory in (".venv", "build", "dist", "src/ai_harness/__pycache__", "tests/__pycache__", ".runs/run-one"):
            path = self.project / directory
            path.mkdir(parents=True)
            (path / "marker").write_text("content")
        (self.project / "src" / "ai_harness" / "main.py").write_text("# preserve source\n")
        before = self.digest_target()
        result = subprocess.run([sys.executable, "-I", "scripts/clean.py"], cwd=self.project, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.digest_target())
        self.assertFalse((self.project / ".venv").exists())
        self.assertFalse((self.project / "src" / "ai_harness" / "__pycache__").exists())
        self.assertTrue((self.project / "src" / "ai_harness" / "main.py").is_file())
        self.assertTrue((self.project / ".runs" / "run-one" / "marker").is_file())

    def test_clean_does_not_follow_symlink_to_target(self):
        self.copy_scripts()
        (self.project / ".venv").symlink_to(self.target, target_is_directory=True)
        before = self.digest_target()
        result = subprocess.run([sys.executable, "-I", "scripts/clean.py"], cwd=self.project, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.digest_target())
        self.assertFalse((self.project / ".venv").is_symlink())

    def test_setup_refuses_symlinked_virtual_environment(self):
        self.copy_scripts()
        (self.project / ".venv").symlink_to(self.target, target_is_directory=True)
        before = self.digest_target()
        result = subprocess.run([sys.executable, "-I", "scripts/setup.py"], cwd=self.project, text=True,
                                capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing", result.stderr)
        self.assertEqual(before, self.digest_target())

    def test_clean_can_remove_a_stale_regular_file_artifact(self):
        self.copy_scripts()
        (self.project / ".venv").write_text("stale setup artifact")
        result = subprocess.run([sys.executable, "-I", "scripts/clean.py"], cwd=self.project, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.project / ".venv").exists())

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

from ai_harness.cli import main
from ai_harness.startup import StartupOptions, initialize

PROJECT = Path(__file__).resolve().parents[1]


class FoundationTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="harness-phase1-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "harness"
        self.target = self.base / "target"
        self.project.mkdir()
        self.target.mkdir()
        (self.target / "main.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        # A random local validation value, never a real or hard-coded credential.
        self.secret = secrets.token_urlsafe(48)
        self.env = {"AI_API_KEY": self.secret}

    def config_file(self, text: str, name: str = "harness.toml") -> Path:
        result = self.project / name
        result.write_text(text, encoding="utf-8")
        return result

    def initialize(self, **kwargs):
        options = kwargs.pop("options", StartupOptions())
        defaults = dict(env=self.env, stdin=io.StringIO(), prompt_output=io.StringIO(),
                        project_root=self.project, cwd=self.base)
        defaults.update(kwargs)
        return initialize(options, **defaults)

    def cli(self, args=None, *, env=None, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(args or [], env=self.env if env is None else env,
                    stdin=io.StringIO() if stdin is None else stdin,
                    stdout=out, stderr=err, project_root=self.project)
        return code, out.getvalue(), err.getvalue()

    def ready_options(self, **kwargs):
        defaults = dict(overrides={"workspace": self.target}, task="Inspect addition behavior", non_interactive=True)
        defaults.update(kwargs)
        return StartupOptions(**defaults)

    def subprocess_env(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("AI_", "HARNESS_", "PYTHON"))}
        env.update(self.env)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def digest_target(self):
        return {str(p.relative_to(self.target)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.target.rglob("*") if p.is_file()}

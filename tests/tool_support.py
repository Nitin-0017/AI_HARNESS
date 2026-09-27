from __future__ import annotations
from pathlib import Path
import secrets
import tempfile
import unittest

from ai_harness.telemetry import Redactor
from ai_harness.tools import RepositoryTools
from ai_harness.tool_types import CheckSpec, ToolLimits
from ai_harness.workspace import Workspace
from fixture_repositories import create_fixture, git

PROJECT = Path(__file__).resolve().parents[1]
UNIT = CheckSpec('unit', ('{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-v'), timeout_seconds=5)


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harness-phase2-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = create_fixture(self.base / 'target')
        self.secret = secrets.token_urlsafe(36)
        self.tools = self.new_tools()

    def new_tools(self, *, limits=None, checks=(), state=None):
        tools = RepositoryTools(Workspace.open(self.target, PROJECT), limits=limits,
                                checks=checks, state=state, redactor=Redactor(self.secret))
        self.addCleanup(tools.close)
        return tools

    def write(self, path, text):
        target = self.target / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text, encoding='utf-8')
        return target

    def run_python(self, source, *, timeout=5, limits=None):
        tools = self.new_tools(limits=limits, checks=(CheckSpec('probe', ('{python}', '-c', source), timeout_seconds=timeout),))
        return tools.run_checks()['results'][0]

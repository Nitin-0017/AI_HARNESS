import io
import os
import unittest
from pathlib import Path

from ai_harness.errors import InputError
from ai_harness.inputs import load_task, validate_task
from support import FoundationTestCase


class InputTests(FoundationTestCase):
    def load(self, **kwargs):
        values = dict(text=None, file=None, from_stdin=False, env={}, stdin=io.StringIO(), cwd=self.base, max_bytes=100)
        values.update(kwargs)
        return load_task(**values)

    def test_missing_input_stays_missing(self):
        self.assertIsNone(self.load())

    def test_cli_text_preserves_multiline_task(self):
        task = self.load(text="  fix add\nkeep existing behavior  ")
        self.assertEqual(task.text, "fix add\nkeep existing behavior")
        self.assertEqual(task.source, "cli")

    def test_file_input(self):
        file = self.base / "issue.txt"
        file.write_text("Fix the edge case\n", encoding="utf-8")
        task = self.load(file=Path("issue.txt"))
        self.assertEqual(task.text, "Fix the edge case")

    def test_stdin_input(self):
        task = self.load(from_stdin=True, stdin=io.StringIO("Fix a bug\n"))
        self.assertEqual(task.source, "stdin")
        self.assertEqual(task.text, "Fix a bug")

    def test_environment_inputs(self):
        self.assertEqual(self.load(env={"HARNESS_TASK": "Fix a bug"}).source, "environment")
        file = self.base / "issue.txt"
        file.write_text("Fix the edge case")
        self.assertEqual(self.load(env={"HARNESS_TASK_FILE": str(file)}).source, "file")

    def test_cli_overrides_environment_task_sources(self):
        task = self.load(text="CLI task", env={"HARNESS_TASK": "Other task", "HARNESS_TASK_FILE": "missing"})
        self.assertEqual(task.text, "CLI task")

    def test_conflicting_sources_are_rejected(self):
        with self.assertRaises(InputError):
            self.load(env={"HARNESS_TASK": "A", "HARNESS_TASK_FILE": "B"})
        with self.assertRaises(InputError):
            self.load(text="A", from_stdin=True)

    def test_empty_nul_and_invalid_unicode_are_rejected(self):
        for text in ("", " \n", "task\0text", "\ud800"):
            with self.assertRaises(InputError):
                self.load(text=text)

    def test_size_limit_uses_utf8_bytes_not_character_count(self):
        with self.assertRaises(InputError):
            self.load(text="é" * 51)
        self.assertEqual(self.load(text="é" * 50).size_bytes, 100)

    def test_large_text_file_and_stdin_are_rejected(self):
        file = self.base / "issue.txt"
        file.write_text("x" * 101)
        with self.assertRaises(InputError):
            self.load(file=file)
        with self.assertRaises(InputError):
            self.load(from_stdin=True, stdin=io.StringIO("x" * 101))

    def test_missing_non_utf8_and_directory_files_are_rejected(self):
        file = self.base / "bad.txt"
        file.write_bytes(b"\xff\xfe")
        for candidate in (file, self.base, self.base / "missing.txt"):
            with self.assertRaises(InputError):
                self.load(file=candidate)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO support required")
    def test_fifo_is_rejected_without_opening_or_blocking(self):
        fifo = self.base / "issue.pipe"
        os.mkfifo(fifo)
        with self.assertRaises(InputError):
            self.load(file=fifo)

    def test_task_repr_does_not_include_input_content(self):
        task = self.load(text=self.secret)
        self.assertNotIn(self.secret, repr(task))

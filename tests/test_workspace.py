from pathlib import Path
import os
import unittest

from ai_harness.errors import WorkspaceError
from ai_harness.workspace import Workspace, validate_output_dir
from support import FoundationTestCase


class WorkspaceTests(FoundationTestCase):
    def test_accepts_existing_separate_target_without_git_metadata(self):
        workspace = Workspace.open(self.target, self.project)
        self.assertEqual(workspace.root, self.target.resolve())

    def test_rejects_harness_itself(self):
        with self.assertRaises(WorkspaceError):
            Workspace.open(self.project, self.project)

    def test_rejects_nested_target(self):
        nested = self.project / "target"
        nested.mkdir()
        with self.assertRaises(WorkspaceError):
            Workspace.open(nested, self.project)

    def test_rejects_ancestor_of_harness(self):
        with self.assertRaises(WorkspaceError):
            Workspace.open(self.base, self.project)

    def test_rejects_missing_target_and_never_creates_it(self):
        missing = self.base / "missing"
        with self.assertRaises(WorkspaceError):
            Workspace.open(missing, self.project)
        self.assertFalse(missing.exists())

    def test_file_is_not_a_workspace(self):
        with self.assertRaises(WorkspaceError):
            Workspace.open(self.target / "main.py", self.project)

    def test_prefix_like_sibling_is_not_falsely_rejected(self):
        sibling = self.base / "harness-target"
        sibling.mkdir()
        self.assertEqual(Workspace.open(sibling, self.project).root, sibling)

    def test_symlink_alias_to_harness_is_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(WorkspaceError):
            Workspace.open(alias, self.project)

    def test_symlink_alias_to_separate_target_is_canonicalized(self):
        alias = self.base / "alias"
        alias.symlink_to(self.target, target_is_directory=True)
        self.assertEqual(Workspace.open(alias, self.project).root, self.target)

    def test_read_path_validation(self):
        workspace = Workspace.open(self.target, self.project)
        self.assertEqual(workspace.resolve_path("main.py"), self.target / "main.py")
        self.assertEqual(workspace.resolve_path("new.py", must_exist=False), self.target / "new.py")
        self.assertFalse((self.target / "new.py").exists())

    def test_absolute_parent_windows_and_empty_paths_are_rejected(self):
        workspace = Workspace.open(self.target, self.project)
        for path in ("", "/etc/passwd", "../harness", "folder/../../other", "C:\\Windows", "C:relative", "main\0.py"):
            with self.subTest(path=path):
                with self.assertRaises(WorkspaceError):
                    workspace.resolve_path(path, must_exist=False)

    def test_file_symlink_escape_is_rejected(self):
        external = self.base / "external.txt"
        external.write_text("do not read")
        (self.target / "link.txt").symlink_to(external)
        with self.assertRaises(WorkspaceError):
            Workspace.open(self.target, self.project).resolve_path("link.txt")

    def test_directory_symlink_escape_for_new_path_is_rejected(self):
        (self.target / "link").symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(WorkspaceError):
            Workspace.open(self.target, self.project).resolve_path("link/new.py", must_exist=False)

    def test_internal_symlink_is_allowed(self):
        (self.target / "link.py").symlink_to(self.target / "main.py")
        self.assertEqual(Workspace.open(self.target, self.project).resolve_path("link.py"), self.target / "main.py")

    def test_symlink_loop_is_a_controlled_error(self):
        loop = self.base / "loop"
        loop.symlink_to(loop)
        with self.assertRaises(WorkspaceError):
            Workspace.open(loop, self.project)

    def test_log_directory_cannot_overlap_target(self):
        workspace = Workspace.open(self.target, self.project)
        for candidate in (self.target, self.target / "logs", self.base):
            with self.assertRaises(WorkspaceError):
                validate_output_dir(candidate, workspace=workspace, harness_root=self.project)

    def test_output_symlink_is_rejected(self):
        link = self.base / "logs-link"
        link.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(WorkspaceError):
            validate_output_dir(link / "child", workspace=None, harness_root=self.project)

    def test_output_harness_root_code_and_ancestors_are_rejected(self):
        for candidate in (self.project, self.base, self.project / "src" / "logs", self.project / ".venv" / "logs"):
            with self.assertRaises(WorkspaceError):
                validate_output_dir(candidate, workspace=None, harness_root=self.project)

    def test_output_regular_file_is_rejected(self):
        with self.assertRaises(WorkspaceError):
            validate_output_dir(self.target / "main.py", workspace=None, harness_root=self.project)

    def test_external_and_default_output_are_allowed_without_creation(self):
        workspace = Workspace.open(self.target, self.project)
        for path in (self.project / ".runs", self.base / "logs"):
            self.assertEqual(validate_output_dir(path, workspace=workspace, harness_root=self.project), path)
            self.assertFalse(path.exists())

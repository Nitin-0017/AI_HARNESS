"""Exact, validated UTF-8 patches. No shell, fuzzy matching, or arbitrary writes."""
from __future__ import annotations

from contextlib import ExitStack
import os
import re
import stat
from uuid import uuid4

from .repository_io import RepositoryIO, digest, path_parts
from .tool_types import PatchEdit, PatchError, ToolError


def _temporary(parent: int, data: bytes, mode: int) -> str:
    name = '.harness-patch-' + uuid4().hex
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode & 0o777)
            os.fsync(stream.fileno())
        return name
    except BaseException:
        os.unlink(name, dir_fd=parent)
        raise


def apply(io: RepositoryIO, edits: list[PatchEdit | dict], *, dry_run: bool = False) -> dict:
    if not isinstance(dry_run, bool):
        raise PatchError('dry_run must be a boolean')
    if not isinstance(edits, list) or not edits or len(edits) > io.limits.max_patch_files:
        raise PatchError('Patch must contain between one and max_patch_files edits')
    validated: list[dict] = []
    paths: set[str] = set()
    with ExitStack() as stack:
        for raw in edits:
            try:
                edit = PatchEdit(**raw) if isinstance(raw, dict) else raw
            except TypeError as exc:
                raise PatchError('Unknown or missing patch field') from exc
            if (not isinstance(edit, PatchEdit) or not isinstance(edit.operation, str)
                    or edit.operation not in {'replace', 'create', 'delete'}):
                raise PatchError('Patch operation must be replace, create, or delete')
            parts = path_parts(edit.path)
            path = '/'.join(parts)
            if path in paths:
                raise PatchError('Each file may appear only once in a patch batch')
            paths.add(path)
            if not isinstance(edit.old, str) or not isinstance(edit.new, str) or '\0' in edit.old + edit.new:
                raise PatchError('Patch old/new values must be UTF-8 text without NUL bytes')
            try:
                old_text = edit.old.encode('utf-8')
                new_text = edit.new.encode('utf-8')
            except UnicodeError as exc:
                raise PatchError('Patch contains invalid Unicode') from exc
            if max(len(old_text), len(new_text)) > io.limits.max_file_bytes:
                raise PatchError('Patch text exceeds max_file_bytes')
            if edit.expected_sha256 is not None and (not isinstance(edit.expected_sha256, str)
                    or re.fullmatch('[0-9a-f]{64}', edit.expected_sha256) is None):
                raise PatchError('expected_sha256 must be a lowercase SHA-256 hex digest')
            parent, name = stack.enter_context(io.parent(path))
            if edit.operation == 'create':
                if edit.old or not edit.new or edit.expected_sha256 is not None:
                    raise PatchError('Create requires new text, empty old text, and no expected digest')
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise PatchError('Create cannot overwrite an existing path')
                before, info, after, mode = None, None, new_text, 0o644
            else:
                before, info = io.read_at(parent, name)
                try:
                    before.decode('utf-8')
                except UnicodeError as exc:
                    raise PatchError('Only UTF-8 text files can be patched') from exc
                if b'\0' in before:
                    raise PatchError('Binary files cannot be patched')
                if edit.expected_sha256 is not None and digest(before) != edit.expected_sha256:
                    raise PatchError('Patch digest does not match the current file')
                mode = stat.S_IMODE(info.st_mode)
                if edit.operation == 'delete':
                    if edit.expected_sha256 is None or edit.new or (edit.old and old_text != before):
                        raise PatchError('Delete requires the current digest, no new text, and an exact old value if supplied')
                    after = None
                else:
                    if not edit.old or edit.old == edit.new or before.count(old_text) != 1:
                        raise PatchError('Replacement old text must match exactly once and new text must differ')
                    after = before.replace(old_text, new_text, 1)
            if after is not None and len(after) > io.limits.max_file_bytes:
                raise PatchError('Resulting file exceeds max_file_bytes')
            validated.append({'path': path, 'parent': parent, 'name': name, 'before': before,
                              'after': after, 'info': info, 'mode': mode, 'operation': edit.operation})
        summaries = [{'path': p['path'], 'operation': p['operation'],
                      'before_sha256': digest(p['before']) if p['before'] is not None else None,
                      'after_sha256': digest(p['after']) if p['after'] is not None else None}
                     for p in validated]
        if dry_run:
            return {'applied': False, 'dry_run': True, 'changes': summaries}
        staged: list[tuple[int, str]] = []
        applied: list[dict] = []
        try:
            for p in validated:
                if p['after'] is not None:
                    p['temporary'] = _temporary(p['parent'], p['after'], p['mode'])
                    staged.append((p['parent'], p['temporary']))
            io.ensure_root()
            # Revalidate every original before publishing ANY member of the batch.
            for p in validated:
                if p['before'] is None:
                    try:
                        os.stat(p['name'], dir_fd=p['parent'], follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    raise PatchError('A create destination appeared while validating the patch')
                current, info = io.read_at(p['parent'], p['name'])
                if current != p['before'] or io.signature(info) != io.signature(p['info']):
                    raise PatchError('File changed during patch validation')
            for p in validated:
                parent, name = p['parent'], p['name']
                if p['after'] is None:
                    os.unlink(name, dir_fd=parent)
                elif p['before'] is None:
                    # link() refuses an existing destination instead of replacing it.
                    os.link(p['temporary'], name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    os.unlink(p['temporary'], dir_fd=parent)
                else:
                    os.replace(p['temporary'], name, src_dir_fd=parent, dst_dir_fd=parent)
                applied.append(p)
                os.fsync(parent)
        except Exception as exc:
            rollback_failures = []
            for p in reversed(applied):
                try:
                    if p['after'] is not None:
                        current, _ = io.read_at(p['parent'], p['name'])
                        if current != p['after']:
                            raise PatchError('Concurrent edit prevents rollback')
                    if p['before'] is None:
                        os.unlink(p['name'], dir_fd=p['parent'])
                    else:
                        replacement = _temporary(p['parent'], p['before'], p['mode'])
                        try:
                            if p['after'] is None:
                                os.link(replacement, p['name'], src_dir_fd=p['parent'], dst_dir_fd=p['parent'],
                                        follow_symlinks=False)
                            else:
                                os.replace(replacement, p['name'], src_dir_fd=p['parent'], dst_dir_fd=p['parent'])
                        finally:
                            try:
                                os.unlink(replacement, dir_fd=p['parent'])
                            except FileNotFoundError:
                                pass
                except Exception:
                    rollback_failures.append(p['path'])
            if rollback_failures:
                raise PatchError('Patch partially applied; rollback failed. Inspect get_changes before continuing.') from exc
            if isinstance(exc, ToolError):
                raise
            raise PatchError('Patch write failed; published changes were rolled back') from exc
        finally:
            for parent, name in staged:
                try:
                    os.unlink(name, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return {'applied': True, 'dry_run': False, 'changes': summaries}

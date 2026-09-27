from dataclasses import replace
import json
import os
from pathlib import Path
from unittest import mock

from ai_harness.errors import ConfigurationError
from ai_harness.repository_io import digest
from ai_harness.tool_types import PatchError, PathViolation, ToolError, ToolLimits
from tool_support import ToolTestCase


class ListReadSearchTests(ToolTestCase):
    def test_list_contains_real_files_and_excludes_git(self):
        result = self.tools.list_files()
        self.assertIn('calculator.py', result['files'])
        self.assertIn('tests/test_calculator.py', result['files'])
        self.assertFalse(any(p.startswith('.git/') for p in result['files']))
        self.assertEqual(result['files'], sorted(result['files']))

    def test_listing_subdirectory_and_pattern(self):
        self.assertEqual(self.tools.list_files('tests', '*.py')['files'], ['tests/test_calculator.py'])

    def test_reads_actual_bytes_and_digest(self):
        self.write('unicode.py', '# café\nx = 42\n')
        result = self.tools.read_file('unicode.py')
        self.assertEqual(result['content'], '# café\nx = 42\n')
        self.assertEqual(result['sha256'], digest((self.target / 'unicode.py').read_bytes()))

    def test_line_ranges_are_one_based(self):
        self.write('lines.txt', 'one\ntwo\nthree\n')
        result = self.tools.read_file('lines.txt', 2, 2)
        self.assertEqual(result['content'], 'two\n')
        self.assertEqual(result['total_lines'], 3)

    def test_crlf_preserved(self):
        self.write('windows.txt', b'first\r\nsecond\r\n')
        self.assertEqual(self.tools.read_file('windows.txt')['content'], 'first\r\nsecond\r\n')

    def test_empty_file_is_real_empty_result(self):
        self.write('empty.txt', '')
        result = self.tools.read_file('empty.txt')
        self.assertEqual(result['content'], '')
        self.assertEqual(result['total_lines'], 0)

    def test_invalid_ranges(self):
        for start, end in [(0, None), (-1, 1), (True, 2), (1, 0), (3, 2), (1000, None)]:
            with self.subTest(start=start, end=end), self.assertRaises((ToolError, ConfigurationError)):
                self.tools.read_file('calculator.py', start, end)

    def test_missing_file(self):
        with self.assertRaises(PathViolation):
            self.tools.read_file('absent.py')

    def test_directory_cannot_be_read_as_file(self):
        with self.assertRaises(PathViolation):
            self.tools.read_file('tests')

    def test_invalid_paths_do_not_read_outside(self):
        outside = self.base / 'outside.txt'
        outside.write_text(self.secret)
        for path in ['../outside.txt', str(outside), 'a/../../outside.txt', 'C:/secret', 'C:\\secret',
                     'tests\\x', 'a\0b', 'a\nb', '', '/etc/passwd']:
            with self.subTest(path=repr(path)), self.assertRaises(PathViolation):
                self.tools.read_file(path)
        self.assertEqual(outside.read_text(), self.secret)

    def test_directory_traversal_is_rejected_by_listing_and_search(self):
        for tool, args in [(self.tools.list_files, {'path': '..'}),
                           (self.tools.search_code, {'query': 'x', 'path': '../'})]:
            with self.assertRaises(PathViolation):
                tool(**args)

    def test_symlink_file_is_rejected(self):
        outside = self.base / 'secret.txt'
        outside.write_text(self.secret)
        (self.target / 'link').symlink_to(outside)
        with self.assertRaises(PathViolation):
            self.tools.read_file('link')
        self.assertNotIn('link', self.tools.list_files()['files'])

    def test_internal_symlinks_are_also_rejected(self):
        (self.target / 'alias').symlink_to('calculator.py')
        with self.assertRaises(PathViolation):
            self.tools.read_file('alias')

    def test_symlink_directory_is_not_followed(self):
        (self.target / 'escape').symlink_to(self.base)
        with self.assertRaises(PathViolation):
            self.tools.read_file('escape/outside.txt')
        with self.assertRaises(PathViolation):
            self.tools.list_files('escape')

    def test_hardlink_is_rejected(self):
        outside = self.base / 'outside.txt'; outside.write_text('outside')
        os.link(outside, self.target / 'hardlink')
        with self.assertRaises(PathViolation):
            self.tools.read_file('hardlink')
        self.assertNotIn('hardlink', self.tools.list_files()['files'])

    def test_fifo_is_rejected_without_blocking(self):
        os.mkfifo(self.target / 'fifo')
        with self.assertRaises(PathViolation):
            self.tools.read_file('fifo')

    def test_protected_paths_are_not_returned(self):
        self.write('.env', self.secret)
        self.write('.env.example', 'AI_API_KEY=\n')
        self.write('credentials.pem', self.secret)
        for path in ['.env', '.git/config', 'credentials.pem']:
            with self.subTest(path=path), self.assertRaises(PathViolation):
                self.tools.read_file(path)
        self.assertIn('.env.example', self.tools.list_files()['files'])
        self.assertNotIn(self.secret, json.dumps(self.tools.search_code(self.secret)))

    def test_binary_read_fails_and_search_skips(self):
        self.write('binary.dat', b'abc\0def')
        with self.assertRaises(ToolError): self.tools.read_file('binary.dat')
        self.assertTrue(any(p['path'] == 'binary.dat' for p in self.tools.search_code('abc')['skipped']))

    def test_invalid_utf8_is_not_decoded_as_source(self):
        self.write('bad.dat', b'\xff\xfe')
        with self.assertRaises(ToolError): self.tools.read_file('bad.dat')

    def test_file_size_bound(self):
        self.write('big.txt', 'x' * 100)
        tools = self.new_tools(limits=replace(ToolLimits(), max_file_bytes=40))
        with self.assertRaises(ToolError): tools.read_file('big.txt')

    def test_read_output_is_bounded_and_marked(self):
        self.write('long.txt', 'hello world' * 30)
        result = self.new_tools(limits=replace(ToolLimits(), max_output_bytes=30)).read_file('long.txt')
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['content'].encode()), 30)

    def test_search_real_line_numbers(self):
        matches = self.tools.search_code('return sum', pattern='calculator.py')['matches']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['line'], 2)

    def test_search_literal_not_regex(self):
        self.write('literal.txt', '(a+)+$\n')
        self.assertEqual(len(self.tools.search_code('(a+)+$')['matches']), 1)

    def test_case_insensitive_search(self):
        self.write('words.txt', 'NeEdLe\n')
        self.assertEqual(self.tools.search_code('needle', pattern='words.txt')['matches'], [])
        self.assertEqual(len(self.tools.search_code('needle', pattern='words.txt', case_sensitive=False)['matches']), 1)

    def test_search_absent_is_empty(self):
        self.assertEqual(self.tools.search_code('not-present-anywhere')['matches'], [])

    def test_search_match_limit_is_explicit(self):
        self.write('many.txt', 'needle\n' * 4)
        result = self.new_tools(limits=replace(ToolLimits(), max_search_matches=2)).search_code('needle')
        self.assertEqual(len(result['matches']), 2)
        self.assertTrue(result['truncated'])

    def test_directory_entry_limit(self):
        with self.assertRaises(ToolError):
            self.new_tools(limits=replace(ToolLimits(), max_files=1)).list_files()

    def test_scan_byte_limit(self):
        result = self.new_tools(limits=replace(ToolLimits(), max_scan_bytes=1)).search_code('anything')
        self.assertTrue(result['truncated'])

    def test_ignored_dependencies_not_searched(self):
        self.write('node_modules/pkg/index.js', 'unique-target-string')
        self.assertEqual(self.tools.search_code('unique-target-string')['matches'], [])

    def test_credential_is_redacted_from_read_and_search(self):
        self.write('debug.txt', self.secret)
        self.assertNotIn(self.secret, json.dumps(self.tools.read_file('debug.txt')))
        self.assertNotIn(self.secret, json.dumps(self.tools.search_code(self.secret)))

    def test_closed_tools_fail(self):
        self.tools.close()
        with self.assertRaises(ToolError): self.tools.list_files()

    def test_root_replacement_is_detected(self):
        self.target.rename(self.base / 'moved')
        self.target.mkdir()
        with self.assertRaises(PathViolation): self.tools.list_files()

    def test_read_does_not_change_mtime(self):
        path = self.target / 'calculator.py'; before = path.stat().st_mtime_ns
        self.tools.read_file('calculator.py')
        self.assertEqual(path.stat().st_mtime_ns, before)


class PatchTests(ToolTestCase):
    def edit(self, **kw):
        return {'path': 'calculator.py', 'old': 'return sum(numbers) / len(numbers)',
                'new': 'return 0 if not numbers else sum(numbers) / len(numbers)', **kw}

    def test_exact_replacement_writes_real_change(self):
        result = self.tools.apply_patch([self.edit()])
        self.assertTrue(result['applied'])
        self.assertIn('return 0 if not numbers', (self.target / 'calculator.py').read_text())

    def test_correct_digest_is_accepted(self):
        sha = self.tools.read_file('calculator.py')['sha256']
        self.assertTrue(self.tools.apply_patch([self.edit(expected_sha256=sha)])['applied'])

    def test_stale_digest_is_rejected(self):
        with self.assertRaises(PatchError): self.tools.apply_patch([self.edit(expected_sha256='0' * 64)])

    def test_missing_match_is_rejected(self):
        with self.assertRaises(PatchError): self.tools.apply_patch([self.edit(old='not there')])

    def test_ambiguous_match_is_rejected(self):
        self.write('repeated.txt', 'same\nsame\n')
        with self.assertRaises(PatchError):
            self.tools.apply_patch([{'path':'repeated.txt','old':'same','new':'different'}])

    def test_empty_or_noop_replacement_is_rejected(self):
        for old, new in [('', 'x'), ('x', 'x')]:
            with self.assertRaises(PatchError): self.tools.apply_patch([self.edit(old=old, new=new)])

    def test_create_new_file(self):
        result = self.tools.apply_patch([{'path':'tests/test_new.py','operation':'create','new':'# regression\n'}])
        self.assertTrue(result['applied'])
        self.assertEqual((self.target / 'tests/test_new.py').read_text(), '# regression\n')

    def test_create_never_overwrites(self):
        with self.assertRaises(PatchError):
            self.tools.apply_patch([{'path':'calculator.py','operation':'create','new':'overwrite'}])

    def test_delete_requires_digest(self):
        with self.assertRaises(PatchError):
            self.tools.apply_patch([{'path':'calculator.py','operation':'delete'}])

    def test_delete_with_digest(self):
        sha = self.tools.read_file('calculator.py')['sha256']
        self.tools.apply_patch([{'path':'calculator.py','operation':'delete','expected_sha256':sha}])
        self.assertFalse((self.target / 'calculator.py').exists())

    def test_parent_must_already_exist(self):
        with self.assertRaises(PathViolation):
            self.tools.apply_patch([{'path':'newdir/a.py','operation':'create','new':'x = 1\n'}])
        self.assertFalse((self.target / 'newdir').exists())

    def test_dry_run_leaves_file_unchanged(self):
        before = (self.target / 'calculator.py').read_bytes()
        result = self.tools.apply_patch([self.edit()], dry_run=True)
        self.assertFalse(result['applied'])
        self.assertEqual((self.target / 'calculator.py').read_bytes(), before)

    def test_batch_validation_prevents_partial_change(self):
        before = (self.target / 'calculator.py').read_bytes()
        with self.assertRaises(PatchError):
            self.tools.apply_patch([self.edit(), {'path':'README.md','old':'absent','new':'bad'}])
        self.assertEqual((self.target / 'calculator.py').read_bytes(), before)

    def test_multi_file_batch(self):
        self.tools.apply_patch([self.edit(), {'path':'extra.py','operation':'create','new':'FACTOR = 2\n'}])
        self.assertTrue((self.target / 'extra.py').is_file())
        self.assertIn('if not numbers', (self.target / 'calculator.py').read_text())

    def test_duplicate_canonical_path_rejected(self):
        with self.assertRaises(PatchError):
            self.tools.apply_patch([self.edit(), self.edit(path='./calculator.py')])

    def test_traversal_patch_never_writes_outside(self):
        outside = self.base / 'outside.py'; outside.write_text('old')
        with self.assertRaises(PathViolation):
            self.tools.apply_patch([{'path':'../outside.py','old':'old','new':'new'}])
        self.assertEqual(outside.read_text(), 'old')

    def test_symlink_parent_cannot_create_outside(self):
        (self.target / 'out').symlink_to(self.base)
        with self.assertRaises(PathViolation):
            self.tools.apply_patch([{'path':'out/new.py','operation':'create','new':'bad'}])
        self.assertFalse((self.base / 'new.py').exists())

    def test_metadata_and_credentials_cannot_be_patched(self):
        for path in ['.git/config', '.env', '.ssh/key']:
            with self.assertRaises(PathViolation):
                self.tools.apply_patch([{'path':path,'operation':'create','new':'bad'}])

    def test_binary_patch_rejected(self):
        self.write('binary', b'a\0b')
        with self.assertRaises(PatchError):
            self.tools.apply_patch([{'path':'binary','old':'a','new':'b'}])

    def test_mode_is_preserved(self):
        path = self.target / 'calculator.py'; path.chmod(0o755)
        self.tools.apply_patch([self.edit()])
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_invalid_types_and_fields(self):
        for edits in [[], {}, [{'path':'x','unknown':1}], [self.edit(new=None)], [self.edit(operation='shell')]]:
            with self.subTest(edits=edits), self.assertRaises((PatchError, ToolError)):
                self.tools.apply_patch(edits)

    def test_patch_file_limit(self):
        t = self.new_tools(limits=replace(ToolLimits(), max_patch_files=1))
        with self.assertRaises(PatchError): t.apply_patch([self.edit(), {'path':'a','operation':'create','new':'a'}])

    def test_write_failure_rolls_back_published_files(self):
        from ai_harness import patching
        original = patching.os.replace
        calls = []
        before = (self.target / 'calculator.py').read_bytes()
        self.write('second.py','x = 1\n')
        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError('injected disk failure')
            return original(*args, **kwargs)
        with mock.patch.object(patching.os, 'replace', side_effect=fail_second):
            with self.assertRaises(PatchError):
                self.tools.apply_patch([self.edit(), {'path':'second.py','old':'1','new':'2'}])
        self.assertEqual((self.target / 'calculator.py').read_bytes(), before)
        self.assertEqual((self.target / 'second.py').read_text(), 'x = 1\n')
        self.assertEqual(list(self.target.rglob('.harness-patch-*')), [])

    def test_concurrent_change_detected_before_publish(self):
        from ai_harness import patching
        original = patching._temporary
        def race(*args):
            result = original(*args)
            (self.target / 'calculator.py').write_text('# external update\n')
            return result
        with mock.patch.object(patching, '_temporary', side_effect=race):
            with self.assertRaises(PatchError): self.tools.apply_patch([self.edit()])
        self.assertEqual((self.target / 'calculator.py').read_text(), '# external update\n')

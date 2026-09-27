from dataclasses import replace
import json
import os
import shutil

from ai_harness.tool_types import ToolError, ToolLimits
from fixture_repositories import git
from tool_support import ToolTestCase


class GitToolsTests(ToolTestCase):
    def test_actual_clean_status(self):
        result=self.tools.get_changes()
        self.assertTrue(result['clean'])
        self.assertEqual(result['status'],[])
        self.assertEqual(result['unstaged_diff'],'')
        self.assertTrue(all(r['exit_code']==0 and r['command_started'] for r in result['execution']))

    def test_actual_unstaged_diff(self):
        self.tools.apply_patch([{'path':'calculator.py','old':'return sum','new':'return 0 if not numbers else sum'}])
        result=self.tools.get_changes()
        self.assertFalse(result['clean'])
        self.assertIn('+    return 0 if not numbers',result['unstaged_diff'])
        self.assertEqual(result['staged_diff'],'')

    def test_actual_staged_diff(self):
        self.write('calculator.py','VALUE = 10\n'); git(self.target,'add','calculator.py')
        result=self.tools.get_changes()
        self.assertIn('+VALUE = 10',result['staged_diff'])
        self.assertEqual(result['unstaged_diff'],'')
        self.assertEqual(result['status'][0]['index_status'],'M')

    def test_staged_and_worktree_changes_are_separate(self):
        self.write('calculator.py','VALUE = 10\n'); git(self.target,'add','calculator.py')
        self.write('calculator.py','VALUE = 20\n')
        result=self.tools.get_changes()
        self.assertIn('+VALUE = 10',result['staged_diff'])
        self.assertIn('+VALUE = 20',result['unstaged_diff'])

    def test_untracked_file_has_actual_content_diff(self):
        self.write('new file.py','VALUE = 3\n')
        result=self.tools.get_changes()
        self.assertIn('new file.py',[s['path'] for s in result['status']])
        entry=next(s for s in result['untracked_files'] if s['path']=='new file.py')
        self.assertIn('+VALUE = 3',entry['diff'])
        self.assertIn('--- /dev/null',entry['diff'])

    def test_deletion_is_reported(self):
        (self.target/'calculator.py').unlink()
        result=self.tools.get_changes()
        self.assertIn('-def average',result['unstaged_diff'])
        self.assertEqual(result['status'][0]['worktree_status'],'D')

    def test_binary_untracked_content_not_fabricated(self):
        self.write('binary.bin',b'\0\xff')
        entry=self.tools.get_changes()['untracked_files'][0]
        self.assertTrue(entry['binary'])
        self.assertIsNone(entry['diff'])

    def test_non_git_root_is_explicit_error(self):
        shutil.rmtree(self.target/'.git')
        with self.assertRaises(ToolError): self.tools.get_changes()

    def test_linked_git_worktree_is_refused(self):
        shutil.rmtree(self.target/'.git'); self.write('.git','gitdir: ../other\n')
        with self.assertRaises(ToolError): self.tools.get_changes()

    def test_external_object_store_is_refused(self):
        self.write('.git/objects/info/alternates','/outside/objects\n')
        with self.assertRaises(ToolError): self.tools.get_changes()

    def test_inspection_does_not_modify_index(self):
        index=self.target/'.git/index'; before=(index.read_bytes(),index.stat().st_mtime_ns)
        self.tools.get_changes()
        self.assertEqual((index.read_bytes(),index.stat().st_mtime_ns),before)

    def test_repo_local_helpers_and_include_are_masked(self):
        self.write('calculator.py','x = 2\n')
        self.write('.gitattributes','*.py diff=danger filter=danger\n')
        config=self.target/'.git/config'
        with config.open('a') as stream:
            stream.write('\n[core]\nfsmonitor = touch marker-fsmonitor\n'
                         '[diff "danger"]\ntextconv = touch marker-diff\n'
                         '[filter "danger"]\nclean = touch marker-filter\n'
                         '[include]\npath = /outside/nonexistent-config\n')
        result=self.tools.get_changes()
        self.assertIn('+x = 2',result['unstaged_diff'])
        self.assertEqual(list(self.target.glob('marker-*')),[])

    def test_secret_value_redacted_from_diff(self):
        self.write('calculator.py','value = '+repr(self.secret)+'\n')
        result=self.tools.get_changes()
        self.assertNotIn(self.secret,json.dumps(result))
        self.assertIn('[REDACTED]',result['unstaged_diff'])

    def test_sensitive_paths_omitted_from_content_diff(self):
        self.write('.env',self.secret)
        result=self.tools.get_changes()
        self.assertIn('.env',result['omitted_sensitive_paths'])
        self.assertNotIn(self.secret,json.dumps(result))

    def test_oversized_git_output_cannot_claim_clean(self):
        self.write('calculator.py','x'*5000+'\n')
        t=self.new_tools(limits=replace(ToolLimits(),max_output_bytes=300))
        with self.assertRaises(ToolError): t.get_changes()

    def test_unborn_repository_staged_diff(self):
        shutil.rmtree(self.target/'.git'); git(self.target,'init','-q'); git(self.target,'add','calculator.py')
        result=self.tools.get_changes()
        self.assertIn('+def average',result['staged_diff'])

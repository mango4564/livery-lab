"""Exercise real local Git backups in disposable repositories; never push."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.build_windows import backup_git


class BuildGitBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root/'backup.log'
        self.git('init', '-q')
        self.git('config', 'user.name', 'Backup Test')
        self.git('config', 'user.email', 'backup@example.invalid')
        (self.root/'.gitignore').write_text('settings.json\nrelease/\n*.log\n')
        (self.root/'app.py').write_text('# original\n')
        (self.root/'deleted.py').write_text('# delete later\n')
        self.git('add', '--', '.gitignore', 'app.py', 'deleted.py')
        self.git('commit', '-qm', 'initial')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout

    def test_modified_new_deleted_and_renamed_files_backed_up_once(self):
        (self.root/'app.py').write_text('# changed\n')
        (self.root/'deleted.py').unlink()
        (self.root/'新增 [sample].py').write_text('# new\n')
        (self.root/'settings.json').write_text('private settings')
        (self.root/'release').mkdir()
        (self.root/'release'/'package.zip').write_bytes(b'private release')
        commit = backup_git(self.root, self.log)
        self.assertEqual(commit, self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(self.git('status', '--porcelain'), b'')
        self.assertEqual(self.git('show', 'HEAD:app.py'), b'# changed\n')
        self.assertNotIn(b'settings.json', self.git('ls-tree', '-r', '--name-only', 'HEAD'))
        self.assertEqual(backup_git(self.root, self.log), commit)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD').strip(), b'2')
        self.git('mv', 'app.py', 'renamed.py')
        backup_git(self.root, self.log)
        self.assertEqual(self.git('show', 'HEAD:renamed.py'), b'# changed\n')

    def test_excluded_staged_settings_stay_out_of_backup(self):
        (self.root/'settings.json').write_text('private settings')
        self.git('add', '-f', '--', 'settings.json')
        (self.root/'app.py').write_text('# changed\n')
        backup_git(self.root, self.log)
        self.assertNotIn(b'settings.json', self.git('ls-tree', '-r', '--name-only', 'HEAD'))
        self.assertIn(b'settings.json', self.git('diff', '--cached', '--name-only'))

    def test_failed_commit_stops_without_creating_a_commit(self):
        (self.root/'app.py').write_text('# changed\n')
        # Force a commit hook failure without relying on machine-wide identity.
        hook = self.root/'.git'/'hooks'/'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n', encoding='utf8')
        hook.chmod(0o755)
        original = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(RuntimeError, 'Git 自动备份失败'):
            backup_git(self.root, self.log)
        self.assertEqual(self.git('rev-parse', 'HEAD'), original)
        self.assertEqual((self.root/'app.py').read_text(), '# changed\n')


if __name__ == '__main__':
    unittest.main()

"""Automatic local updates without invoking PyInstaller or external APIs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_windows as builder


class LatestLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'core').mkdir()
        (self.root/'scripts').mkdir()
        for name in ('app.py', 'core/parser.py', 'requirements.txt',
                     'settings.example.json', 'README.md', 'scripts/build_windows.py',
                     'scripts/package_source.py', 'scripts/source_runtime.py'):
            (self.root/name).write_text('original', encoding='utf8')

    def release(self, name, fingerprint=None):
        app = self.root/'release'/name/builder.APP_NAME
        app.mkdir(parents=True)
        (app/'_internal').mkdir()
        exe = app/(builder.APP_NAME+'.exe')
        exe.write_bytes(b'test executable')
        (app/'build-info.json').write_text(json.dumps({
            'source_sha256': fingerprint or builder.source_digest(self.root),
            'self_test': 'passed',
        }), encoding='utf8')
        return exe

    def test_current_release_skips_build_even_with_newer_stale_exe(self):
        current = self.release('current')
        stale = self.release('stale', 'outdated')
        os.utime(current, (100, 100))
        os.utime(stale, (200, 200))
        with patch.object(builder, 'main') as build:
            self.assertEqual(builder.ensure_latest(self.root), current)
        build.assert_not_called()

    def test_missing_release_builds_then_returns_verified_exe(self):
        with patch.object(builder, 'main', side_effect=lambda argv: (self.release('new'), 0)[1]) as build:
            exe = builder.ensure_latest(self.root)
        self.assertTrue(exe.is_file())
        build.assert_called_once_with(['--backup-git'])

    def test_changed_source_triggers_build(self):
        self.release('old')
        (self.root/'core'/'parser.py').write_text('changed', encoding='utf8')
        with patch.object(builder, 'main', side_effect=lambda argv: (self.release('new'), 0)[1]) as build:
            self.assertIn('new', builder.ensure_latest(self.root).parts)
        build.assert_called_once()

    def test_failed_build_does_not_fall_back_to_old_exe(self):
        self.release('old', 'outdated')
        with patch.object(builder, 'main', return_value=1):
            with self.assertRaisesRegex(RuntimeError, '自动打包失败'):
                builder.ensure_latest(self.root)

    def test_changed_source_after_build_is_rejected(self):
        def build(argv):
            self.release('new')
            (self.root/'app.py').write_text('edited during publishing')
            return 0
        with patch.object(builder, 'main', side_effect=build):
            with self.assertRaisesRegex(RuntimeError, '源码又发生变化'):
                builder.ensure_latest(self.root)

    def test_invalid_manifest_and_missing_runtime_are_rejected(self):
        exe = self.release('invalid')
        info = exe.with_name('build-info.json')
        for content in ('invalid', '[]', '{"self_test":"failed"}'):
            info.write_text(content)
            self.assertIsNone(builder.matching_release(self.root, builder.source_digest(self.root)))
        exe = self.release('incomplete')
        (exe.parent/'_internal').rmdir()
        self.assertIsNone(builder.matching_release(self.root, builder.source_digest(self.root)))

    def test_digest_tracks_build_recipe_but_not_local_settings(self):
        original = builder.source_digest(self.root)
        (self.root/'settings.json').write_text('{"local":true}')
        self.assertEqual(builder.source_digest(self.root), original)
        (self.root/'scripts'/'build_windows.py').write_text('new recipe')
        self.assertNotEqual(builder.source_digest(self.root), original)

    @unittest.skipUnless(os.name == 'nt', 'Windows file locking')
    def test_lock_prevents_concurrent_build_and_is_released(self):
        with builder.launch_build_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, '等待自动打包超时'):
                with builder.launch_build_lock(self.root, timeout=0):
                    self.fail('second process must wait')
        with builder.launch_build_lock(self.root, timeout=0):
            pass

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher')
    def test_powershell_reads_worker_exit_code_and_utf8_path(self):
        launcher = self.root/'launch_latest.ps1'
        shutil.copy2(builder.ROOT/'launch_latest.ps1', launcher)
        exe = self.root/(builder.APP_NAME+'.exe')
        exe.write_bytes(b'not launched in ResolveOnly mode')
        command = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; & '{}' -ResolveOnly".format(
            str(launcher).replace("'", "''"))
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                (self.root/'scripts'/'build_windows.py').write_text(
                    f'import sys\nprint({str(exe)!r})\n'
                    f'print("worker detail", file=sys.stderr)\nsys.exit({exit_code})\n',
                    encoding='utf8')
                completed = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
                    capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                if exit_code == 0:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout.decode('utf8').strip(), str(exe))
                else:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(b'worker detail', completed.stderr)


if __name__ == '__main__':
    unittest.main()

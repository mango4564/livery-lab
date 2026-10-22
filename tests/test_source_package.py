"""Sharing archives contain only the allowlisted, verified source demo."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from scripts import package_source


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        validation = patch.object(package_source, 'validate_legacy_syntax')
        self.validate = validation.start()
        self.addCleanup(validation.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root/'core').mkdir()
        (self.root/'scripts').mkdir()
        for module in package_source.MODULES:
            (self.root/(module+'.py')).write_text('# source\n')
        (self.root/'core'/'__init__.py').write_text('# core\n')
        (self.root/'scripts'/'source_runtime.py').write_text('# runtime\n')
        (self.root/'requirements.txt').write_text('olefile==0.47\n')
        (self.root/'settings.example.json').write_text('{"enabled":false}')

    def test_staged_archive_preserves_source_and_excludes_local_files(self):
        for name in ('settings.json', '.api-key.dpapi', '.env', 'private.fs', 'old.exe'):
            (self.root/name).write_text('private local content')
        destination = self.root/'build'/'同事体验.zip'
        result = package_source.create_archive(destination, root=self.root)
        self.validate.assert_called_once()
        self.assertEqual(result['archive'], str(destination))
        self.assertFalse((self.root/'release').exists())
        self.assertEqual(result['sha256'], hashlib.sha256(destination.read_bytes()).hexdigest())
        with zipfile.ZipFile(destination) as bundle:
            self.assertIsNone(bundle.testzip())
            files = {name.split('/', 1)[1]: bundle.read(name) for name in bundle.namelist()}
        self.assertEqual(result['file_count'], len(files))
        expected = {name+'.py' for name in package_source.MODULES} | {
            'core/__init__.py', 'scripts/source_runtime.py', 'requirements.txt',
            'settings.example.json', '01_首次安装环境.cmd', '02_启动程序.cmd',
            '使用说明.txt', '文件校验.json',
        }
        self.assertEqual(set(files), expected)
        for name, digest in json.loads(files['文件校验.json'])['files_sha256'].items():
            self.assertEqual(hashlib.sha256(files[name]).hexdigest(), digest)
        self.assertEqual(files['app.py'], (self.root/'app.py').read_bytes())
        self.assertIn(b'--check', files['02_启动程序.cmd'])
        self.assertIn(b'--install', files['01_首次安装环境.cmd'])
        with self.assertRaises(FileExistsError):
            package_source.create_archive(destination, root=self.root)

    def test_nonblank_template_is_rejected_before_writing_archive(self):
        (self.root/'settings.example.json').write_text('{"api_key":"local-key"}')
        destination = self.root/'unsafe.zip'
        with self.assertRaisesRegex(RuntimeError, 'blank and disabled'):
            package_source.create_archive(destination, root=self.root)
        self.assertFalse(destination.exists())

    def test_missing_local_dependency_is_rejected(self):
        (self.root/'app.py').write_text('import newly_added_module\n')
        (self.root/'newly_added_module.py').write_text('# missing from allowlist')
        with self.assertRaisesRegex(RuntimeError, 'Missing local dependency'):
            package_source.payload(self.root)


class LegacySyntaxTests(unittest.TestCase):
    def test_exact_payload_is_compiled_by_python310(self):
        with patch.object(package_source.sys, 'version_info', (3, 10)), \
             patch.object(package_source.shutil, 'which', return_value=None), \
             patch.object(package_source.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            package_source.validate_legacy_syntax({'ui.py': b'print("ok")', 'settings.example.json': b'{}'})
        self.assertEqual(json.loads(run.call_args.kwargs['input']), {'ui.py': 'print("ok")'})
        self.assertIn('compile(source', run.call_args.args[0][-1])

    def test_incompatible_syntax_stops_archive_creation(self):
        with patch.object(package_source.sys, 'version_info', (3, 10)), \
             patch.object(package_source.shutil, 'which', return_value=None), \
             patch.object(package_source.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=1, stderr='SyntaxError: f-string: unmatched')):
            with self.assertRaisesRegex(RuntimeError, 'SyntaxError'):
                package_source.validate_legacy_syntax({'ui.py': b'f"{x.get("key")}"'})

    def test_missing_checker_stops_instead_of_skipping(self):
        with patch.object(package_source.sys, 'version_info', (3, 13)), \
             patch.object(package_source.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '需要 Python 3.10'):
                package_source.validate_legacy_syntax({'ui.py': b'pass'})

    def test_unavailable_launcher_version_falls_back_to_uv_python(self):
        results = [SimpleNamespace(returncode=1, stdout=''),
                   SimpleNamespace(returncode=0, stdout='C:/managed/python.exe\n'),
                   SimpleNamespace(returncode=0)]
        with patch.object(package_source.sys, 'version_info', (3, 13)), \
             patch.object(package_source.shutil, 'which', side_effect=lambda name: name), \
             patch.object(package_source.subprocess, 'run', side_effect=results) as run:
            package_source.validate_legacy_syntax({'ui.py': b'pass'})
        self.assertEqual(run.call_args.args[0][0], 'C:/managed/python.exe')


if __name__ == '__main__':
    unittest.main()

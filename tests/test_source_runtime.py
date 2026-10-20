"""Offline checks for dependency reuse across extracted source packages."""
from importlib import metadata
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import source_runtime


class SharedDependenciesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'requirements.txt').write_text('olefile==0.47\nPillow==12.2.0\n')

    def test_supported_versions_and_incompatible_architectures(self):
        with patch.object(source_runtime.sys, 'prefix', 'base'), \
             patch.object(source_runtime.sys, 'base_prefix', 'base'), \
             patch.object(source_runtime.sys, 'maxsize', 2**63 - 1), \
             patch.object(source_runtime.platform, 'machine', return_value='AMD64'), \
             patch.object(source_runtime.sysconfig, 'get_config_var', return_value=0):
            for minor in (10, 11, 12, 13, 14):
                with self.subTest(minor=minor), patch.object(source_runtime.sys, 'version_info', (3, minor)):
                    self.assertTrue(source_runtime.supported_runtime())
            for minor in (9, 15):
                with self.subTest(minor=minor), patch.object(source_runtime.sys, 'version_info', (3, minor)):
                    self.assertFalse(source_runtime.supported_runtime())
            with patch.object(source_runtime.platform, 'machine', return_value='ARM64'):
                self.assertFalse(source_runtime.supported_runtime())
            with patch.object(source_runtime.sys, 'maxsize', 2**31 - 1):
                self.assertFalse(source_runtime.supported_runtime())
            with patch.object(source_runtime.sysconfig, 'get_config_var', return_value=1):
                self.assertFalse(source_runtime.supported_runtime())
            with patch.object(source_runtime.sys, 'prefix', 'virtual-env'):
                self.assertFalse(source_runtime.supported_runtime())

    def test_two_extracted_versions_reuse_installed_packages_without_pip(self):
        second = self.root / 'another_version'
        second.mkdir()
        (second / 'requirements.txt').write_bytes((self.root / 'requirements.txt').read_bytes())
        with patch.object(source_runtime.metadata, 'version', side_effect=lambda name: {'olefile': '0.47', 'Pillow': '12.2.0'}[name]), \
             patch.object(source_runtime.importlib, 'import_module'), \
             patch.object(source_runtime.subprocess, 'run', side_effect=AssertionError('No pip or network')):
            self.assertEqual(source_runtime.install_dependencies(self.root), 0)
            self.assertEqual(source_runtime.install_dependencies(second), 0)

    def test_missing_and_wrong_versions_are_reported(self):
        def version(name):
            if name == 'olefile':
                raise metadata.PackageNotFoundError(name)
            return '0.0'
        with patch.object(source_runtime.metadata, 'version', side_effect=version):
            problems = source_runtime.check_dependencies(self.root)
        self.assertEqual(len(problems), 2)
        self.assertIn('not installed', problems[0])
        self.assertIn('installed 0.0', problems[1])

    def test_missing_packages_use_user_install_and_recheck_in_fresh_process(self):
        with patch.object(source_runtime, 'check_dependencies', return_value=['missing']), \
             patch.object(source_runtime.site, 'ENABLE_USER_SITE', True), \
             patch.object(source_runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(source_runtime.install_dependencies(self.root), 0)
        install = run.call_args_list[0].args[0]
        self.assertIn('--user', install)
        self.assertNotIn('--upgrade', install)
        self.assertIn(str(self.root / 'requirements.txt'), install)
        self.assertEqual(run.call_args_list[1].args[0][-1], '--check')

    def test_failed_install_stops_and_returns_failure(self):
        with patch.object(source_runtime, 'check_dependencies', return_value=['missing']), \
             patch.object(source_runtime.site, 'ENABLE_USER_SITE', True), \
             patch.object(source_runtime.subprocess, 'run', return_value=SimpleNamespace(returncode=7)) as run:
            self.assertEqual(source_runtime.install_dependencies(self.root), 7)
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()

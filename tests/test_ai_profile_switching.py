"""Local profile switching: preserve options, exact backup, rollback, no network."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import switch_ai


class AIProfileSwitchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root/'release/version/彩条提取工作台'
        self.app.mkdir(parents=True)
        self.config = {'enabled': False, 'base_url': 'http://old.invalid/v1',
                       'model': 'old', 'api_key': 'old-test-key', 'fast_mode': True,
                       'timeout_seconds': 240, 'audit_enabled': True, 'custom_option': [1, 2]}
        self.original = json.dumps(self.config).encode('utf-8-sig')
        for directory in (self.root, self.app):
            (directory/'settings.json').write_bytes(self.original)
        self.updates = {'enabled': True, 'base_url': 'http://new.invalid/v1',
                        'model': 'new', 'api_key': 'new-test-key'}

    def test_preserves_other_settings_and_exact_backups(self):
        backup, changed = switch_ai.switch_profile(self.root, [self.root, self.app], self.updates)
        self.assertEqual(len(changed), 2)
        for path in changed:
            actual = switch_ai.read_object(path)
            for key in ('fast_mode', 'timeout_seconds', 'audit_enabled', 'custom_option'):
                self.assertEqual(actual[key], self.config[key])
            self.assertEqual(actual['model'], 'new')
        manifest = json.loads((backup/'manifest.json').read_text(encoding='utf8'))
        for entry in manifest:
            self.assertEqual((backup/entry['backup']).read_bytes(), self.original)
        self.assertEqual(switch_ai.switch_profile(self.root, [self.root, self.app], self.updates), (None, []))

    def test_invalid_later_target_leaves_all_targets_unchanged(self):
        (self.app/'settings.json').write_text('invalid JSON', encoding='utf8')
        with self.assertRaises(ValueError):
            switch_ai.switch_profile(self.root, [self.root, self.app], self.updates)
        self.assertEqual((self.root/'settings.json').read_bytes(), self.original)
        self.assertFalse((self.root/'.local').exists())

    def test_failed_second_write_rolls_back_first(self):
        real = switch_ai.atomic_write
        def write(path, data):
            if path == self.app/'settings.json':
                raise OSError('simulated locked file')
            real(path, data)
        with patch.object(switch_ai, 'atomic_write', side_effect=write), self.assertRaisesRegex(ValueError, '已回滚'):
            switch_ai.switch_profile(self.root, [self.root, self.app], self.updates)
        self.assertEqual((self.root/'settings.json').read_bytes(), self.original)
        self.assertEqual((self.app/'settings.json').read_bytes(), self.original)

    def test_default_targets_include_source_latest_and_running_versions(self):
        exe = self.app/'彩条提取工作台.exe'
        exe.write_bytes(b'test')
        (self.app/'build-info.json').write_text('{}', encoding='utf8')
        running = self.root/'release/older/彩条提取工作台'
        running.mkdir(parents=True)
        with patch.object(switch_ai, 'running_app_directories', return_value=[running, self.app]):
            self.assertEqual(switch_ai.discover_targets(self.root), [self.root, self.app, running])

    def test_preview_never_writes_or_prints_secrets(self):
        private = self.root/'profiles.json'
        private.write_text(json.dumps({'gemini': self.updates}), encoding='utf8')
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = switch_ai.main(['gemini', '--root', str(self.root), '--app-dir', str(self.app),
                                   '--profiles', str(private), '--dry-run'])
        self.assertEqual(code, 0)
        self.assertNotIn(self.updates['api_key'], output.getvalue())
        self.assertNotIn(self.updates['base_url'], output.getvalue())
        self.assertEqual((self.app/'settings.json').read_bytes(), self.original)

    def test_profile_rejects_credentials_in_url_and_non_boolean_proxy(self):
        path = self.root/'profiles.json'
        for extra in ({'base_url': 'http://user:password@example.invalid/v1'}, {'use_system_proxy': 'false'}):
            path.write_text(json.dumps({'internal': dict(self.updates, **extra)}), encoding='utf8')
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                switch_ai.load_profile(path, 'internal')

    def test_tuning_resets_between_profiles_and_rejects_invalid_values(self):
        path=self.root/'profiles.json'
        profiles={'internal':dict(self.updates,component_batch_size=12,prompt_style='compact',
                                  max_output_tokens=8192,include_usage=True),
                  'gemini':dict(self.updates,model='gemini')}
        path.write_text(json.dumps(profiles),encoding='utf8')
        internal=switch_ai.load_profile(path,'internal')
        gemini=switch_ai.load_profile(path,'gemini')
        self.assertEqual(internal['max_output_tokens'],8192)
        self.assertEqual(gemini['max_output_tokens'],0)
        self.assertEqual(gemini['component_batch_size'],'auto')
        self.assertFalse(gemini['include_usage'])
        profiles['internal']['component_batch_size']=-2
        path.write_text(json.dumps(profiles),encoding='utf8')
        with self.assertRaises(ValueError):switch_ai.load_profile(path,'internal')


if __name__ == '__main__':
    unittest.main()

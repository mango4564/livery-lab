"""Offline checks for comparison profile, process and output isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import compare_ai


class AIComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = {'timeout_seconds': 240, 'enabled': False, 'api_key': 'original'}
        (self.root / 'settings.json').write_text(json.dumps(self.base), encoding='utf8')
        self.profiles = self.root / 'profiles.json'
        self.profiles.write_text(json.dumps({name: {
            'model': name, 'base_url': f'http://{name}.invalid/v1',
            'api_key': name + '-test-key', 'use_system_proxy': False,
        } for name in compare_ai.LABELS}), encoding='utf8')

    def test_profiles_reach_child_and_worker_without_parent_overrides(self):
        with patch.dict(os.environ, {'LIVERY_MODEL': 'wrong', 'LIVERY_API_KEY': 'wrong',
                                     'LIVERY_ENABLED': 'false', 'LIVERY_BASE_URL': 'wrong'}):
            plans = compare_ai.prepare_instances(self.root, self.profiles)
        probe = ('import json, os, subprocess, sys; from vision import read_config; '
                 'config=read_config(); '
                 'worker=subprocess.check_output([sys.executable,"-c",'
                 '"import json; from vision import read_config; print(json.dumps(read_config()))"]); '
                 'assert config==json.loads(worker); print(json.dumps(config))')
        for plan in plans:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', probe],
                                    env=plan['env'], cwd=compare_ai.ROOT,
                                    capture_output=True, text=True, check=True)
            config = json.loads(result.stdout)
            self.assertEqual(config['model'], plan['name'])
            self.assertEqual(config['api_key'], plan['name'] + '-test-key')
            self.assertTrue(config['enabled'])
            self.assertEqual(config['timeout_seconds'], 240)
            self.assertFalse(config['use_system_proxy'])
        self.assertNotEqual(plans[0]['output'], plans[1]['output'])
        self.assertNotEqual(plans[0]['env']['LIVERY_CONFIG_PATH'], plans[1]['env']['LIVERY_CONFIG_PATH'])
        self.assertEqual(json.loads((self.root / 'settings.json').read_text()), self.base)
        again = compare_ai.prepare_instances(self.root, self.profiles)
        self.assertNotEqual(plans[0]['output'], again[0]['output'])

    def test_invalid_second_profile_launches_nothing_and_writes_nothing(self):
        self.profiles.write_text(json.dumps({'gemini': {
            'model': 'test', 'base_url': 'http://offline.invalid/v1', 'api_key': 'test-key'}}))
        with self.assertRaises(ValueError):
            compare_ai.prepare_instances(self.root, self.profiles)
        self.assertFalse((self.root / '.local').exists())

    def test_dry_run_has_no_side_effects(self):
        compare_ai.prepare_instances(self.root, self.profiles, dry_run=True)
        self.assertFalse((self.root / '.local').exists())
        self.assertFalse((self.root / 'output').exists())

    def test_launch_passes_separate_environments_without_keys_in_command(self):
        plans = compare_ai.prepare_instances(self.root, self.profiles)
        with patch.object(compare_ai.subprocess, 'Popen') as spawn:
            compare_ai.launch_instances(self.root, plans)
        self.assertEqual(spawn.call_count, 2)
        for call, plan in zip(spawn.call_args_list, plans):
            self.assertEqual(call.kwargs['env'], plan['env'])
            self.assertNotIn('test-key', str(call.args[0]))


if __name__ == '__main__':
    unittest.main()

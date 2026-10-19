"""Local release configuration inheritance without live credentials or APIs."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.build_windows import inherit_local_config


class PackagingConfigTests(unittest.TestCase):
    def test_latest_config_copies_matching_key_instead_of_root_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = root/'new'; app.mkdir()
            old = root/'settings.json'; old.write_text('{"model":"old"}')
            (root/'.api-key.dpapi').write_bytes(b'old-ciphertext')
            saved = root/'release'/'previous'/'彩条提取工作台'; saved.mkdir(parents=True)
            config = saved/'settings.json'
            config.write_text(json.dumps({'enabled': True, 'model': 'new'}))
            (saved/'.api-key.dpapi').write_bytes(b'matching-ciphertext')
            os.utime(old, (100, 100)); os.utime(config, (200, 200))
            self.assertTrue(inherit_local_config(root, app))
            self.assertEqual(json.loads((app/'settings.json').read_text())['model'], 'new')
            self.assertEqual((app/'.api-key.dpapi').read_bytes(), b'matching-ciphertext')

    def test_no_config_and_no_matching_key_are_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); app = root/'new'; app.mkdir()
            self.assertFalse(inherit_local_config(root, app))
            (root/'settings.json').write_text('{"api_key_env":"LIVERY_API_KEY"}')
            self.assertTrue(inherit_local_config(root, app))
            self.assertFalse((app/'.api-key.dpapi').exists())

    def test_invalid_config_or_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); app = root/'new'; app.mkdir()
            config = root/'settings.json'; config.write_text('invalid')
            with self.assertRaises(RuntimeError):
                inherit_local_config(root, app)
            self.assertFalse((app/'settings.json').exists())
            config.write_text('{}'); (app/'settings.json').write_text('{"keep":true}')
            with self.assertRaises(RuntimeError):
                inherit_local_config(root, app)
            self.assertEqual((app/'settings.json').read_text(), '{"keep":true}')

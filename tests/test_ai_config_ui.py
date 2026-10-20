"""Offline UI checks for plain JSON configuration and key editing."""
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from tests.tk_lifecycle import TkTestCase
from unittest.mock import patch

from ui import LiveryApp
import vision


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class PlainConfigTests(TkTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'settings.json'
        for mock in (patch.object(vision, 'CONFIG_PATH', self.path),
                     patch.object(vision, 'load_api_key', return_value=None),
                     patch.dict(os.environ, {}, clear=True),
                     patch.object(vision.request, 'build_opener', side_effect=AssertionError('No network'))):
            mock.start()
            self.addCleanup(mock.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = LiveryApp(self.root)
        self.addCleanup(self.app._close)

    def open_fields(self):
        self.app._configure_ai()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        entries = [w for w in descendants(dialog) if isinstance(w, ttk.Entry)]
        self.assertEqual(len(entries), 3)
        self.assertEqual(str(entries[2].cget('show')), '')
        labels = [str(w.cget('text')) for w in descendants(dialog) if isinstance(w, ttk.Label)]
        self.assertIn('API 密钥', labels)
        return dialog, entries

    def save(self, dialog):
        next(w for w in descendants(dialog) if isinstance(w, ttk.Button)
             and w.cget('text') == '保存配置').invoke()
        self.assertFalse(dialog.winfo_exists())

    def test_save_reopen_replace_and_clear_plain_key_preserving_other_settings(self):
        self.path.write_text(json.dumps({'enabled': True, 'base_url': 'http://offline.invalid/v1',
            'model': 'offline', 'api_key': 'old-test-key', 'allow_insecure': False,
            'custom_option': 'preserved'}), encoding='utf8')
        for expected, replacement in [('old-test-key', 'new-test-key'), ('new-test-key', '')]:
            dialog, entries = self.open_fields()
            self.assertEqual(entries[2].get(), expected)
            entries[2].delete(0, tk.END)
            entries[2].insert(0, replacement)
            self.save(dialog)
            data = json.loads(self.path.read_text(encoding='utf8'))
            self.assertEqual(data['api_key'], replacement)
            self.assertEqual(data['custom_option'], 'preserved')
            self.assertEqual(vision.resolve_api_key(vision.read_config()), replacement)
            self.assertFalse((self.path.parent / '.api-key.dpapi').exists())
        dialog, entries = self.open_fields()
        self.assertEqual(entries[2].get(), '')
        dialog.destroy()

    def test_legacy_encrypted_key_is_prefilled_and_saved_as_json(self):
        self.path.write_text('{}', encoding='utf8')
        with patch.object(vision, 'load_api_key', return_value='legacy-test-key'):
            dialog, entries = self.open_fields()
            self.assertEqual(entries[2].get(), 'legacy-test-key')
            self.save(dialog)
        self.assertEqual(json.loads(self.path.read_text())['api_key'], 'legacy-test-key')

    def test_remote_http_requires_no_extra_option(self):
        self.assertEqual(vision._endpoint({'base_url': 'http://offline.invalid/v1'}),
                         'http://offline.invalid/v1/chat/completions')


if __name__ == '__main__':
    unittest.main()

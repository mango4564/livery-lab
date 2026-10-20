"""Offline random-key tests; never includes or prints any actual credential."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

import credentials
import vision


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / '.api-key.dpapi'
        self.key = secrets.token_urlsafe(48)

    def test_missing_file_returns_none(self):
        self.assertIsNone(credentials.load_api_key(self.path))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI required')
    def test_random_roundtrip_never_stores_plaintext(self):
        returned_path = credentials.save_api_key(self.key, self.path)
        self.assertEqual(returned_path, self.path)
        self.assertTrue(credentials.load_api_key(self.path) == self.key)
        self.assertNotIn(self.key.encode(), self.path.read_bytes(), 'Ciphertext must not contain plaintext')
        first = self.path.read_bytes()
        credentials.save_api_key(self.key, self.path)
        self.assertNotEqual(first, self.path.read_bytes(), 'DPAPI uses randomized encryption')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI required')
    def test_corrupted_ciphertext_is_sanitized(self):
        credentials.save_api_key(self.key, self.path)
        data = bytearray(self.path.read_bytes())
        data[-1] ^= 0x80
        self.path.write_bytes(data)
        with self.assertRaises(credentials.CredentialError) as raised:
            credentials.load_api_key(self.path)
        self.assertTrue(self.key not in str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_invalid_key_and_file_do_not_leak_input(self):
        with self.assertRaises(credentials.CredentialError) as raised:
            credentials.save_api_key(self.key + '\n', self.path)
        self.assertTrue(self.key not in str(raised.exception))
        self.assertFalse(self.path.exists())
        self.path.write_bytes(self.key.encode())
        with self.assertRaises(credentials.CredentialError) as raised:
            credentials.load_api_key(self.path)
        self.assertTrue(self.key not in str(raised.exception))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI required')
    def test_os_failure_does_not_include_low_level_secret(self):
        with patch.object(credentials.ctypes, 'WinDLL', side_effect=OSError(self.key)):
            with self.assertRaises(credentials.CredentialError) as raised:
                credentials.save_api_key(self.key, self.path)
        self.assertTrue(self.key not in str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI required')
    def test_save_failure_preserves_previous_encrypted_key(self):
        credentials.save_api_key(self.key, self.path)
        before = self.path.read_bytes()
        replacement = secrets.token_urlsafe(48)
        with patch.object(credentials.os, 'replace', side_effect=OSError(replacement)):
            with self.assertRaises(credentials.CredentialError) as raised:
                credentials.save_api_key(replacement, self.path)
        self.assertTrue(replacement not in str(raised.exception))
        self.assertTrue(before == self.path.read_bytes())
        self.assertTrue(credentials.load_api_key(self.path) == self.key)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_plain_config_then_legacy_environment_then_dpapi_precedence(self):
        config = {'api_key_env': 'RANDOM_TEST_CREDENTIAL', 'api_key': secrets.token_urlsafe(48)}
        encrypted_key = secrets.token_urlsafe(48)
        with patch.dict(os.environ, {'RANDOM_TEST_CREDENTIAL': self.key}, clear=True), patch.object(vision, 'load_api_key') as load:
            self.assertTrue(vision.resolve_api_key(config) == config['api_key'])
            load.assert_not_called()
            self.assertTrue(vision.resolve_api_key({'api_key_env': 'RANDOM_TEST_CREDENTIAL'}) == self.key)
        with patch.dict(os.environ, {}, clear=True), patch.object(vision, 'load_api_key') as load:
            self.assertTrue(vision.resolve_api_key(config) == config['api_key'])
            load.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch.object(vision, 'load_api_key', return_value=encrypted_key):
            self.assertTrue(vision.resolve_api_key({'api_key_env': 'RANDOM_TEST_CREDENTIAL'}) == encrypted_key)

    def test_status_recognizes_dpapi_without_contacting_service(self):
        config = {'enabled': True, 'base_url': 'https://offline.invalid/v1', 'model': 'offline-model'}
        with patch.dict(os.environ, {}, clear=True), patch.object(vision, 'load_api_key', return_value=self.key), patch.object(vision.request, 'build_opener', side_effect=AssertionError('Network forbidden')):
            status = vision.configuration_status(config)
            self.assertIn('已配置', status)
            self.assertTrue(self.key not in status)

    def test_explicit_empty_key_does_not_restore_an_old_credential(self):
        with patch.dict(os.environ, {'LIVERY_API_KEY': self.key}), patch.object(vision, 'load_api_key') as load:
            self.assertEqual(vision.resolve_api_key({'api_key': ''}), '')
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""The app can bypass the system HTTP proxy without changing other programs."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib import request

import vision


class VisionProxyTests(unittest.TestCase):
    def test_default_keeps_system_proxy_and_redirect_protection(self):
        with patch.object(vision.request, 'build_opener') as build:
            vision._network_opener()
        handlers = build.call_args.args
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], vision._NoRedirect)

    def test_direct_mode_disables_proxy_and_keeps_redirect_protection(self):
        with patch.object(vision.request, 'build_opener') as build:
            vision._network_opener(False)
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], vision._NoRedirect)

    def test_config_and_environment_override_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            path.write_text(json.dumps({'use_system_proxy': False}), encoding='utf8')
            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(vision.read_config(path)['use_system_proxy'])
            with patch.dict(os.environ, {'LIVERY_USE_SYSTEM_PROXY': 'true'}, clear=True):
                self.assertTrue(vision.read_config(path)['use_system_proxy'])
            with patch.dict(os.environ, {'LIVERY_USE_SYSTEM_PROXY': 'invalid'}, clear=True):
                with self.assertRaises(vision.VisionError):
                    vision.read_config(path)

    def test_connection_and_retry_transport_use_the_same_selected_mode(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"choices":[{"message":{"content":"pong"}}]}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(vision, '_network_opener', return_value=opener) as build:
            vision.test_connection({'base_url': 'https://example.invalid/v1',
                                    'model': 'vision', 'api_key': 'test-key',
                                    'use_system_proxy': False})
            build.assert_called_once_with(False)
            build.reset_mock()
            vision._request_bytes(request.Request('https://example.invalid'), 90, 1024,
                                  use_system_proxy=False)
            build.assert_called_once_with(False)


if __name__ == '__main__':
    unittest.main()

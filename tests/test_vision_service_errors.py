"""Offline service-failure diagnostics; no network or real credentials."""
from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import vision


class ServiceErrorTests(unittest.TestCase):
    def failure(self, code):
        return HTTPError('https://example.invalid/v1/chat/completions', code,
                         'private upstream address', {}, BytesIO(b'private-key-and-body'))

    def test_connection_identifies_upstream_service_failure_and_closes_error(self):
        config = {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'test-key'}
        for code in (502, 503, 504):
            with self.subTest(code=code):
                failure = self.failure(code)
                opener = MagicMock()
                opener.open.side_effect = failure
                with patch.object(vision, '_network_opener', return_value=opener), \
                     self.assertRaises(vision.VisionError) as caught:
                    vision.test_connection(config)
                message = str(caught.exception)
                self.assertIn(str(code), message)
                self.assertIn('服务方', message)
                self.assertNotIn('private', message)
                self.assertTrue(failure.fp.closed)

    def test_recognition_preserves_retries_and_explains_503(self):
        failures = [self.failure(503) for _ in range(3)]
        opener = MagicMock()
        opener.open.side_effect = failures
        with patch.object(vision, '_network_opener', return_value=opener), \
             patch.object(vision.time, 'sleep'), \
             self.assertRaises(vision.VisionError) as caught:
            vision._request_bytes(Request('https://example.invalid/v1', data=b'{}'), 90, 1024)
        message = str(caught.exception)
        self.assertIn('503', message)
        self.assertIn('上游端口连通性', message)
        self.assertNotIn('private', message)
        self.assertEqual(opener.open.call_count, 3)
        self.assertTrue(all(e.fp.closed for e in failures))

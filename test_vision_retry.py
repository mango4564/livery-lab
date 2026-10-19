"""Transport regressions; no real network or credentials."""
from io import BytesIO
from urllib import error, request
import unittest
from unittest.mock import MagicMock, patch

import vision


class VisionRetryTests(unittest.TestCase):
    def setUp(self):
        self.opener = MagicMock()
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.read.return_value = b'{"ok":true}'
        self.req = request.Request('https://example.invalid/v1/chat/completions', data=b'{}')
        patcher = patch.object(vision.request, 'build_opener', return_value=self.opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(vision.time, 'sleep')
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def http_error(self, code, headers=None):
        return error.HTTPError(self.req.full_url, code, 'secret-server-message',
                               headers or {}, BytesIO(b'secret-body'))

    def test_502_then_success_replays_identical_request(self):
        self.opener.open.side_effect = [self.http_error(502), self.response]
        self.assertEqual(vision._request_bytes(self.req, 90, 1024), b'{"ok":true}')
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertTrue(all(c.args[0] is self.req for c in self.opener.open.call_args_list))
        self.sleep.assert_called_once()

    def test_persistent_502_is_bounded_and_sanitized(self):
        failures = [self.http_error(502) for _ in range(3)]
        self.opener.open.side_effect = failures
        with self.assertRaises(vision.VisionError) as caught:
            vision._request_bytes(self.req, 90, 1024)
        self.assertIn('502', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertTrue(all(e.fp.closed for e in failures))

    def test_auth_and_invalid_requests_do_not_retry(self):
        for code in (400, 401, 403, 404, 413):
            with self.subTest(code=code):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = self.http_error(code)
                with self.assertRaises(vision.VisionError):
                    vision._request_bytes(self.req, 90, 1024)
                self.opener.open.assert_called_once()
        self.sleep.assert_not_called()

    def test_timeout_and_connection_failures_can_recover(self):
        self.opener.open.side_effect = [TimeoutError(), error.URLError('secret'), self.response]
        self.assertEqual(vision._request_bytes(self.req, 90, 1024), b'{"ok":true}')

    def test_retry_after_and_total_budget(self):
        self.opener.open.side_effect = [self.http_error(429, {'Retry-After': '5'}), self.response]
        self.assertTrue(vision._request_bytes(self.req, 90, 1024))
        self.sleep.assert_called_once_with(5.0)
        self.sleep.reset_mock()
        self.opener.open.reset_mock()
        self.opener.open.side_effect = self.http_error(502)
        with patch.object(vision.time, 'monotonic', side_effect=[0, 0, 9.9]):
            with self.assertRaises(vision.VisionError):
                vision._request_bytes(self.req, 10, 1024)
        self.opener.open.assert_called_once()
        self.sleep.assert_not_called()

    def test_redirect_and_oversize_responses_are_not_retried(self):
        self.opener.open.side_effect = vision.VisionError('redirect refused')
        with self.assertRaises(vision.VisionError):
            vision._request_bytes(self.req, 90, 1024)
        self.opener.open.reset_mock()
        self.opener.open.side_effect = None
        self.opener.open.return_value = self.response
        self.response.read.return_value = b'x' * 1025
        with self.assertRaises(vision.VisionError):
            vision._request_bytes(self.req, 90, 1024)
        self.opener.open.assert_called_once()
        self.sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()

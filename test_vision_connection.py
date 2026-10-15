import unittest
from unittest.mock import patch, MagicMock
from io import BytesIO
from urllib.error import HTTPError
import json

from vision import test_connection, VisionError

class TestVisionConnection(unittest.TestCase):
    def test_missing_model(self):
        config = {"base_url": "https://api.openai.com/v1", "model": "", "api_key": "sk-123456"}
        with self.assertRaises(VisionError) as ctx:
            test_connection(config)
        self.assertIn("模型尚未配置", str(ctx.exception))

    @patch("vision.resolve_api_key", return_value=None)
    def test_missing_key(self, mock_resolve_key):
        config = {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"}
        with self.assertRaises(VisionError) as ctx:
            test_connection(config)
        self.assertIn("密钥尚未配置", str(ctx.exception))

    def test_invalid_endpoint(self):
        config = {"base_url": "ftp://bad-url", "model": "gpt-4o", "api_key": "sk-123456"}
        with self.assertRaises(VisionError) as ctx:
            test_connection(config)
        self.assertIn("http", str(ctx.exception))

    @patch("urllib.request.build_opener")
    def test_successful_connection(self, mock_opener_cls):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "pong"}}]
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_response
        mock_opener_cls.return_value = mock_opener

        config = {"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "sk-testkey123"}
        res = test_connection(config)
        self.assertTrue(res["ok"])
        self.assertIn("连接成功", res["message"])

    @patch("urllib.request.build_opener")
    def test_http_401_auth_failure(self, mock_opener_cls):
        mock_opener = MagicMock()
        mock_opener.open.side_effect = HTTPError("url", 401, "Unauthorized", {}, BytesIO(b""))
        mock_opener_cls.return_value = mock_opener

        config = {"base_url": "https://api.openai.com/v1", "model": "gpt-4o", "api_key": "sk-badkey"}
        with self.assertRaises(VisionError) as ctx:
            test_connection(config)
        self.assertIn("鉴权失败", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

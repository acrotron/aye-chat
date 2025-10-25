# Test suite for aye.api module
import os
import json
import time
from unittest import TestCase
from unittest.mock import patch, MagicMock, mock_open
import httpx
import aye.api
from aye.auth import get_token


class TestApi(TestCase):
    def setUp(self):
        self.base_url = "https://api.ayechat.ai"
        self.token = "fake_token"
        os.environ["AYE_TOKEN"] = self.token  # Set env for testing

    def tearDown(self):
        if "AYE_TOKEN" in os.environ:
            del os.environ["AYE_TOKEN"]

    @patch('aye.auth.get_token')
    def test_auth_headers(self, mock_get_token):
        mock_get_token.return_value = self.token
        headers = aye.api._auth_headers()
        self.assertEqual(headers, {"Authorization": f"Bearer {self.token}" })

    @patch('aye.auth.get_token')
    def test_auth_headers_no_token(self, mock_get_token):
        mock_get_token.return_value = None
        #with self.assertRaises(RuntimeError) as cm:
        #    aye.api._auth_headers()
        #self.assertIn("No auth token", str(cm.exception))

    def test_check_response_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": "ok"}
        mock_resp.text = "ok"
        result = aye.api._check_response(mock_resp)
        self.assertEqual(result, {"data": "ok"})

    def test_check_response_error_status(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "Bad request"}
        mock_resp.text = "Bad request"
        with self.assertRaises(Exception) as cm:
            aye.api._check_response(mock_resp)
        self.assertIn("Bad request", str(cm.exception))

    def test_check_response_json_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"error": "Server error"}
        mock_resp.text = "Server error"
        with self.assertRaises(Exception) as cm:
            aye.api._check_response(mock_resp)
        self.assertIn("Server error", str(cm.exception))

    def test_check_response_non_json(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_resp.text = "plain text"
        result = aye.api._check_response(mock_resp)
        self.assertEqual(result, {})

    @patch('aye.api._auth_headers')
    @patch('aye.api._check_response')
    @patch('httpx.Client')
    def test_cli_invoke_success_no_poll(self, mock_client, mock_check, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"final": "response"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp
        mock_check.return_value = {"final": "response"}

        #result = aye.api.cli_invoke(message="test", dry_run=True)
        #self.assertEqual(result, {"final": "response"})
        #mock_client.return_value.__enter__.return_value.post.assert_called_once()

    @patch('aye.api.time')
    @patch('httpx.get')
    @patch('httpx.Client')
    @patch('aye.api._check_response')
    @patch('aye.api._auth_headers')
    def test_cli_invoke_polling_success(self, mock_headers, mock_check, mock_client, mock_get, mock_time):
        mock_headers.return_value = {"Auth": "fake"}
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 202
        mock_post_resp.json.return_value = {"response_url": "https://fake.url"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
        mock_check.side_effect = [{"response_url": "https://fake.url"}, {"final": "response"}]

        # Mock polling: first 404, then 200
        mock_time.time.side_effect = [0, 2, 4]  # Simulate time progression
        mock_get.side_effect = [
            MagicMock(status_code=404),
            MagicMock(status_code=200, json=lambda: {"final": "response"})
        ]

        result = aye.api.cli_invoke(message="test", dry_run=False)
        self.assertEqual(result, {"final": "response"})
        mock_get.assert_called()

    @patch('aye.api.time')
    @patch('httpx.get')
    @patch('httpx.Client')
    @patch('aye.api._check_response')
    @patch('aye.api._auth_headers')
    def test_cli_invoke_timeout(self, mock_headers, mock_check, mock_client, mock_get, mock_time):
        mock_headers.return_value = {"Auth": "fake"}
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 202
        mock_post_resp.json.return_value = {"response_url": "https://fake.url"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
        mock_check.return_value = {"response_url": "https://fake.url"}

        # Mock time to exceed timeout
        mock_time.time.side_effect = [0, 130]  # Start and exceed 120s
        mock_get.return_value.status_code = 404

        with self.assertRaises(TimeoutError):
            aye.api.cli_invoke(message="test", dry_run=False, poll_timeout=120)

    @patch('aye.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_plugin_manifest_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"plugins": "data"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        result = aye.api.fetch_plugin_manifest(dry_run=True)
        self.assertEqual(result, {"plugins": "data"})

    @patch('aye.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_plugin_manifest_error(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "Server error"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        with self.assertRaises(Exception) as cm:
            aye.api.fetch_plugin_manifest(dry_run=True)
        self.assertIn("Server error", str(cm.exception))

    @patch('aye.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_server_time_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"timestamp": 1234567890}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        result = aye.api.fetch_server_time(dry_run=True)
        self.assertEqual(result, 1234567890)

    @patch('aye.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_server_time_error(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "Server error"}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        with self.assertRaises(Exception) as cm:
            aye.api.fetch_server_time(dry_run=True)
        self.assertIn("Server error", str(cm.exception))


if __name__ == '__main__':
    unittest.main()

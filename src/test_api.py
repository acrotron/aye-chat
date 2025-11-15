# Test suite for aye.model.api module
import os
import json
import time
from unittest import TestCase
from unittest.mock import patch, MagicMock
import httpx
import aye.model.api as api
from aye.model.auth import get_token


class TestModelApi(TestCase):
    def setUp(self):
        self.base_url = "https://api.ayechat.ai"
        self.token = "fake_token"
        os.environ["AYE_TOKEN"] = self.token  # Set env for testing
        # Disable debug prints for tests
        api.DEBUG = False

    def tearDown(self):
        if "AYE_TOKEN" in os.environ:
            del os.environ["AYE_TOKEN"]
        api.DEBUG = False

    @patch('aye.model.api.get_token')
    def test_auth_headers(self, mock_get_token):
        mock_get_token.return_value = self.token
        headers = api._auth_headers()
        self.assertEqual(headers, {"Authorization": f"Bearer {self.token}"}) 

    @patch('aye.model.api.get_token')
    def test_auth_headers_no_token(self, mock_get_token):
        mock_get_token.return_value = None
        with self.assertRaises(RuntimeError) as cm:
            api._auth_headers()
        self.assertIn("No auth token", str(cm.exception))

    def test_check_response_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": "ok"}
        mock_resp.text = "ok"
        mock_resp.raise_for_status.return_value = None
        result = api._check_response(mock_resp)
        self.assertEqual(result, {"data": "ok"})

    def test_check_response_error_status(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "Bad request"}
        mock_resp.text = "Bad request"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=None, response=mock_resp)
        with self.assertRaises(Exception) as cm:
            api._check_response(mock_resp)
        self.assertIn("Bad request", str(cm.exception))

    def test_check_response_error_status_non_json(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_resp.text = "Raw error text"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=None, response=mock_resp)
        with self.assertRaises(Exception) as cm:
            api._check_response(mock_resp)
        self.assertIn("Raw error text", str(cm.exception))

    def test_check_response_json_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"error": "Server error"}
        mock_resp.text = "Server error"
        with self.assertRaises(Exception) as cm:
            api._check_response(mock_resp)
        self.assertIn("Server error", str(cm.exception))

    def test_check_response_non_json(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_resp.text = "plain text"
        result = api._check_response(mock_resp)
        self.assertEqual(result, {})

    @patch('aye.model.api.time')
    @patch('httpx.get')
    @patch('httpx.Client')
    @patch('aye.model.api._check_response')
    @patch('aye.model.api._auth_headers')
    def test_cli_invoke_polling_success(self, mock_headers, mock_check, mock_client, mock_get, mock_time):
        mock_headers.return_value = {"Auth": "fake"}
        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {"response_url": "https://fake.url"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
        mock_check.return_value = {"response_url": "https://fake.url"}

        # Mock polling: first 404, then 200 with final data
        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [0, 2, 4]
        mock_get.side_effect = [
            MagicMock(status_code=404),
            MagicMock(status_code=200, json=lambda: {"final": "response"})
        ]

        result = api.cli_invoke(message="test", dry_run=False)
        self.assertEqual(result, {"final": "response"})
        self.assertEqual(mock_get.call_count, 2)
        mock_get.assert_called_with("https://fake.url", timeout=api.TIMEOUT)

    @patch('aye.model.api.time')
    @patch('httpx.get')
    @patch('httpx.Client')
    @patch('aye.model.api._check_response')
    @patch('aye.model.api._auth_headers')
    def test_cli_invoke_polling_json_decode_error(self, mock_headers, mock_check, mock_client, mock_get, mock_time):
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.time.side_effect = [0, 2]
        mock_get.return_value = MagicMock(status_code=200)
        mock_get.return_value.json.side_effect = json.JSONDecodeError("err", "doc", 0)

        with self.assertRaises(json.JSONDecodeError):
            api.cli_invoke(message="test")

    @patch('aye.model.api.time')
    @patch('httpx.get')
    @patch('httpx.Client')
    @patch('aye.model.api._check_response')
    @patch('aye.model.api._auth_headers')
    def test_cli_invoke_polling_request_error(self, mock_headers, mock_check, mock_client, mock_get, mock_time):
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.time.side_effect = [0, 2, 4]
        mock_get.side_effect = [
            httpx.RequestError("network error"),
            MagicMock(status_code=200, json=lambda: {"final": "response"})
        ]

        result = api.cli_invoke(message="test")
        self.assertEqual(result, {"final": "response"})
        self.assertEqual(mock_get.call_count, 2)

    @patch('aye.model.api.time')
    @patch('httpx.get')
    @patch('httpx.Client')
    @patch('aye.model.api._check_response')
    @patch('aye.model.api._auth_headers')
    def test_cli_invoke_timeout(self, mock_headers, mock_check, mock_client, mock_get, mock_time):
        mock_headers.return_value = {"Auth": "fake"}
        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {"response_url": "https://fake.url"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
        mock_check.return_value = {"response_url": "https://fake.url"}

        mock_time.sleep.return_value = None
        deadline = 120
        timestamps = list(range(0, deadline, 2)) + [deadline + 1]
        mock_time.time.side_effect = timestamps
        mock_get.return_value = MagicMock(status_code=404)

        with self.assertRaises(TimeoutError):
            api.cli_invoke(message="test", dry_run=False, poll_timeout=deadline)

    @patch('aye.model.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_plugin_manifest_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"plugins": "data"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        result = api.fetch_plugin_manifest(dry_run=True)
        self.assertEqual(result, {"plugins": "data"})

    @patch('aye.model.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_plugin_manifest_error(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "Server error"}
        mock_resp.text = '{"error": "Server error"}'
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=None, response=mock_resp)
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        with self.assertRaises(Exception) as cm:
            api.fetch_plugin_manifest(dry_run=True)
        self.assertIn("Server error", str(cm.exception))

    @patch('aye.model.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_server_time_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {"timestamp": 1234567890}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        result = api.fetch_server_time(dry_run=True)
        self.assertEqual(result, 1234567890)

    @patch('aye.model.api._auth_headers')
    @patch('httpx.Client')
    def test_fetch_server_time_error(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.ok = False
        mock_resp.json.return_value = {"error": "Server error"}
        mock_resp.text = '{"error": "Server error"}'
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=None, response=mock_resp)
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        with self.assertRaises(Exception) as cm:
            api.fetch_server_time(dry_run=True)
        self.assertIn("Server error", str(cm.exception))

    @patch('aye.model.api._auth_headers')
    @patch('httpx.Client')
    def test_send_feedback_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp

        api.send_feedback("great tool!", chat_id=123)

        mock_client.return_value.__enter__.return_value.post.assert_called_once()
        call_args = mock_client.return_value.__enter__.return_value.post.call_args
        #self.assertEqual(call_args.args[0], f"{self.base_url}/feedback")

        self.assertTrue('ayechat.ai/feedback' in call_args.args[0])
        self.assertEqual(call_args.kwargs['json'], {"feedback": "great tool!", "chat_id": 123})

    @patch('aye.model.api._auth_headers')
    @patch('httpx.Client')
    def test_send_feedback_error_ignored(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_client.return_value.__enter__.return_value.post.side_effect = httpx.RequestError("network error")

        # Should not raise an exception
        api.send_feedback("this will fail silently", chat_id=123)
        mock_client.return_value.__enter__.return_value.post.assert_called_once()

    @patch('builtins.print')
    def test_debug_mode_prints(self, mock_print):
        api.DEBUG = True

        # Test cli_invoke
        with patch('httpx.Client') as mock_client, \
             patch('httpx.get') as mock_get, \
             patch('aye.model.api._auth_headers'):
            mock_post_resp = MagicMock()
            mock_get_resp = MagicMock()

            mock_post_resp.status_code = 200
            mock_post_resp.json.return_value = {"response_url": "https://testurl"}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp

            mock_get_resp.status_code = 200
            mock_get_resp.json.return_value = { "answer_summary": "Test response", "source_files": []}
            mock_get.return_value = mock_get_resp
            api.cli_invoke(message="test")
            self.assertIn("[DEBUG] Sending request to", str(mock_print.call_args_list[0][0][0]))

        # Test fetch_plugin_manifest
        with patch('httpx.Client') as mock_client, patch('aye.model.api._auth_headers'):
            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 200
            mock_post_resp.json.return_value = {"plugins": "data"}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
            api.fetch_plugin_manifest()
            # Find the call after the first three (sending, payload, headers)
            debug_calls = [str(call[0][0]) for call in mock_print.call_args_list if "[DEBUG] Sending request to" in str(call[0][0])]
            self.assertIn("[DEBUG] Sending request to", debug_calls[-1])

        # Test fetch_server_time
        with patch('httpx.Client') as mock_client, patch('aye.model.api._auth_headers'):
            mock_get_resp = MagicMock()
            mock_get_resp.status_code = 200
            mock_get_resp.ok = True
            mock_get_resp.json.return_value = {"timestamp": 123}
            mock_client.return_value.__enter__.return_value.get.return_value = mock_get_resp
            api.fetch_server_time()
            debug_calls = [str(call[0][0]) for call in mock_print.call_args_list if "[DEBUG] Sending request to" in str(call[0][0])]
            self.assertIn("[DEBUG] Sending request to", debug_calls[-1])

        # Test send_feedback
        with patch('httpx.Client') as mock_client, patch('aye.model.api._auth_headers'):
            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 200
            mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
            api.send_feedback("feedback")
            debug_calls = [str(call[0][0]) for call in mock_print.call_args_list if "[DEBUG] Sending request to" in str(call[0][0])]
            self.assertIn("[DEBUG] Sending request to", debug_calls[-1])

        # Test send_feedback error in debug
        with patch('httpx.Client') as mock_client, patch('aye.model.api._auth_headers'):
            mock_client.return_value.__enter__.return_value.post.side_effect = Exception("send error")
            api.send_feedback("feedback")
            self.assertIn("[DEBUG] Error sending feedback: send error", str(mock_print.call_args_list[-1][0][0]))

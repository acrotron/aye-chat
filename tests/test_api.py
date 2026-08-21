# Test suite for aye.model.api module
import io
import os
import json
from unittest import TestCase
from unittest.mock import patch, MagicMock

import httpx

import aye.model.api as api
from aye.model.api import ApiError, InvalidDemoTokenError


class TestModelApi(TestCase):
    def setUp(self):
        self.base_url = "https://api.ayechat.ai"
        self.token = "fake_token"
        os.environ["AYE_TOKEN"] = self.token  # Set env for testing

    def tearDown(self):
        if "AYE_TOKEN" in os.environ:
            del os.environ["AYE_TOKEN"]
        os.environ.pop("AYE_STREAM_DEBUG", None)

    @patch("aye.model.api.get_token")
    def test_auth_headers(self, mock_get_token):
        mock_get_token.return_value = self.token
        headers = api._auth_headers()
        self.assertEqual(headers, {"Authorization": f"Bearer {self.token}"})

    @patch("aye.model.api.get_token")
    def test_auth_headers_no_token(self, mock_get_token):
        mock_get_token.return_value = None
        with self.assertRaises(RuntimeError) as cm:
            api._auth_headers()
        self.assertIn("No auth token", str(cm.exception))

    def test_is_stream_debug_env_parsing(self):
        os.environ.pop("AYE_STREAM_DEBUG", None)
        self.assertFalse(api._is_stream_debug())

        for v in ("1", "true", "on", "TRUE", "On"):
            os.environ["AYE_STREAM_DEBUG"] = v
            self.assertTrue(api._is_stream_debug())

        os.environ["AYE_STREAM_DEBUG"] = "0"
        self.assertFalse(api._is_stream_debug())

    @patch("aye.model.api.get_user_config")
    def test_ssl_verify_parsing(self, mock_get_user_config):
        for value in ("0", "false", "off", "no", " OFF "):
            mock_get_user_config.return_value = value
            self.assertFalse(api._ssl_verify())

        for value in ("1", "true", "on", "yes", " YES "):
            mock_get_user_config.return_value = value
            self.assertTrue(api._ssl_verify())

        mock_get_user_config.return_value = "unexpected"
        self.assertTrue(api._ssl_verify())

    @patch("aye.model.api.get_user_config", return_value="on")
    def test_is_debug_true(self, mock_get_user_config):
        self.assertTrue(api._is_debug())
        mock_get_user_config.assert_called_once_with("debug", "off")

    @patch("aye.model.api.get_user_config", return_value="off")
    def test_is_debug_false(self, mock_get_user_config):
        self.assertFalse(api._is_debug())
        mock_get_user_config.assert_called_once_with("debug", "off")

    def test_redact_payload_for_debug_redacts_attachment_data_b64(self):
        payload = {
            "message": "hello",
            "attachments": [
                {
                    "file_name": "image.png",
                    "mime_type": "image/png",
                    "data_b64": "abcdef",
                    "bytes_size": 3,
                }
            ],
        }

        redacted = api._redact_payload_for_debug(payload)

        self.assertEqual(redacted["message"], "hello")
        self.assertEqual(redacted["attachments"][0]["data_b64"], "<redacted: 6 chars>")
        self.assertEqual(payload["attachments"][0]["data_b64"], "abcdef")

    def test_redact_payload_for_debug_handles_non_string_and_non_dict_attachments(self):
        payload = {
            "attachments": [
                {"file_name": "image.png", "data_b64": b"abc"},
                "not-a-dict",
            ]
        }

        redacted = api._redact_payload_for_debug(payload)

        self.assertEqual(redacted["attachments"][0]["data_b64"], "<redacted: 0 chars>")
        self.assertEqual(redacted["attachments"][1], "not-a-dict")

    def test_redact_payload_for_debug_returns_original_when_no_attachments(self):
        payload = {"message": "hello"}
        self.assertIs(api._redact_payload_for_debug(payload), payload)

    def test_redact_payload_for_debug_returns_non_dict_unchanged(self):
        payload = ["not", "a", "dict"]
        self.assertIs(api._redact_payload_for_debug(payload), payload)

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
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )
        with self.assertRaises(ApiError) as cm:
            api._check_response(mock_resp)
        self.assertIn("Bad request", str(cm.exception))
        self.assertEqual(cm.exception.status_code, 400)

    def test_check_response_error_status_json_without_error_uses_text(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"message": "no error field"}
        mock_resp.text = "Fallback text"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )

        with self.assertRaises(ApiError) as cm:
            api._check_response(mock_resp)
        self.assertIn("Fallback text", str(cm.exception))
        self.assertEqual(cm.exception.status_code, 400)

    def test_check_response_error_status_non_json(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_resp.text = "Raw error text"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )
        with self.assertRaises(ApiError) as cm:
            api._check_response(mock_resp)
        self.assertIn("Raw error text", str(cm.exception))
        self.assertEqual(cm.exception.status_code, 400)

    def test_check_response_json_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"error": "Server error"}
        mock_resp.text = "Server error"
        with self.assertRaises(ApiError) as cm:
            api._check_response(mock_resp)
        self.assertIn("Server error", str(cm.exception))
        self.assertIsNone(cm.exception.status_code)

    def test_check_response_error_status_429_rate_limit(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.json.return_value = {"error": "Rate limit exceeded"}
        mock_resp.text = "Rate limit exceeded"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )
        with self.assertRaises(ApiError) as cm:
            api._check_response(mock_resp)
        self.assertEqual(cm.exception.status_code, 429)

    def test_check_response_error_status_500_server(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "Internal server error"}
        mock_resp.text = "Internal server error"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )
        with self.assertRaises(ApiError) as cm:
            api._check_response(mock_resp)
        self.assertEqual(cm.exception.status_code, 500)

    def test_check_response_non_json(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = json.JSONDecodeError("", "", 0)
        mock_resp.text = "plain text"
        result = api._check_response(mock_resp)
        self.assertEqual(result, {})

    def test_check_response_error_status_invalid_demo_token_by_code(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {
            "error": "expired demo token",
            "error_code": "INVALID_DEMO_TOKEN",
        }
        mock_resp.text = "expired demo token"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )

        with self.assertRaises(InvalidDemoTokenError) as cm:
            api._check_response(mock_resp)

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(cm.exception.error_code, "INVALID_DEMO_TOKEN")

    def test_check_response_error_status_invalid_demo_token_by_message(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": "INVALID_DEMO_TOKEN: expired"}
        mock_resp.text = "INVALID_DEMO_TOKEN: expired"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )

        with self.assertRaises(InvalidDemoTokenError) as cm:
            api._check_response(mock_resp)

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(cm.exception.error_code, "INVALID_DEMO_TOKEN")

    def test_check_response_success_payload_invalid_demo_token_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "error": "expired demo token",
            "code": "INVALID_DEMO_TOKEN",
        }

        with self.assertRaises(InvalidDemoTokenError) as cm:
            api._check_response(mock_resp)

        self.assertIsNone(cm.exception.status_code)
        self.assertEqual(cm.exception.error_code, "INVALID_DEMO_TOKEN")

    def test_check_response_preserves_error_code_on_regular_api_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "bad", "code": "BAD_INPUT"}
        mock_resp.text = "bad"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )

        with self.assertRaises(ApiError) as cm:
            api._check_response(mock_resp)

        self.assertEqual(cm.exception.error_code, "BAD_INPUT")

    def test_extract_answer_summary_missing(self):
        self.assertEqual(api._extract_answer_summary_from_assistant_response({}), "")

    def test_extract_answer_summary_from_dict(self):
        payload = {"assistant_response": {"answer_summary": "Hi"}}
        self.assertEqual(api._extract_answer_summary_from_assistant_response(payload), "Hi")

    def test_extract_answer_summary_from_list(self):
        payload = {"assistant_response": [{"answer_summary": "Hi"}]}
        self.assertEqual(api._extract_answer_summary_from_assistant_response(payload), "")

    def test_extract_answer_summary_from_json_string(self):
        payload = {"assistant_response": json.dumps({"answer_summary": "Hello"})}
        self.assertEqual(api._extract_answer_summary_from_assistant_response(payload), "Hello")

    def test_extract_answer_summary_invalid_json_string(self):
        payload = {"assistant_response": "{not-json"}
        self.assertEqual(api._extract_answer_summary_from_assistant_response(payload), "")

    def test_extract_answer_summary_from_json_non_dict(self):
        payload = {"assistant_response": json.dumps(["not", "a", "dict"])}
        self.assertEqual(api._extract_answer_summary_from_assistant_response(payload), "")

    def test_call_stream_update_none_callback_is_noop(self):
        api._call_stream_update(None, "content", is_final=True)

    def test_call_stream_update_prefers_keyword_is_final(self):
        calls = []

        def callback(content, *, is_final):
            calls.append((content, is_final))

        api._call_stream_update(callback, "content", is_final=True)
        self.assertEqual(calls, [("content", True)])

    def test_call_stream_update_falls_back_to_positional_is_final(self):
        calls = []

        def callback(content, final):
            calls.append((content, final))

        api._call_stream_update(callback, "content", is_final=False)
        self.assertEqual(calls, [("content", False)])

    def test_call_stream_update_falls_back_to_legacy_single_arg(self):
        calls = []

        def callback(content):
            calls.append(content)

        api._call_stream_update(callback, "content", is_final=True)
        self.assertEqual(calls, ["content"])

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_polling_success(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
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
            MagicMock(status_code=200, json=lambda: {"final": "response"}),
        ]

        result = api.cli_invoke(message="test", dry_run=False)
        self.assertEqual(result, {"final": "response"})
        self.assertEqual(mock_get.call_count, 2)
        mock_get.assert_called_with(
            "https://fake.url", timeout=api.POLL_REQUEST_TIMEOUT, verify=True
        )

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_polling_json_decode_error(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        """If the presigned URL returns 200 but the body isn't valid JSON,
        cli_invoke() retries until poll_timeout and then raises TimeoutError.

        The streaming refactor changed behavior from raising JSONDecodeError
        immediately to retrying.
        """
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}

        mock_time.sleep.return_value = None
        # deadline = time.time() + poll_timeout uses first value
        # the while loop condition consumes subsequent values
        mock_time.time.side_effect = [0, 0.1, 0.2, 0.3, 1.1]

        mock_get.return_value = MagicMock(status_code=200, text="not-json")
        mock_get.return_value.json.side_effect = json.JSONDecodeError("err", "doc", 0)

        with self.assertRaises(TimeoutError):
            api.cli_invoke(message="test", poll_timeout=1.0)

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_polling_request_error(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.time.side_effect = [0, 2, 4]
        mock_get.side_effect = [
            httpx.RequestError("network error"),
            MagicMock(status_code=200, json=lambda: {"final": "response"}),
        ]

        result = api.cli_invoke(message="test")
        self.assertEqual(result, {"final": "response"})
        self.assertEqual(mock_get.call_count, 2)

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_timeout(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
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

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_poll_uses_short_per_request_timeout(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        """Each poll must cap at POLL_REQUEST_TIMEOUT, not the loop deadline.

        Reusing TIMEOUT (900s) here meant a single hung GET consumed the whole
        poll budget, so the request failed outright instead of retrying.
        """
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.time.side_effect = [0, 2]
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"final": "response"}
        )

        api.cli_invoke(message="test")

        self.assertEqual(
            mock_get.call_args.kwargs["timeout"], api.POLL_REQUEST_TIMEOUT
        )
        self.assertLess(api.POLL_REQUEST_TIMEOUT, api.TIMEOUT)

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_poll_retries_after_a_read_timeout(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        """A timed-out poll is retried rather than failing the request.

        httpx.ReadTimeout is a RequestError subclass, so the existing handler
        catches it; this pins the retry so a hung poll cannot end the request.
        """
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [0, 2, 4]
        mock_get.side_effect = [
            httpx.ReadTimeout("poll hung"),
            MagicMock(status_code=200, json=lambda: {"final": "response"}),
        ]

        result = api.cli_invoke(message="test")

        self.assertEqual(result, {"final": "response"})
        self.assertEqual(mock_get.call_count, 2)

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_streaming_calls_callback_and_sets_streamed_summary(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        """Exercise streaming polling path:

        - receives partial_content updates (streaming=True)
        - calls on_stream_update with new partials
        - when final result arrives, extracts answer_summary and sends one last update
        - sets result['_streamed_summary'] = True
        """
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}

        # Deterministic time: compute deadline then allow 3 loop iterations.
        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [
            0.0,  # for deadline
            0.1,  # loop 1
            0.2,  # loop 2
            0.3,  # loop 3
        ]

        stream_1 = MagicMock(
            status_code=200,
            text='{"streaming":true,"partial_content":"Hel"}',
        )
        stream_1.json.return_value = {"streaming": True, "partial_content": "Hel"}

        stream_2 = MagicMock(
            status_code=200,
            text='{"streaming":true,"partial_content":"Hello"}',
        )
        stream_2.json.return_value = {"streaming": True, "partial_content": "Hello"}

        final = MagicMock(
            status_code=200,
            text='{"assistant_response":"..."}',
        )
        final.json.return_value = {
            "assistant_response": json.dumps({"answer_summary": "Hello final"})
        }

        mock_get.side_effect = [stream_1, stream_2, final]

        updates = []

        def on_update(s):
            updates.append(s)

        result = api.cli_invoke(message="test", poll_timeout=10, on_stream_update=on_update)

        self.assertEqual(updates, ["Hel", "Hello", "Hello final"])
        self.assertTrue(result.get("_streamed_summary"))

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_streaming_dedupes_identical_partials(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}

        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [0.0, 0.1, 0.2, 0.3]

        stream_1 = MagicMock(status_code=200, text="")
        stream_1.json.return_value = {"streaming": True, "partial_content": "Same"}

        stream_2 = MagicMock(status_code=200, text="")
        stream_2.json.return_value = {"streaming": True, "partial_content": "Same"}

        final = MagicMock(status_code=200, text="")
        final.json.return_value = {"assistant_response": json.dumps({"answer_summary": "Same"})}

        mock_get.side_effect = [stream_1, stream_2, final]

        updates = []
        result = api.cli_invoke(
            message="test",
            poll_timeout=10,
            on_stream_update=lambda s: updates.append(s),
        )

        # Identical partials are deduped, but finalization always triggers a final update.
        self.assertEqual(updates, ["Same", "Same"])
        self.assertTrue(result.get("_streamed_summary"))

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_streaming_final_uses_streamed_content_when_summary_missing(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [0.0, 0.1, 0.2]

        stream = MagicMock(status_code=200, text="")
        stream.json.return_value = {"streaming": True, "partial_content": "Partial only"}

        final = MagicMock(status_code=200, text="")
        final.json.return_value = {"assistant_response": "{}"}
        mock_get.side_effect = [stream, final]

        updates = []
        result = api.cli_invoke(
            message="test",
            poll_timeout=10,
            on_stream_update=lambda s, is_final=False: updates.append((s, is_final)),
        )

        self.assertEqual(updates, [("Partial only", False), ("Partial only", True)])
        self.assertTrue(result.get("_streamed_summary"))

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_streaming_ignores_empty_partial(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [0.0, 0.1, 0.2]

        stream = MagicMock(status_code=200, text="")
        stream.json.return_value = {"streaming": True, "partial_content": ""}

        final = MagicMock(status_code=200, text="")
        final.json.return_value = {"final": "response"}
        mock_get.side_effect = [stream, final]

        updates = []
        result = api.cli_invoke(
            message="test",
            poll_timeout=10,
            on_stream_update=lambda s: updates.append(s),
        )

        self.assertEqual(result, {"final": "response"})
        self.assertEqual(updates, [])

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_stream_debug_writes_to_stderr(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        os.environ["AYE_STREAM_DEBUG"] = "1"

        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}

        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [0.0, 0.1, 0.2]

        stream = MagicMock(status_code=200, text="")
        stream.json.return_value = {"streaming": True, "partial_content": "Hello"}

        final = MagicMock(status_code=200, text="")
        final.json.return_value = {"assistant_response": json.dumps({"answer_summary": "Hello"})}

        mock_get.side_effect = [stream, final]

        stderr = io.StringIO()
        with patch("aye.model.api.sys.stderr", stderr):
            api.cli_invoke(message="test", poll_timeout=10)

        self.assertIn("[STREAM_DEBUG]", stderr.getvalue())

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_unexpected_status_raises_for_status(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        mock_headers.return_value = {"Auth": "fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.sleep.return_value = None
        mock_time.time.side_effect = [0.0, 0.1]

        r = MagicMock(status_code=500)
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=None, response=r
        )
        mock_get.return_value = r

        with self.assertRaises(httpx.HTTPStatusError):
            api.cli_invoke(message="test", poll_timeout=10)

    @patch("aye.model.api._ssl_verify", return_value=False)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_builds_full_payload_with_optional_fields(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.time.side_effect = [0.0, 0.1]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

        attachments = [
            {
                "file_name": "clip.png",
                "mime_type": "image/png",
                "data_b64": "abc",
                "bytes_size": 2,
            }
        ]
        telemetry = {"event_count": 1}

        result = api.cli_invoke(
            chat_id=123,
            message="prompt",
            source_files={"main.py": "print('hi')"},
            model="gpt-test",
            system_prompt="system",
            max_output_tokens=42,
            dry_run=True,
            telemetry=telemetry,
            attachments=attachments,
        )

        self.assertEqual(result, {"ok": True})
        mock_client.assert_called_with(timeout=api.TIMEOUT, verify=False)

        post_kwargs = mock_client.return_value.__enter__.return_value.post.call_args.kwargs
        payload = post_kwargs["json"]
        self.assertEqual(payload["chat_id"], 123)
        self.assertEqual(payload["message"], "prompt")
        self.assertEqual(payload["source_files"], {"main.py": "print('hi')"})
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["system_prompt"], "system")
        self.assertEqual(payload["max_output_tokens"], 42)
        self.assertEqual(payload["dry_run"], True)
        self.assertEqual(payload["streaming"], True)
        self.assertEqual(payload["telemetry"], telemetry)
        self.assertEqual(payload["attachments"], attachments)

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_omits_optional_fields_when_not_supplied(
        self, mock_headers, mock_check, mock_client, mock_get, mock_time, mock_ssl_verify
    ):
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.time.side_effect = [0.0, 0.1]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

        api.cli_invoke(
            message="prompt",
            model=None,
            system_prompt=None,
            max_output_tokens=None,
            telemetry=None,
            attachments=[],
        )

        payload = mock_client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertNotIn("model", payload)
        self.assertNotIn("system_prompt", payload)
        self.assertNotIn("max_output_tokens", payload)
        self.assertNotIn("telemetry", payload)
        self.assertNotIn("attachments", payload)

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    @patch("aye.model.api.refresh_demo_token")
    def test_cli_invoke_refreshes_invalid_demo_token_and_retries_once(
        self,
        mock_refresh_demo_token,
        mock_headers,
        mock_check,
        mock_client,
        mock_get,
        mock_time,
        mock_ssl_verify,
    ):
        mock_refresh_demo_token.return_value = "new-demo-token"
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_check.side_effect = [
            InvalidDemoTokenError(
                "invalid", status_code=401, error_code="INVALID_DEMO_TOKEN"
            ),
            {"response_url": "https://fake.url"},
        ]
        mock_time.time.side_effect = [0.0, 0.1]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

        result = api.cli_invoke(message="test")

        self.assertEqual(result, {"ok": True})
        mock_refresh_demo_token.assert_called_once_with()
        self.assertEqual(mock_check.call_count, 2)
        self.assertEqual(mock_client.return_value.__enter__.return_value.post.call_count, 2)

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    @patch("aye.model.api.refresh_demo_token")
    def test_cli_invoke_invalid_demo_token_refresh_failure_raises_actionable_api_error(
        self,
        mock_refresh_demo_token,
        mock_headers,
        mock_check,
        mock_client,
        mock_ssl_verify,
    ):
        mock_refresh_demo_token.return_value = None
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_check.side_effect = InvalidDemoTokenError(
            "invalid", status_code=401, error_code="INVALID_DEMO_TOKEN"
        )

        with self.assertRaises(ApiError) as cm:
            api.cli_invoke(message="test")

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(cm.exception.error_code, "INVALID_DEMO_TOKEN")
        self.assertIn("demo session has expired", str(cm.exception))

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    @patch("aye.model.api.refresh_demo_token")
    def test_cli_invoke_invalid_demo_token_no_retry_raises_actionable_api_error(
        self,
        mock_refresh_demo_token,
        mock_headers,
        mock_check,
        mock_client,
        mock_ssl_verify,
    ):
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_check.side_effect = InvalidDemoTokenError(
            "invalid", status_code=401, error_code="INVALID_DEMO_TOKEN"
        )

        with self.assertRaises(ApiError) as cm:
            api.cli_invoke(message="test", _retry_on_invalid_demo=False)

        mock_refresh_demo_token.assert_not_called()
        self.assertEqual(cm.exception.error_code, "INVALID_DEMO_TOKEN")

    @patch("builtins.print")
    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api.get_user_config", return_value="on")
    @patch("aye.model.api.time")
    @patch("httpx.get")
    @patch("httpx.Client")
    @patch("aye.model.api._check_response")
    @patch("aye.model.api._auth_headers")
    def test_cli_invoke_debug_redacts_attachment_data(
        self,
        mock_headers,
        mock_check,
        mock_client,
        mock_get,
        mock_time,
        mock_get_config,
        mock_ssl_verify,
        mock_print,
    ):
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_check.return_value = {"response_url": "https://fake.url"}
        mock_time.time.side_effect = [0.0, 0.1]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"ok": True}, text="{}")

        api.cli_invoke(
            message="test",
            attachments=[
                {
                    "file_name": "clip.png",
                    "mime_type": "image/png",
                    "data_b64": "abcdef",
                    "bytes_size": 3,
                }
            ],
        )

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("<redacted: 6 chars>", printed)
        self.assertNotIn('"data_b64": "abcdef"', printed)

    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_fetch_plugin_manifest_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"plugins": "data"}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        result = api.fetch_plugin_manifest(dry_run=True)
        self.assertEqual(result, {"plugins": "data"})

    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_fetch_plugin_manifest_error(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "Server error"}
        mock_resp.text = '{"error": "Server error"}'
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        with self.assertRaises(Exception) as cm:
            api.fetch_plugin_manifest(dry_run=True)
        self.assertIn("Server error", str(cm.exception))

    @patch("aye.model.api._ssl_verify", return_value=False)
    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_fetch_plugin_manifest_includes_previous_token_when_different(
        self, mock_client, mock_headers, mock_ssl_verify
    ):
        mock_headers.return_value = {"Authorization": "Bearer new"}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"plugins": []}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        result = api.fetch_plugin_manifest(
            dry_run=False,
            token_override="new-token",
            previous_token="old-token",
        )

        self.assertEqual(result, {"plugins": []})
        mock_client.assert_called_with(timeout=api.TIMEOUT, verify=False)
        mock_headers.assert_called_once_with(token_override="new-token")
        post_kwargs = mock_client.return_value.__enter__.return_value.post.call_args.kwargs
        self.assertEqual(
            post_kwargs["json"],
            {"dry_run": False, "previous_token": "old-token"},
        )

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_fetch_plugin_manifest_omits_previous_token_when_same_as_override(
        self, mock_client, mock_headers, mock_ssl_verify
    ):
        mock_headers.return_value = {"Authorization": "Bearer same"}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"plugins": []}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        api.fetch_plugin_manifest(
            dry_run=True,
            token_override="same-token",
            previous_token="same-token",
        )

        post_kwargs = mock_client.return_value.__enter__.return_value.post.call_args.kwargs
        self.assertEqual(post_kwargs["json"], {"dry_run": True})

    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_fetch_server_time_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {"timestamp": 1234567890}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        result = api.fetch_server_time(dry_run=True)
        self.assertEqual(result, 1234567890)

    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_fetch_server_time_error(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.ok = False
        mock_resp.json.return_value = {"error": "Server error"}
        mock_resp.text = '{"error": "Server error"}'
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=None, response=mock_resp
        )
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        with self.assertRaises(Exception) as cm:
            api.fetch_server_time(dry_run=True)
        self.assertIn("Server error", str(cm.exception))

    @patch("aye.model.api._ssl_verify", return_value=False)
    @patch("httpx.Client")
    def test_fetch_server_time_uses_params_and_ssl_verify(self, mock_client, mock_ssl_verify):
        mock_resp = MagicMock(status_code=200, ok=True)
        mock_resp.json.return_value = {"timestamp": 456}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        result = api.fetch_server_time(dry_run=False)

        self.assertEqual(result, 456)
        mock_client.assert_called_with(timeout=api.TIMEOUT, verify=False)
        get_call = mock_client.return_value.__enter__.return_value.get.call_args
        self.assertEqual(get_call.args[0], f"{api.BASE_URL}/time")
        self.assertEqual(get_call.kwargs["params"], {"dry_run": False})

    @patch("aye.model.api._ssl_verify", return_value=True)
    @patch("aye.model.api._check_response", return_value={})
    @patch("httpx.Client")
    def test_fetch_server_time_not_ok_returns_none_if_check_response_does_not_raise(
        self, mock_client, mock_check_response, mock_ssl_verify
    ):
        mock_resp = MagicMock(status_code=500, ok=False)
        mock_client.return_value.__enter__.return_value.get.return_value = mock_resp

        result = api.fetch_server_time(dry_run=True)

        self.assertIsNone(result)
        mock_check_response.assert_called_once_with(mock_resp)

    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_send_feedback_success(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp

        api.send_feedback("great tool!", chat_id=123)

        mock_client.return_value.__enter__.return_value.post.assert_called_once()
        call_args = mock_client.return_value.__enter__.return_value.post.call_args

        self.assertTrue("/feedback" in call_args.args[0])
        self.assertEqual(call_args.kwargs["json"], {"feedback": "great tool!", "chat_id": 123})

    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_send_feedback_includes_telemetry(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_post_resp = MagicMock(status_code=200)
        mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp

        api.send_feedback("ok", chat_id=1, telemetry={"k": "v"})

        call_args = mock_client.return_value.__enter__.return_value.post.call_args
        self.assertEqual(
            call_args.kwargs["json"],
            {"feedback": "ok", "chat_id": 1, "telemetry": {"k": "v"}},
        )

    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_send_feedback_error_ignored(self, mock_client, mock_headers):
        mock_headers.return_value = {"Auth": "fake"}
        mock_client.return_value.__enter__.return_value.post.side_effect = httpx.RequestError(
            "network error"
        )

        # Should not raise an exception
        api.send_feedback("this will fail silently", chat_id=123)
        mock_client.return_value.__enter__.return_value.post.assert_called_once()

    @patch("aye.model.api._ssl_verify", return_value=False)
    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_send_feedback_uses_short_timeout_and_ssl_verify(
        self, mock_client, mock_headers, mock_ssl_verify
    ):
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_client.return_value.__enter__.return_value.post.return_value = MagicMock(status_code=200)

        api.send_feedback("feedback")

        mock_client.assert_called_with(timeout=10.0, verify=False)

    @patch("aye.model.api._auth_headers", side_effect=RuntimeError("missing token"))
    @patch("httpx.Client")
    def test_send_feedback_auth_error_ignored(self, mock_client, mock_headers):
        api.send_feedback("feedback")
        mock_client.return_value.__enter__.return_value.post.assert_not_called()

    @patch("builtins.print")
    @patch("aye.model.api.get_user_config", return_value="on")
    def test_debug_mode_prints(self, mock_get_config, mock_print):
        # Test cli_invoke
        with patch("httpx.Client") as mock_client, patch("httpx.get") as mock_get, patch(
            "aye.model.api._auth_headers"
        ):
            mock_post_resp = MagicMock()
            mock_get_resp = MagicMock()

            mock_post_resp.status_code = 200
            mock_post_resp.json.return_value = {"response_url": "https://testurl"}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp

            mock_get_resp.status_code = 200
            mock_get_resp.json.return_value = {"answer_summary": "Test response", "source_files": []}
            mock_get_resp.text = json.dumps({"answer_summary": "Test response", "source_files": []})
            mock_get.return_value = mock_get_resp
            api.cli_invoke(message="test")
            self.assertIn("[DEBUG] Sending request to", str(mock_print.call_args_list[0][0][0]))

        # Test fetch_plugin_manifest
        with patch("httpx.Client") as mock_client, patch("aye.model.api._auth_headers"):
            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 200
            mock_post_resp.json.return_value = {"plugins": "data"}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
            api.fetch_plugin_manifest()
            debug_calls = [
                str(call[0][0])
                for call in mock_print.call_args_list
                if "[DEBUG] Sending request to" in str(call[0][0])
            ]
            self.assertIn("[DEBUG] Sending request to", debug_calls[-1])

        # Test fetch_server_time
        with patch("httpx.Client") as mock_client, patch("aye.model.api._auth_headers"):
            mock_get_resp = MagicMock()
            mock_get_resp.status_code = 200
            mock_get_resp.ok = True
            mock_get_resp.json.return_value = {"timestamp": 123}
            mock_client.return_value.__enter__.return_value.get.return_value = mock_get_resp
            api.fetch_server_time()
            debug_calls = [
                str(call[0][0])
                for call in mock_print.call_args_list
                if "[DEBUG] Sending request to" in str(call[0][0])
            ]
            self.assertIn("[DEBUG] Sending request to", debug_calls[-1])

        # Test send_feedback
        with patch("httpx.Client") as mock_client, patch("aye.model.api._auth_headers"):
            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 200
            mock_client.return_value.__enter__.return_value.post.return_value = mock_post_resp
            api.send_feedback("feedback")
            debug_calls = [
                str(call[0][0])
                for call in mock_print.call_args_list
                if "[DEBUG] Sending request to" in str(call[0][0])
            ]
            self.assertIn("[DEBUG] Sending request to", debug_calls[-1])

        # Test send_feedback error in debug
        with patch("httpx.Client") as mock_client, patch("aye.model.api._auth_headers"):
            mock_client.return_value.__enter__.return_value.post.side_effect = Exception(
                "send error"
            )
            api.send_feedback("feedback")
            self.assertIn(
                "[DEBUG] Error sending feedback: send error",
                str(mock_print.call_args_list[-1][0][0]),
            )

    @patch("builtins.print")
    @patch("aye.model.api.get_user_config", return_value="on")
    @patch("aye.model.api._auth_headers")
    @patch("httpx.Client")
    def test_fetch_plugin_manifest_debug_prints_previous_token_included_false(
        self, mock_client, mock_headers, mock_get_config, mock_print
    ):
        mock_headers.return_value = {"Authorization": "Bearer fake"}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"plugins": []}
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp

        api.fetch_plugin_manifest(token_override="same", previous_token="same")

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("[DEBUG] previous_token included: False", printed)

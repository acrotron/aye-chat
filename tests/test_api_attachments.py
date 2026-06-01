"""Tests for Phase 4: attachments plumbing through `cli_invoke` and `invoke_llm`.

Verifies:
- `cli_invoke` payload is unchanged when `attachments` is None or empty.
- `cli_invoke` includes `attachments` only when non-empty, with exact field
  names ``file_name``, ``mime_type``, ``data_b64``, ``bytes_size``.
- Image bytes (``data_b64``) never appear verbatim in debug output.
- `invoke_llm` forwards `attachments` to both local plugins and `cli_invoke`.
"""

import io
import sys
from unittest.mock import MagicMock, patch

import pytest

from aye.model import api as api_module
from aye.model.api import _redact_payload_for_debug, cli_invoke


LONG_B64 = "QUJDREVGRw==" * 200  # ~ 2.4KB of base64 \u2014 distinctive substring


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )


class _FakeClient:
    """Captures the POST payload sent to /invoke_cli."""

    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, headers=None):
        _FakeClient.captured = {
            "url": url,
            "json": json,
            "headers": headers,
        }
        return _FakeResponse(
            status_code=200,
            json_data={"response_url": "https://example/poll"},
        )


def _fake_get_final(*args, **kwargs):
    """Polling GET that immediately returns a final (non-streaming) result."""
    return _FakeResponse(
        status_code=200,
        json_data={
            "assistant_response": '{"answer_summary": "ok", "source_files": []}',
            "chat_id": 1,
        },
        text='{"streaming": false}',
    )


@pytest.fixture
def patched_api(monkeypatch):
    """Patch httpx + auth so `cli_invoke` can run end-to-end without network."""
    monkeypatch.setattr(api_module.httpx, "Client", _FakeClient)
    monkeypatch.setattr(api_module.httpx, "get", _fake_get_final)
    monkeypatch.setattr(api_module, "_auth_headers", lambda **kw: {"Authorization": "Bearer X"})
    monkeypatch.setattr(api_module, "_ssl_verify", lambda: True)
    monkeypatch.setattr(api_module, "_is_debug", lambda: False)
    _FakeClient.captured = {}
    yield


# ---------------------------------------------------------------------------
# _redact_payload_for_debug
# ---------------------------------------------------------------------------

class TestRedactPayloadForDebug:
    def test_no_attachments_returns_payload_unchanged(self):
        payload = {"message": "hi", "source_files": {}}
        result = _redact_payload_for_debug(payload)
        assert result == payload

    def test_empty_attachments_list_returns_unchanged(self):
        payload = {"message": "hi", "attachments": []}
        result = _redact_payload_for_debug(payload)
        assert result == payload

    def test_data_b64_is_replaced(self):
        payload = {
            "attachments": [
                {
                    "file_name": "a.png",
                    "mime_type": "image/png",
                    "data_b64": LONG_B64,
                    "bytes_size": 123,
                }
            ]
        }
        result = _redact_payload_for_debug(payload)
        assert LONG_B64 not in str(result)
        assert "redacted" in result["attachments"][0]["data_b64"]
        # Other fields preserved
        assert result["attachments"][0]["file_name"] == "a.png"
        assert result["attachments"][0]["mime_type"] == "image/png"
        assert result["attachments"][0]["bytes_size"] == 123

    def test_original_payload_not_mutated(self):
        payload = {
            "attachments": [
                {
                    "file_name": "a.png",
                    "mime_type": "image/png",
                    "data_b64": LONG_B64,
                    "bytes_size": 123,
                }
            ]
        }
        _redact_payload_for_debug(payload)
        assert payload["attachments"][0]["data_b64"] == LONG_B64

    def test_non_dict_input_returned_as_is(self):
        assert _redact_payload_for_debug("not a dict") == "not a dict"


# ---------------------------------------------------------------------------
# cli_invoke payload shape
# ---------------------------------------------------------------------------

class TestCliInvokePayload:
    def test_no_attachments_arg_excludes_field(self, patched_api):
        cli_invoke(message="hi", source_files={}, model="m")
        body = _FakeClient.captured["json"]
        assert "attachments" not in body

    def test_attachments_none_excludes_field(self, patched_api):
        cli_invoke(message="hi", source_files={}, model="m", attachments=None)
        body = _FakeClient.captured["json"]
        assert "attachments" not in body

    def test_empty_attachments_list_excludes_field(self, patched_api):
        cli_invoke(message="hi", source_files={}, model="m", attachments=[])
        body = _FakeClient.captured["json"]
        assert "attachments" not in body

    def test_text_only_body_matches_legacy_shape(self, patched_api):
        """Text-only requests should look the same as before this change."""
        cli_invoke(message="hi", source_files={"a.py": "x"}, model="m")
        body = _FakeClient.captured["json"]
        assert set(body.keys()) == {
            "chat_id",
            "message",
            "source_files",
            "dry_run",
            "streaming",
            "model",
            "max_output_tokens",
        }

    def test_attachments_included_when_present(self, patched_api):
        attachments = [
            {
                "file_name": "shot.png",
                "mime_type": "image/png",
                "data_b64": "AAAA",
                "bytes_size": 3,
            }
        ]
        cli_invoke(
            message="describe",
            source_files={},
            model="m",
            attachments=attachments,
        )
        body = _FakeClient.captured["json"]
        assert "attachments" in body
        assert body["attachments"] == attachments

    def test_attachment_field_names_exact(self, patched_api):
        attachments = [
            {
                "file_name": "a.png",
                "mime_type": "image/png",
                "data_b64": "AAAA",
                "bytes_size": 3,
            }
        ]
        cli_invoke(message="hi", source_files={}, model="m", attachments=attachments)
        body = _FakeClient.captured["json"]
        assert set(body["attachments"][0].keys()) == {
            "file_name",
            "mime_type",
            "data_b64",
            "bytes_size",
        }

    def test_multiple_attachments_preserved_in_order(self, patched_api):
        attachments = [
            {"file_name": "a.png", "mime_type": "image/png", "data_b64": "AA", "bytes_size": 1},
            {"file_name": "b.jpg", "mime_type": "image/jpeg", "data_b64": "BB", "bytes_size": 2},
            {"file_name": "c.webp", "mime_type": "image/webp", "data_b64": "CC", "bytes_size": 3},
        ]
        cli_invoke(message="hi", source_files={}, model="m", attachments=attachments)
        body = _FakeClient.captured["json"]
        names = [a["file_name"] for a in body["attachments"]]
        assert names == ["a.png", "b.jpg", "c.webp"]


# ---------------------------------------------------------------------------
# Debug output does not include image bytes
# ---------------------------------------------------------------------------

class TestDebugDoesNotLeakImageBytes:
    def test_debug_output_excludes_data_b64(self, monkeypatch, capsys):
        # Enable debug mode for this test
        monkeypatch.setattr(api_module.httpx, "Client", _FakeClient)
        monkeypatch.setattr(api_module.httpx, "get", _fake_get_final)
        monkeypatch.setattr(api_module, "_auth_headers", lambda **kw: {"Authorization": "Bearer X"})
        monkeypatch.setattr(api_module, "_ssl_verify", lambda: True)
        monkeypatch.setattr(api_module, "_is_debug", lambda: True)
        _FakeClient.captured = {}

        attachments = [
            {
                "file_name": "a.png",
                "mime_type": "image/png",
                "data_b64": LONG_B64,
                "bytes_size": len(LONG_B64),
            }
        ]
        cli_invoke(message="hi", source_files={}, model="m", attachments=attachments)

        captured = capsys.readouterr()
        combined_output = captured.out + captured.err
        # The actual base64 payload must not appear anywhere in debug output
        assert LONG_B64 not in combined_output
        # But the redaction marker should
        assert "redacted" in combined_output
        # And other attachment metadata should still be visible for debugging
        assert "a.png" in combined_output
        assert "image/png" in combined_output

    def test_actual_request_body_still_contains_data_b64(self, monkeypatch):
        """Redaction must NOT affect the real outgoing request body."""
        monkeypatch.setattr(api_module.httpx, "Client", _FakeClient)
        monkeypatch.setattr(api_module.httpx, "get", _fake_get_final)
        monkeypatch.setattr(api_module, "_auth_headers", lambda **kw: {"Authorization": "Bearer X"})
        monkeypatch.setattr(api_module, "_ssl_verify", lambda: True)
        monkeypatch.setattr(api_module, "_is_debug", lambda: True)
        _FakeClient.captured = {}

        attachments = [
            {
                "file_name": "a.png",
                "mime_type": "image/png",
                "data_b64": LONG_B64,
                "bytes_size": len(LONG_B64),
            }
        ]
        cli_invoke(message="hi", source_files={}, model="m", attachments=attachments)
        sent_body = _FakeClient.captured["json"]
        assert sent_body["attachments"][0]["data_b64"] == LONG_B64


# ---------------------------------------------------------------------------
# invoke_llm forwards attachments
# ---------------------------------------------------------------------------

class TestInvokeLlmAttachmentsForwarding:
    def _make_conf(self, tmp_path):
        conf = MagicMock()
        conf.root = tmp_path
        conf.selected_model = "anthropic/claude-sonnet-4.6"
        conf.ground_truth = None
        conf.use_rag = False
        conf.file_mask = None
        conf.index_manager = None
        return conf

    def test_attachments_forwarded_to_local_plugin(self, tmp_path):
        from aye.controller import llm_invoker

        conf = self._make_conf(tmp_path)

        plugin_manager = MagicMock()
        # Local plugin returns a response so the API path is short-circuited.
        plugin_manager.handle_command.return_value = {
            "summary": "local ok",
            "updated_files": [],
        }

        attachments = [
            {"file_name": "a.png", "mime_type": "image/png", "data_b64": "AA", "bytes_size": 1}
        ]

        with patch.object(llm_invoker, "collect_sources", return_value={}):
            llm_invoker.invoke_llm(
                prompt="hi",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
                attachments=attachments,
            )

        # Verify the plugin was called with attachments in params.
        call_args = plugin_manager.handle_command.call_args
        assert call_args[0][0] == "local_model_invoke"
        params = call_args[0][1]
        assert "attachments" in params
        assert params["attachments"] == attachments

    def test_attachments_forwarded_to_cli_invoke(self, tmp_path):
        from aye.controller import llm_invoker

        conf = self._make_conf(tmp_path)

        plugin_manager = MagicMock()
        # No local handler \u2014 falls through to API path.
        plugin_manager.handle_command.return_value = None

        attachments = [
            {"file_name": "a.png", "mime_type": "image/png", "data_b64": "AA", "bytes_size": 1}
        ]

        with patch.object(llm_invoker, "collect_sources", return_value={}), \
             patch.object(llm_invoker, "cli_invoke") as mock_cli_invoke:
            mock_cli_invoke.return_value = {
                "assistant_response": '{"answer_summary": "ok", "source_files": []}',
                "chat_id": 7,
            }

            llm_invoker.invoke_llm(
                prompt="hi",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
                attachments=attachments,
            )

        # `cli_invoke` should have been called with the same attachments list.
        assert mock_cli_invoke.called
        kwargs = mock_cli_invoke.call_args.kwargs
        assert kwargs.get("attachments") == attachments

    def test_no_attachments_default_passes_empty_list_to_local_plugin(self, tmp_path):
        from aye.controller import llm_invoker

        conf = self._make_conf(tmp_path)
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "summary": "ok",
            "updated_files": [],
        }

        with patch.object(llm_invoker, "collect_sources", return_value={}):
            llm_invoker.invoke_llm(
                prompt="hi",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
            )

        params = plugin_manager.handle_command.call_args[0][1]
        # Local plugins should always see the key, defaulted to [].
        assert params.get("attachments") == []

    def test_no_attachments_default_passes_none_to_cli_invoke(self, tmp_path):
        from aye.controller import llm_invoker

        conf = self._make_conf(tmp_path)
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = None

        with patch.object(llm_invoker, "collect_sources", return_value={}), \
             patch.object(llm_invoker, "cli_invoke") as mock_cli_invoke:
            mock_cli_invoke.return_value = {
                "assistant_response": '{"answer_summary": "ok", "source_files": []}',
                "chat_id": 1,
            }

            llm_invoker.invoke_llm(
                prompt="hi",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
            )

        kwargs = mock_cli_invoke.call_args.kwargs
        # When the caller didn't pass attachments, cli_invoke gets None,
        # which keeps the request body identical to legacy text-only requests.
        assert kwargs.get("attachments") is None

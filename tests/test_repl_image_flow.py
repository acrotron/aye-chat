"""Tests for Phase 5: REPL wiring of image attachments + capability gating.

Covers:
- Image-only `@` prompt: ``explicit_source_files`` stays ``None``,
  attachments are forwarded to ``invoke_llm``.
- Source-only `@` prompt: existing behavior (explicit files, no attachments).
- Mixed prompt: explicit sources passed + attachments forwarded.
- Unsupported model + image: error printed, ``invoke_llm`` NOT called.
- Defensive check inside ``invoke_llm``: raises ``UnsupportedImageModelError``
  when REPL didn't gate.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aye.controller import repl
from aye.controller import llm_invoker
from aye.controller.llm_invoker import UnsupportedImageModelError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_at_response(file_contents=None, attachments=None,
                     cleaned_prompt="describe", error=None):
    """Build a plausible plugin response from ``parse_at_references``."""
    if file_contents is None and attachments is None and error is None:
        return None
    response = {
        "references": [],
        "expanded_files": [],
        "file_contents": file_contents or {},
        "attachments": attachments or [],
        "image_errors": [],
        "has_image_references": bool(attachments),
        "has_source_references": bool(file_contents),
        "cleaned_prompt": cleaned_prompt,
    }
    if error is not None:
        response["error"] = error
    return response


@pytest.fixture
def mock_conf(tmp_path, monkeypatch):
    """Build a minimal ``conf`` plus chdir into a writable tmp project."""
    monkeypatch.chdir(tmp_path)
    conf = MagicMock()
    conf.root = tmp_path
    conf.selected_model = "anthropic/claude-sonnet-4.6"  # supports images
    conf.verbose = False
    conf.file_mask = "*"
    conf.use_rag = False
    conf.index_manager = None
    conf.ground_truth = None
    conf.plugin_manager = MagicMock()
    return conf


def _drive_one_input(conf, user_input, at_response):
    """Run ``chat_repl`` for a single user input, then EOF.

    Returns:
        (mock_invoke_obj, captured_kwargs_list)
    """
    def handle_command(cmd, params):
        if cmd == "get_completer":
            return {"completer": None}
        if cmd == "parse_at_references":
            return at_response
        if cmd == "execute_shell_command":
            return None  # falls through to LLM path
        if cmd == "process_url":
            return None
        if cmd == "new_chat":
            return None
        return None

    conf.plugin_manager.handle_command.side_effect = handle_command

    session_mock = MagicMock()
    session_mock.prompt.side_effect = [user_input, EOFError()]

    captured = []

    def fake_invoke(*args, **kwargs):
        captured.append(kwargs)
        # Return a falsy-friendly response with no chat_id
        resp = MagicMock()
        resp.chat_id = None
        resp.summary = ""
        resp.updated_files = []
        return resp

    with patch.object(repl, "create_prompt_session", return_value=session_mock), \
         patch.object(repl, "run_first_time_tutorial_if_needed", return_value=False), \
         patch.object(repl, "print_startup_header"), \
         patch.object(repl, "_prompt_for_telemetry_consent_if_needed", return_value=False), \
         patch.object(repl, "_maybe_print_demo_registration_hint"), \
         patch.object(repl, "collect_and_send_feedback"), \
         patch.object(repl, "invoke_llm", side_effect=fake_invoke) as mock_invoke, \
         patch.object(repl, "process_llm_response", return_value=None), \
         patch.object(repl, "maybe_attach_shell_result", side_effect=lambda c, p: p), \
         patch.object(repl, "telemetry"):
        repl.chat_repl(conf)

    return mock_invoke, captured


def _image_attachment(name="shot.png", mime="image/png"):
    return {
        "file_name": name,
        "mime_type": mime,
        "data_b64": "QUFB",
        "bytes_size": 3,
    }


# ---------------------------------------------------------------------------
# REPL flow tests
# ---------------------------------------------------------------------------

class TestReplImageFlow:
    def test_image_only_prompt_keeps_normal_source_search(self, mock_conf):
        at_response = _make_at_response(
            file_contents={},
            attachments=[_image_attachment()],
        )
        mock_invoke, calls = _drive_one_input(
            mock_conf, "describe @shot.png", at_response
        )

        assert mock_invoke.called, "invoke_llm should be called for supported model"
        assert len(calls) == 1
        # Image-only \u2192 leave explicit_source_files=None so normal search runs.
        assert calls[0].get("explicit_source_files") is None
        atts = calls[0].get("attachments")
        assert atts is not None
        assert len(atts) == 1
        assert atts[0]["file_name"] == "shot.png"

    def test_source_only_prompt_passes_explicit_files(self, mock_conf):
        at_response = _make_at_response(
            file_contents={"main.py": "print('hi')"},
            attachments=[],
        )
        mock_invoke, calls = _drive_one_input(
            mock_conf, "explain @main.py", at_response
        )

        assert mock_invoke.called
        assert calls[0].get("explicit_source_files") == {"main.py": "print('hi')"}
        # No attachments \u2192 None (matches legacy text-only path)
        assert calls[0].get("attachments") is None

    def test_mixed_prompt_includes_both(self, mock_conf):
        at_response = _make_at_response(
            file_contents={"main.py": "code"},
            attachments=[_image_attachment()],
        )
        mock_invoke, calls = _drive_one_input(
            mock_conf, "review @main.py @shot.png", at_response
        )

        assert mock_invoke.called
        # Explicit sources passed (auto search suppressed).
        assert calls[0].get("explicit_source_files") == {"main.py": "code"}
        # Attachments forwarded.
        atts = calls[0].get("attachments")
        assert atts is not None and len(atts) == 1

    def test_unsupported_model_with_image_skips_api(self, mock_conf, capsys):
        # Offline model is not flagged as image-capable.
        mock_conf.selected_model = "offline/qwen2.5-coder-7b"
        at_response = _make_at_response(
            file_contents={},
            attachments=[_image_attachment()],
        )
        mock_invoke, calls = _drive_one_input(
            mock_conf, "describe @shot.png", at_response
        )

        # invoke_llm must not have been called.
        assert not mock_invoke.called, "REPL must skip API call for unsupported model"

    def test_text_only_prompt_no_attachments(self, mock_conf):
        """Backward-compat: a plain prompt with no @ refs still works."""
        mock_invoke, calls = _drive_one_input(
            mock_conf, "what is the weather?", None
        )
        assert mock_invoke.called
        assert calls[0].get("attachments") is None
        assert calls[0].get("explicit_source_files") is None

    def test_at_response_with_error_falls_back_to_plain_prompt(self, mock_conf):
        """When ``parse_at_references`` returns an error, REPL still runs the LLM
        without attachments or explicit files.
        """
        at_response = _make_at_response(error="No files found matching the @references")
        mock_invoke, calls = _drive_one_input(
            mock_conf, "explain @nope.py", at_response
        )
        assert mock_invoke.called
        assert calls[0].get("explicit_source_files") is None
        assert calls[0].get("attachments") is None


# ---------------------------------------------------------------------------
# Defensive check inside invoke_llm
# ---------------------------------------------------------------------------

class TestInvokeLlmDefensiveCheck:
    def _conf(self, tmp_path, model_id):
        return SimpleNamespace(
            root=tmp_path,
            file_mask="*",
            selected_model=model_id,
            index_manager=None,
            use_rag=False,
            ground_truth=None,
        )

    def test_unsupported_model_raises(self, tmp_path):
        conf = self._conf(tmp_path, "offline/qwen2.5-coder-7b")
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = None

        attachments = [{
            "file_name": "a.png",
            "mime_type": "image/png",
            "data_b64": "AA",
            "bytes_size": 1,
        }]

        with pytest.raises(UnsupportedImageModelError, match="does not support image"):
            llm_invoker.invoke_llm(
                prompt="describe",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
                attachments=attachments,
            )

        # Critically: no work should have been done before the raise.
        plugin_manager.handle_command.assert_not_called()

    def test_supported_model_proceeds(self, tmp_path):
        conf = self._conf(tmp_path, "anthropic/claude-sonnet-4.6")
        plugin_manager = MagicMock()
        # Local plugin returns a response so the API path is skipped.
        plugin_manager.handle_command.return_value = {
            "summary": "ok",
            "updated_files": [],
        }

        attachments = [{
            "file_name": "a.png",
            "mime_type": "image/png",
            "data_b64": "AA",
            "bytes_size": 1,
        }]

        with patch.object(llm_invoker, "collect_sources", return_value={}):
            result = llm_invoker.invoke_llm(
                prompt="describe",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
                attachments=attachments,
            )

        assert result is not None

    def test_no_attachments_skips_capability_check(self, tmp_path):
        """Without attachments, even non-multimodal models proceed normally."""
        conf = self._conf(tmp_path, "offline/qwen2.5-coder-7b")
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "summary": "ok",
            "updated_files": [],
        }

        with patch.object(llm_invoker, "collect_sources", return_value={}):
            result = llm_invoker.invoke_llm(
                prompt="hi",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
            )

        assert result is not None

    def test_empty_attachments_list_skips_capability_check(self, tmp_path):
        """``attachments=[]`` is equivalent to None and should not block."""
        conf = self._conf(tmp_path, "offline/qwen2.5-coder-7b")
        plugin_manager = MagicMock()
        plugin_manager.handle_command.return_value = {
            "summary": "ok",
            "updated_files": [],
        }

        with patch.object(llm_invoker, "collect_sources", return_value={}):
            result = llm_invoker.invoke_llm(
                prompt="hi",
                conf=conf,
                console=MagicMock(),
                plugin_manager=plugin_manager,
                attachments=[],
            )

        assert result is not None

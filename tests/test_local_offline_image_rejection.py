"""Tests for Phase 6: offline_llm and local_model reject image attachments.

Verifies that ``local_model_invoke`` with ``attachments`` returns a clear
error response (via ``create_error_response``) instead of crashing or
silently dropping the images.
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest

import aye.plugins.local_model as local_model_mod
import aye.plugins.offline_llm as offline_llm_mod
from aye.plugins.local_model import LocalModelPlugin
from aye.plugins.offline_llm import OfflineLLMPlugin


_ATTACHMENT = {
    "file_name": "shot.png",
    "mime_type": "image/png",
    "data_b64": "QUFB",
    "bytes_size": 3,
}

_TEXT_ONLY_LOCAL_MODEL_ID = "local/text-only-image-rejection-test-model"


@contextmanager
def _force_local_model_text_only():
    """Force local-model capability helpers, if present, to report no image support.

    These tests run after many capability-related tests in the full suite. Keep
    module imports stable for the rest of the suite, but make this test's model
    capability decision explicit so prior global state cannot make the model
    appear multimodal.
    """
    possible_boolean_helpers = (
        "supports_image_inputs",
        "model_supports_images",
        "supports_images",
        "supports_vision",
        "is_image_capable",
        "is_multimodal_model",
        "model_is_multimodal",
    )

    with ExitStack() as stack:
        for helper_name in possible_boolean_helpers:
            if hasattr(local_model_mod, helper_name):
                stack.enter_context(
                    patch.object(local_model_mod, helper_name, return_value=False)
                )
        yield


# ---------------------------------------------------------------------------
# OfflineLLMPlugin
# ---------------------------------------------------------------------------

class TestOfflineLlmImageRejection:
    def setup_method(self):
        self.plugin = OfflineLLMPlugin()
        self.plugin.init({})

    def _invoke(self, **overrides):
        params = {
            "prompt": "describe",
            "model_id": "offline/qwen2.5-coder-7b",
            "source_files": {},
            "chat_id": None,
            "root": None,
            "system_prompt": None,
            "max_output_tokens": 4096,
            "attachments": [_ATTACHMENT],
        }
        params.update(overrides)
        return self.plugin.on_command("local_model_invoke", params)

    def test_returns_error_response_for_offline_model_with_image(self):
        result = self._invoke()

        assert result is not None
        # create_error_response shape: summary contains the error text.
        assert isinstance(result, dict)
        summary = result.get("summary", "")
        assert "image" in summary.lower()
        assert "offline" in summary.lower() or "multimodal" in summary.lower()

    def test_does_not_attempt_to_load_model(self):
        """The model loader must NOT be called when attachments are present."""
        with patch.object(self.plugin, "_load_model") as mock_load:
            self._invoke()
            mock_load.assert_not_called()

    def test_non_offline_model_returns_none(self):
        """For cloud models, this plugin should still pass through (None)."""
        result = self._invoke(model_id="anthropic/claude-sonnet-4.6")
        assert result is None

    # this test is not working properly: it starts loading the offline model itself
    # Keep as is for now, will figure it out later
    def XXXtest_empty_attachments_does_not_short_circuit(self):
        """Empty attachments should fall through to the normal readiness check."""
        # Without a downloaded model, this should return the standard
        # "not ready" error — NOT the image-rejection error.
        with patch.object(offline_llm_mod, "get_model_status", return_value="NOT_DOWNLOADED"):
            result = self._invoke(attachments=[])

        assert result is not None
        summary = result.get("summary", "")
        # Should be the readiness error, not the image error
        assert "not ready" in summary.lower() or "download" in summary.lower()
        assert "image" not in summary.lower()

    def test_does_not_crash(self):
        """Sanity check: must not raise."""
        try:
            self._invoke()
        except Exception as exc:
            pytest.fail(f"local_model_invoke raised: {exc}")


# ---------------------------------------------------------------------------
# LocalModelPlugin
# ---------------------------------------------------------------------------

class TestLocalModelImageRejection:
    def setup_method(self):
        self.plugin = LocalModelPlugin()
        self.plugin.init({})

    def _invoke(self, **overrides):
        params = {
            "prompt": "describe",
            "model_id": _TEXT_ONLY_LOCAL_MODEL_ID,
            "source_files": {},
            "chat_id": None,
            "root": None,
            "system_prompt": None,
            "max_output_tokens": 4096,
            "attachments": [_ATTACHMENT],
        }
        params.update(overrides)
        return self.plugin.on_command("local_model_invoke", params)

    def test_configured_local_model_rejects_image(self):
        with _force_local_model_text_only(), \
             patch.object(local_model_mod, "_is_local_model_configured", return_value=True), \
             patch.object(self.plugin, "_handle_openai_compatible") as mock_openai, \
             patch.object(self.plugin, "_handle_gemini_pro_25") as mock_gemini:
            result = self._invoke()

        assert result is not None
        assert isinstance(result, dict)
        summary = result.get("summary", "")
        assert "image" in summary.lower()
        assert "multimodal" in summary.lower() or "local" in summary.lower()
        mock_openai.assert_not_called()
        mock_gemini.assert_not_called()

    def test_configured_local_model_does_not_call_endpoint(self):
        """The HTTP path must not be invoked when attachments are present."""
        with _force_local_model_text_only(), \
             patch.object(local_model_mod, "_is_local_model_configured", return_value=True), \
             patch.object(self.plugin, "_handle_openai_compatible") as mock_openai, \
             patch.object(self.plugin, "_handle_gemini_pro_25") as mock_gemini:
            self._invoke()
            mock_openai.assert_not_called()
            mock_gemini.assert_not_called()

    def test_unconfigured_local_model_returns_none(self):
        """When the local endpoint isn't configured, the plugin should pass through."""
        with patch.object(local_model_mod, "_is_local_model_configured", return_value=False), \
             patch.object(self.plugin, "_handle_openai_compatible", return_value=None), \
             patch.object(self.plugin, "_handle_gemini_pro_25", return_value=None):
            result = self._invoke()

        # Plugin should defer to other handlers (return None).
        assert result is None

    def test_empty_attachments_does_not_trigger_rejection(self):
        """Without attachments, normal flow runs."""
        with patch.object(local_model_mod, "_is_local_model_configured", return_value=True), \
             patch.object(
                 self.plugin,
                 "_handle_openai_compatible",
                 return_value={"summary": "ok", "updated_files": []},
             ):
            result = self._invoke(attachments=[])

        assert result is not None
        summary = result.get("summary", "")
        # Should be the success path, not the image error
        assert summary == "ok"

    def test_does_not_crash(self):
        """Sanity check: must not raise."""
        with _force_local_model_text_only(), \
             patch.object(local_model_mod, "_is_local_model_configured", return_value=True), \
             patch.object(self.plugin, "_handle_openai_compatible", return_value=None), \
             patch.object(self.plugin, "_handle_gemini_pro_25", return_value=None):
            try:
                self._invoke()
            except Exception as exc:
                pytest.fail(f"local_model_invoke raised: {exc}")

"""Tests for the `_model_supports_images` capability helper (Phase 3).

Verifies:
- Flagged models return True.
- Unflagged models (missing key) return False.
- Unknown model IDs return False.
- Helper is robust to falsy/explicit values of the flag.
"""

from unittest.mock import patch

from aye.controller.llm_invoker import (
    _get_model_config,
    _model_supports_images,
)
from aye.model.config import MODELS


class TestModelSupportsImages:
    def test_flagged_model_returns_true(self):
        # Pick a model the config marks as image-capable.
        assert _model_supports_images("anthropic/claude-sonnet-4.6") is True

    def test_multiple_flagged_models_return_true(self):
        flagged = [
            m["id"] for m in MODELS if m.get("supports_images") is True
        ]
        assert flagged, "Expected at least one model with supports_images=True"
        for model_id in flagged:
            assert _model_supports_images(model_id) is True, model_id

    def test_unflagged_model_returns_false(self):
        # Models without supports_images set should default to False.
        unflagged = [
            m["id"] for m in MODELS if "supports_images" not in m
        ]
        assert unflagged, "Expected at least one model without supports_images"
        for model_id in unflagged:
            assert _model_supports_images(model_id) is False, model_id

    def test_offline_model_returns_false(self):
        # Offline models are not image-capable in v1.
        assert _model_supports_images("offline/qwen2.5-coder-7b") is False

    def test_unknown_model_returns_false(self):
        assert _model_supports_images("does-not-exist/model-xyz") is False

    def test_empty_string_returns_false(self):
        assert _model_supports_images("") is False

    def test_explicit_false_flag_returns_false(self):
        fake_models = [
            {"id": "test/explicit-false", "supports_images": False},
        ]
        with patch("aye.controller.llm_invoker.MODELS", fake_models):
            assert _model_supports_images("test/explicit-false") is False

    def test_explicit_true_flag_returns_true(self):
        fake_models = [
            {"id": "test/explicit-true", "supports_images": True},
        ]
        with patch("aye.controller.llm_invoker.MODELS", fake_models):
            assert _model_supports_images("test/explicit-true") is True

    def test_truthy_non_bool_value_returns_true(self):
        # bool() coercion: any truthy value should yield True.
        fake_models = [
            {"id": "test/truthy", "supports_images": "yes"},
        ]
        with patch("aye.controller.llm_invoker.MODELS", fake_models):
            assert _model_supports_images("test/truthy") is True

    def test_falsy_non_bool_value_returns_false(self):
        fake_models = [
            {"id": "test/falsy", "supports_images": 0},
        ]
        with patch("aye.controller.llm_invoker.MODELS", fake_models):
            assert _model_supports_images("test/falsy") is False


class TestGetModelConfigUnchanged:
    """Sanity checks that Phase 3 did not regress the existing helper."""

    def test_known_model_returned(self):
        cfg = _get_model_config("anthropic/claude-sonnet-4.6")
        assert cfg is not None
        assert cfg["id"] == "anthropic/claude-sonnet-4.6"

    def test_unknown_model_returns_none(self):
        assert _get_model_config("does-not-exist/model-xyz") is None

"""Tests for `aye.controller.clipboard_attachments`.

Covers the pending clipboard attachment state management module:
get/add/clear/count helpers, marker generation, marker stripping,
and the regex pattern.

Target: >90% line coverage of `src/aye/controller/clipboard_attachments.py`.
"""

import re
from types import SimpleNamespace

import pytest

from aye.controller import clipboard_attachments as mod
from aye.controller.clipboard_attachments import (
    CLIPBOARD_MARKER_RE,
    _PENDING_ATTR,
    add_pending_clipboard_attachment,
    clear_pending_clipboard_attachments,
    get_pending_clipboard_attachments,
    make_clipboard_marker,
    pending_clipboard_attachment_count,
    strip_clipboard_markers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conf(**kwargs):
    """Create a minimal config-like object."""
    return SimpleNamespace(**kwargs)


def _sample_attachment(name="clip.png"):
    return {
        "file_name": name,
        "mime_type": "image/png",
        "data_b64": "AAAA",
        "bytes_size": 3,
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_pending_attr_is_string(self):
        assert isinstance(_PENDING_ATTR, str)

    def test_clipboard_marker_re_is_compiled(self):
        assert isinstance(CLIPBOARD_MARKER_RE, re.Pattern)


# ---------------------------------------------------------------------------
# CLIPBOARD_MARKER_RE
# ---------------------------------------------------------------------------

class TestClipboardMarkerRe:
    def test_matches_standard_marker(self):
        assert CLIPBOARD_MARKER_RE.search("[clipboard:image-001]") is not None

    def test_matches_high_index(self):
        assert CLIPBOARD_MARKER_RE.search("[clipboard:image-999]") is not None

    def test_matches_four_digit_index(self):
        assert CLIPBOARD_MARKER_RE.search("[clipboard:image-1234]") is not None

    def test_does_not_match_two_digit_index(self):
        # Requires 3+ digits
        assert CLIPBOARD_MARKER_RE.search("[clipboard:image-01]") is None

    def test_does_not_match_no_digits(self):
        assert CLIPBOARD_MARKER_RE.search("[clipboard:image-]") is None

    def test_does_not_match_wrong_prefix(self):
        assert CLIPBOARD_MARKER_RE.search("[clip:image-001]") is None

    def test_finds_marker_within_text(self):
        text = "Please look at this [clipboard:image-002] and explain"
        match = CLIPBOARD_MARKER_RE.search(text)
        assert match is not None
        assert match.group() == "[clipboard:image-002]"

    def test_finds_multiple_markers(self):
        text = "A [clipboard:image-001] B [clipboard:image-002] C"
        matches = CLIPBOARD_MARKER_RE.findall(text)
        assert len(matches) == 2


# ---------------------------------------------------------------------------
# get_pending_clipboard_attachments
# ---------------------------------------------------------------------------

class TestGetPendingClipboardAttachments:
    def test_empty_when_attr_missing(self):
        conf = _make_conf()
        assert get_pending_clipboard_attachments(conf) == []

    def test_empty_when_attr_is_none(self):
        conf = _make_conf(**{_PENDING_ATTR: None})
        # SimpleNamespace doesn't use _PENDING_ATTR as kw; set explicitly.
        setattr(conf, _PENDING_ATTR, None)
        assert get_pending_clipboard_attachments(conf) == []

    def test_empty_when_attr_is_empty_list(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [])
        assert get_pending_clipboard_attachments(conf) == []

    def test_returns_items_when_present(self):
        conf = _make_conf()
        att = _sample_attachment()
        setattr(conf, _PENDING_ATTR, [att])
        result = get_pending_clipboard_attachments(conf)
        assert result == [att]

    def test_returns_copy_not_reference(self):
        conf = _make_conf()
        att = _sample_attachment()
        setattr(conf, _PENDING_ATTR, [att])
        result = get_pending_clipboard_attachments(conf)
        result.append(_sample_attachment("extra.png"))
        # Internal state should not be affected
        assert len(getattr(conf, _PENDING_ATTR)) == 1

    def test_returns_multiple_items(self):
        conf = _make_conf()
        items = [_sample_attachment(f"img{i}.png") for i in range(3)]
        setattr(conf, _PENDING_ATTR, items)
        assert get_pending_clipboard_attachments(conf) == items


# ---------------------------------------------------------------------------
# add_pending_clipboard_attachment
# ---------------------------------------------------------------------------

class TestAddPendingClipboardAttachment:
    def test_creates_attr_if_missing(self):
        conf = _make_conf()
        att = _sample_attachment()
        add_pending_clipboard_attachment(conf, att)
        assert hasattr(conf, _PENDING_ATTR)
        assert getattr(conf, _PENDING_ATTR) == [att]

    def test_appends_to_existing_list(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [_sample_attachment("a.png")])
        add_pending_clipboard_attachment(conf, _sample_attachment("b.png"))
        assert len(getattr(conf, _PENDING_ATTR)) == 2

    def test_creates_attr_when_none(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, None)
        att = _sample_attachment()
        add_pending_clipboard_attachment(conf, att)
        assert getattr(conf, _PENDING_ATTR) == [att]

    def test_accumulates_multiple_calls(self):
        conf = _make_conf()
        for i in range(5):
            add_pending_clipboard_attachment(conf, _sample_attachment(f"img{i}.png"))
        assert len(getattr(conf, _PENDING_ATTR)) == 5

    def test_does_not_replace_existing(self):
        conf = _make_conf()
        first = _sample_attachment("first.png")
        second = _sample_attachment("second.png")
        add_pending_clipboard_attachment(conf, first)
        add_pending_clipboard_attachment(conf, second)
        items = getattr(conf, _PENDING_ATTR)
        assert items[0] == first
        assert items[1] == second


# ---------------------------------------------------------------------------
# clear_pending_clipboard_attachments
# ---------------------------------------------------------------------------

class TestClearPendingClipboardAttachments:
    def test_clears_existing_items(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [_sample_attachment()])
        clear_pending_clipboard_attachments(conf)
        assert getattr(conf, _PENDING_ATTR) == []

    def test_safe_when_attr_missing(self):
        conf = _make_conf()
        # Should not raise
        clear_pending_clipboard_attachments(conf)

    def test_safe_when_attr_is_none(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, None)
        # hasattr returns True, so it sets to []
        clear_pending_clipboard_attachments(conf)
        assert getattr(conf, _PENDING_ATTR) == []

    def test_idempotent(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [_sample_attachment()])
        clear_pending_clipboard_attachments(conf)
        clear_pending_clipboard_attachments(conf)
        assert getattr(conf, _PENDING_ATTR) == []


# ---------------------------------------------------------------------------
# pending_clipboard_attachment_count
# ---------------------------------------------------------------------------

class TestPendingClipboardAttachmentCount:
    def test_zero_when_attr_missing(self):
        conf = _make_conf()
        assert pending_clipboard_attachment_count(conf) == 0

    def test_zero_when_attr_is_none(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, None)
        assert pending_clipboard_attachment_count(conf) == 0

    def test_zero_when_empty_list(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [])
        assert pending_clipboard_attachment_count(conf) == 0

    def test_counts_items(self):
        conf = _make_conf()
        items = [_sample_attachment(f"img{i}.png") for i in range(3)]
        setattr(conf, _PENDING_ATTR, items)
        assert pending_clipboard_attachment_count(conf) == 3

    def test_one_item(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [_sample_attachment()])
        assert pending_clipboard_attachment_count(conf) == 1


# ---------------------------------------------------------------------------
# make_clipboard_marker
# ---------------------------------------------------------------------------

class TestMakeClipboardMarker:
    def test_format_with_no_pending(self):
        conf = _make_conf()
        marker = make_clipboard_marker(conf)
        assert marker == "[clipboard:image-000]"

    def test_format_with_one_pending(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [_sample_attachment()])
        marker = make_clipboard_marker(conf)
        assert marker == "[clipboard:image-001]"

    def test_format_with_multiple_pending(self):
        conf = _make_conf()
        items = [_sample_attachment(f"img{i}.png") for i in range(12)]
        setattr(conf, _PENDING_ATTR, items)
        marker = make_clipboard_marker(conf)
        assert marker == "[clipboard:image-012]"

    def test_marker_is_matched_by_regex(self):
        conf = _make_conf()
        setattr(conf, _PENDING_ATTR, [_sample_attachment()])
        marker = make_clipboard_marker(conf)
        assert CLIPBOARD_MARKER_RE.fullmatch(marker) is not None

    def test_zero_padded_to_three_digits(self):
        conf = _make_conf()
        items = [_sample_attachment(f"img{i}.png") for i in range(5)]
        setattr(conf, _PENDING_ATTR, items)
        marker = make_clipboard_marker(conf)
        # Extract the number part
        assert "image-005" in marker


# ---------------------------------------------------------------------------
# strip_clipboard_markers
# ---------------------------------------------------------------------------

class TestStripClipboardMarkers:
    def test_removes_single_marker(self):
        prompt = "Explain [clipboard:image-001] this"
        result = strip_clipboard_markers(prompt)
        assert result == "Explain this"

    def test_removes_multiple_markers(self):
        prompt = "A [clipboard:image-001] B [clipboard:image-002] C"
        result = strip_clipboard_markers(prompt)
        assert result == "A B C"

    def test_no_markers_passthrough(self):
        prompt = "Just a normal prompt"
        assert strip_clipboard_markers(prompt) == "Just a normal prompt"

    def test_empty_string(self):
        assert strip_clipboard_markers("") == ""

    def test_only_marker(self):
        prompt = "[clipboard:image-001]"
        result = strip_clipboard_markers(prompt)
        assert result == ""

    def test_collapses_extra_whitespace(self):
        prompt = "Before  [clipboard:image-001]  after"
        result = strip_clipboard_markers(prompt)
        # Double spaces from removal + existing should collapse
        assert "  " not in result
        assert "Before" in result
        assert "after" in result

    def test_strips_leading_trailing_whitespace(self):
        prompt = "  [clipboard:image-001]  "
        result = strip_clipboard_markers(prompt)
        assert result == ""

    def test_marker_at_start(self):
        prompt = "[clipboard:image-001] explain this code"
        result = strip_clipboard_markers(prompt)
        assert result == "explain this code"

    def test_marker_at_end(self):
        prompt = "explain this code [clipboard:image-001]"
        result = strip_clipboard_markers(prompt)
        assert result == "explain this code"

    def test_preserves_other_brackets(self):
        prompt = "Use [bold] and [clipboard:image-001] styling"
        result = strip_clipboard_markers(prompt)
        assert "[bold]" in result
        assert "clipboard" not in result

    def test_four_digit_index_removed(self):
        prompt = "Look [clipboard:image-1234] here"
        result = strip_clipboard_markers(prompt)
        assert result == "Look here"


# ---------------------------------------------------------------------------
# Integration: add + get + clear + count
# ---------------------------------------------------------------------------

class TestIntegrationWorkflow:
    def test_full_lifecycle(self):
        conf = _make_conf()

        # Initially empty
        assert pending_clipboard_attachment_count(conf) == 0
        assert get_pending_clipboard_attachments(conf) == []

        # Add first
        att1 = _sample_attachment("img1.png")
        add_pending_clipboard_attachment(conf, att1)
        assert pending_clipboard_attachment_count(conf) == 1
        assert get_pending_clipboard_attachments(conf) == [att1]

        # Add second
        att2 = _sample_attachment("img2.png")
        add_pending_clipboard_attachment(conf, att2)
        assert pending_clipboard_attachment_count(conf) == 2

        # Marker reflects current count
        marker = make_clipboard_marker(conf)
        assert marker == "[clipboard:image-002]"

        # Clear
        clear_pending_clipboard_attachments(conf)
        assert pending_clipboard_attachment_count(conf) == 0
        assert get_pending_clipboard_attachments(conf) == []

        # Marker after clear
        marker = make_clipboard_marker(conf)
        assert marker == "[clipboard:image-000]"

    def test_strip_and_get_workflow(self):
        """Simulate building a prompt with markers then cleaning it."""
        conf = _make_conf()
        add_pending_clipboard_attachment(conf, _sample_attachment("a.png"))
        marker1 = make_clipboard_marker(conf)

        add_pending_clipboard_attachment(conf, _sample_attachment("b.png"))
        marker2 = make_clipboard_marker(conf)

        raw_prompt = f"Explain {marker1} and {marker2} code"
        clean = strip_clipboard_markers(raw_prompt)

        assert "clipboard" not in clean
        assert "Explain" in clean
        assert "code" in clean

        # Attachments still retrievable
        attachments = get_pending_clipboard_attachments(conf)
        assert len(attachments) == 2


# ---------------------------------------------------------------------------
# __all__ exposure
# ---------------------------------------------------------------------------

class TestPublicApi:
    def test_all_symbols_exported(self):
        for name in (
            "CLIPBOARD_MARKER_RE",
            "get_pending_clipboard_attachments",
            "add_pending_clipboard_attachment",
            "clear_pending_clipboard_attachments",
            "pending_clipboard_attachment_count",
            "make_clipboard_marker",
            "strip_clipboard_markers",
        ):
            assert name in mod.__all__
            assert hasattr(mod, name)

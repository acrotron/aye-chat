"""Tests for tool-protocol parsing (split_summary and friends)."""

import json

from aye.model.tool_protocol import (
    MAX_CALLS_PER_ROUND,
    parse_tool_calls,
    split_summary,
    strip_tool_protocol,
)


def _req(*calls):
    return json.dumps(
        {"tool_calls": [{"name": name, "arguments": args} for name, args in calls]}
    )


class TestSplitSummary:
    def test_bare_request_has_no_narration(self):
        parsed = split_summary(_req(("read", {"path": "a.py"})))
        assert parsed.narration == ""
        assert [c.name for c in parsed.calls] == ["read"]
        assert parsed.requested == 1

    def test_narration_before_and_after_is_kept(self):
        summary = "Before.\n" + _req(("glob", {"pattern": "*.py"})) + "\nAfter."
        parsed = split_summary(summary)
        assert parsed.narration == "Before.\n\nAfter."
        assert [c.name for c in parsed.calls] == ["glob"]

    def test_braces_in_narration_do_not_confuse_extraction(self):
        summary = (
            'The config is {"a": 1, "b": [2]} here.\n'
            + _req(("read", {"path": "cfg.py"}))
        )
        parsed = split_summary(summary)
        assert parsed.narration == 'The config is {"a": 1, "b": [2]} here.'
        assert [c.name for c in parsed.calls] == ["read"]

    def test_multiple_objects_merge(self):
        summary = (
            "Looking around.\n"
            + _req(("glob", {"pattern": "*.py"}))
            + "\nalso:\n"
            + _req(("grep", {"pattern": "x"}))
        )
        parsed = split_summary(summary)
        assert [c.name for c in parsed.calls] == ["glob", "grep"]
        assert parsed.requested == 2

    def test_truncated_object_is_cut_from_narration(self):
        summary = 'Checking.\n{"tool_calls": [{"name": "grep"'  # no closing
        parsed = split_summary(summary)
        assert parsed.calls == []
        assert parsed.narration == "Checking."

    def test_dangling_object_prefix_before_the_key_is_cut(self):
        """Streams show '{' before the "tool_calls" key finishes arriving."""
        parsed = split_summary('Answer.\n{"tool_c')
        assert parsed.calls == []
        assert parsed.narration == "Answer."

    def test_prose_ending_in_a_bare_brace_is_kept(self):
        parsed = split_summary("The set so far is {1, 2")
        assert parsed.narration == "The set so far is {1, 2"

    def test_balanced_garbage_snippet_is_removed_from_narration(self):
        summary = "Use {\"tool_calls\": x} syntax carefully in prose."
        parsed = split_summary(summary)
        assert parsed.calls == []
        assert "syntax carefully" in parsed.narration
        assert "tool_calls" not in parsed.narration

    def test_malformed_blob_between_prose_is_removed(self):
        summary = 'Sure.\n{"tool_calls": oops}\nDone.'
        parsed = split_summary(summary)
        assert parsed.calls == []
        assert parsed.narration == "Sure.\n\nDone."

    def test_braces_inside_blob_strings_do_not_confuse_balance(self):
        summary = 'Note.\n{"tool_calls": "a } b { c"}'
        parsed = split_summary(summary)
        assert parsed.narration == "Note."

    def test_empty_calls_object_is_not_a_request(self):
        assert parse_tool_calls('{"tool_calls": []}') == []
        assert strip_tool_protocol('{"tool_calls": []}') == ""

    def test_malformed_tool_calls_value_yields_no_calls(self):
        assert parse_tool_calls('{"tool_calls": "oops"}') == []

    def test_duplicates_merge_and_cap_counts_unique(self):
        calls = [("read", {"path": f"f{i}.txt"}) for i in range(8)]
        calls.append(("read", {"path": "f0.txt"}))  # duplicate of the first
        parsed = split_summary(_req(*calls))
        assert parsed.requested == 8
        assert len(parsed.calls) == MAX_CALLS_PER_ROUND

    def test_singular_and_dict_forms_are_tolerated(self):
        parsed = split_summary(
            json.dumps({"tool_call": {"name": "glob", "arguments": {"pattern": "*"}}})
        )
        assert [c.name for c in parsed.calls] == ["glob"]

    def test_fence_around_json_only_is_consumed(self):
        summary = "Note\n```json\n" + _req(("read", {"path": "a.py"})) + "\n```\nDone"
        parsed = split_summary(summary)
        assert parsed.narration == "Note\n\nDone"
        assert [c.name for c in parsed.calls] == ["read"]

    def test_fence_wrapping_whole_body(self):
        summary = "```json\n" + _req(("read", {"path": "a.py"})) + "\n```"
        parsed = split_summary(summary)
        assert parsed.narration == ""
        assert [c.name for c in parsed.calls] == ["read"]

    def test_prose_with_json_snippet_is_not_a_request(self):
        summary = 'Run `{"tool_calls": ...}` is what the model sends.'
        parsed = split_summary(summary)
        # Not a usable request, and protocol-shaped text never reaches the
        # user: the blob is removed, the surrounding prose is kept.
        assert parsed.calls == []
        assert "tool_calls" not in parsed.narration
        assert "is what the model sends" in parsed.narration


class TestStripToolProtocol:
    def test_prose_passes_through_unchanged(self):
        assert strip_tool_protocol("plain answer") == "plain answer"

    def test_bare_request_strips_to_empty(self):
        assert strip_tool_protocol(_req(("grep", {"pattern": "x"}))) == ""

    def test_mixed_keeps_narration_only(self):
        summary = "Let me check the tests.\n" + _req(("glob", {"pattern": "t.py"}))
        assert strip_tool_protocol(summary) == "Let me check the tests."

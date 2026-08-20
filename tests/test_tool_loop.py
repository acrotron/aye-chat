"""Tests for the agentic tool loop (tool_loop.py)."""

import json
from types import SimpleNamespace

from aye.controller.tool_loop import MAX_TOOL_ROUNDS, run_tool_loop
from aye.model.tool_protocol import looks_like_protocol_json


def _resp(answer_summary, source_files=None, chat_id=7):
    return {
        "assistant_response": json.dumps(
            {
                "answer_summary": answer_summary,
                "source_files": source_files or [],
            }
        ),
        "chat_id": chat_id,
    }


def _conf(tmp_path):
    return SimpleNamespace(root=tmp_path, selected_model="test-model")


def _tool_request(name, arguments):
    return json.dumps({"tool_calls": [{"name": name, "arguments": arguments}]})


class TestReadRound:
    def test_read_then_prose(self, tmp_path, monkeypatch):
        (tmp_path / "notes.txt").write_text("hello world", encoding="utf-8")
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("the notes say hello world", chat_id=11)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)

        summary, files, chat_id = run_tool_loop(
            initial_summary=_tool_request("read", {"path": "notes.txt"}),
            updated_files=[],
            chat_id=1,
            prompt="what do the notes say?",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )

        assert "the notes say hello world" in summary
        assert chat_id == 11
        assert "hello world" in calls[0]["message"]

    def test_followup_restates_the_original_question(self, tmp_path, monkeypatch):
        (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        run_tool_loop(
            initial_summary=_tool_request("read", {"path": "notes.txt"}),
            updated_files=[],
            chat_id=1,
            prompt="original question here",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert "original question here" in calls[0]["message"]


class TestWriteRound:
    def test_write_tool_creates_the_file(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        run_tool_loop(
            initial_summary=_tool_request(
                "write", {"path": "out.txt", "content": "data"}
            ),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "data"
        assert "out.txt" in calls[0]["message"]


class TestShellApproval:
    def test_shell_call_prompts_for_confirmation(self, tmp_path, monkeypatch):
        prompted = []
        executed = []

        def fake_cli_invoke(**kwargs):
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        monkeypatch.setattr(
            "aye.controller.tool_loop.needs_confirmation", lambda name, registry: True
        )
        monkeypatch.setattr(
            "aye.controller.tool_loop.confirm_command",
            lambda cmd, **kw: prompted.append(cmd) or True,
        )
        monkeypatch.setattr(
            "aye.controller.tool_loop.execute_tool",
            lambda name, args, root: executed.append((name, args)) or "ran",
        )

        run_tool_loop(
            initial_summary=_tool_request("cmd", {"command": "pytest -q"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert prompted == ["pytest -q"]
        assert executed == [("cmd", {"command": "pytest -q"})]

    def test_declined_shell_call_is_reported_to_the_model(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("understood", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        monkeypatch.setattr(
            "aye.controller.tool_loop.needs_confirmation", lambda name, registry: True
        )
        monkeypatch.setattr(
            "aye.controller.tool_loop.confirm_command", lambda cmd, **kw: False
        )
        monkeypatch.setattr(
            "aye.controller.tool_loop.execute_tool",
            lambda name, args, root: (_ for _ in ()).throw(AssertionError("should not run")),
        )

        run_tool_loop(
            initial_summary=_tool_request("bash", {"command": "rm -rf ."}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert "declined" in calls[0]["message"]

    def test_full_mode_skips_confirmation(self, tmp_path, monkeypatch):
        executed = []

        def fake_cli_invoke(**kwargs):
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        monkeypatch.setattr(
            "aye.controller.tool_loop.needs_confirmation", lambda name, registry: False
        )
        monkeypatch.setattr(
            "aye.controller.tool_loop.execute_tool",
            lambda name, args, root: executed.append((name, args)) or "ran",
        )

        run_tool_loop(
            initial_summary=_tool_request("cmd", {"command": "pytest"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert executed == [("cmd", {"command": "pytest"})]


class TestRoundBudget:
    def test_loop_stops_when_model_answers_in_prose(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("final answer", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, _ = run_tool_loop(
            initial_summary=_tool_request("glob", {"pattern": "*.py"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert "final answer" in summary
        assert len(calls) == 1

    def test_round_budget_is_capped(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) < 5:
                return _resp(_tool_request("glob", {"pattern": "*.py"}), chat_id=1)
            return _resp("done with the task", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, _ = run_tool_loop(
            initial_summary=_tool_request("glob", {"pattern": "*.py"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
            max_rounds=4,
        )
        # 4 budget rounds plus one forced prose round: the raw tool-call JSON
        # must never be returned as the final answer.
        assert len(calls) == 5
        forced = calls[-1]
        assert "tool call limit" in forced["message"].lower()
        assert forced["system_prompt"] == "sys"
        assert "done with the task" in summary
        assert looks_like_protocol_json(summary) is False

    def test_default_budget_allows_multi_file_creation(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) < MAX_TOOL_ROUNDS:
                return _resp(_tool_request("write", {"path": "f.tsx", "content": "x"}), chat_id=1)
            return _resp("created the files", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, _ = run_tool_loop(
            initial_summary=_tool_request("write", {"path": "a.tsx", "content": "x"}),
            updated_files=[],
            chat_id=1,
            prompt="create 6 tsx files",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert "created the files" in summary
        assert len(calls) > 5


class TestUpdatedFiles:
    def test_files_are_merged_and_deduplicated(self, tmp_path, monkeypatch):
        def fake_cli_invoke(**kwargs):
            return _resp(
                "done",
                source_files=[{"file_name": "b.py", "file_content": "x"}],
                chat_id=1,
            )

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, files, _ = run_tool_loop(
            initial_summary=_tool_request("glob", {"pattern": "*.py"}),
            updated_files=[
                {"file_name": "a.py", "file_content": "x"},
                {"file_name": "a.py", "file_content": "y"},
            ],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert [f["file_name"] for f in files] == ["a.py", "b.py"]
        assert "done" in summary

class TestStubRetry:
    def test_stub_round_nudges_the_model_then_uses_tools(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return _resp(_tool_request("glob", {"pattern": "*.py"}), chat_id=2)
            return _resp("all clear", chat_id=3)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, chat_id = run_tool_loop(
            initial_summary="Let me investigate the texture issue",
            updated_files=[],
            chat_id=1,
            prompt="fix the texture bug",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
            stub_retry=True,
        )
        assert len(calls) == 2
        assert "use the available tools now" in calls[0]["message"].lower()
        assert "fix the texture bug" in calls[0]["message"]
        assert "all clear" in summary
        assert chat_id == 3

    def test_stub_still_prose_when_model_refuses_to_call_tools(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("I cannot help with that.", chat_id=2)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, _ = run_tool_loop(
            initial_summary="Let me investigate",
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
            stub_retry=True,
        )
        assert summary == "I cannot help with that."
        assert len(calls) == 1

    def test_structured_tool_call_field_is_used(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "assistant_response": json.dumps(
                        {
                            "answer_summary": "",
                            "tool_call": [
                                {"name": "glob", "arguments": {"pattern": "*.py"}}
                            ],
                        }
                    ),
                    "chat_id": 5,
                }
            return _resp("done", chat_id=6)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, chat_id = run_tool_loop(
            initial_summary=_tool_request("glob", {"pattern": "*.txt"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert "glob" in calls[0]["message"]
        assert "*.txt" in calls[0]["message"]
        assert "*.py" in calls[1]["message"]
        assert "done" in summary
        assert chat_id == 6


class TestNarrationRendering:
    def test_narration_and_tools_merge_into_single_summary(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return _resp(
                    "Ok, let me check the layout. "
                    + _tool_request("glob", {"pattern": "*.py"}),
                    chat_id=1,
                )
            return _resp("Found 1 TODO, done.", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, _ = run_tool_loop(
            initial_summary=_tool_request("glob", {"pattern": "*.txt"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
            initial_raw_summary="Let me search for TODOs. "
            + _tool_request("glob", {"pattern": "*.txt"}),
        )
        # Everything is merged into ONE bubble text: round-1 narration, the
        # follow-up narration, the tool line, and the final answer.
        assert "Let me search for TODOs." in summary
        assert "Ok, let me check the layout." in summary
        assert '✱ Glob "*.txt"' in summary
        assert '✱ Glob "*.py"' in summary
        assert "Found 1 TODO, done." in summary
        assert looks_like_protocol_json(summary) is False
        assert summary.count("```") == 0

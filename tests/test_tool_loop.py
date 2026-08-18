"""Tests for the agentic tool loop (tool_loop.py)."""

import json
from types import SimpleNamespace

from aye.controller.tool_loop import MAX_TOOL_ROUNDS, run_tool_loop


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

        assert summary == "the notes say hello world"
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
        assert summary == "final answer"
        assert len(calls) == 1

    def test_round_budget_is_capped(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp(_tool_request("glob", {"pattern": "*.py"}), chat_id=1)

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
        assert len(calls) == MAX_TOOL_ROUNDS
        assert "reached the tool call limit" in calls[-1]["system_prompt"]
        assert "reached the tool call limit" not in calls[0]["system_prompt"]


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
        assert summary == "done"
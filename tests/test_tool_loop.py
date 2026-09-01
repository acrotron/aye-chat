"""Tests for the agentic tool loop (tool_loop.py)."""

import json
from types import SimpleNamespace

from aye.controller.tool_loop import run_tool_loop
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
    def TODO_test_read_then_prose(self, tmp_path, monkeypatch):
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
    def test_write_tool_is_not_offered(self, tmp_path, monkeypatch):
        """``write`` is withheld from the registry (see tools.WRITE_TOOL).

        A model that requests it anyway gets the unknown-tool error fed back,
        and no file is created. Re-enable via ``default_specs()`` when the
        sandboxed test flow lands.
        """
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
        assert not (tmp_path / "out.txt").exists()
        assert "unknown tool 'write'" in calls[0]["message"]

    def test_write_still_works_when_explicitly_registered(self, tmp_path, monkeypatch):
        """The implementation is intact; only the default registry withholds it."""
        from aye.model import snapshot
        from aye.model.tools import ALL_TOOLS, build_registry, execute_tool

        monkeypatch.chdir(tmp_path)
        snapshot.reset_backend()
        try:
            execute_tool(
                "write",
                {"path": "out.txt", "content": "data"},
                tmp_path,
                registry=build_registry(ALL_TOOLS),
            )
            assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "data"
        finally:
            snapshot.reset_backend()


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
            # A fresh request every round: real progress, so only the
            # budget can end the loop (the stall guard never fires).
            if len(calls) < 5:
                return _resp(
                    _tool_request("glob", {"pattern": f"*.{len(calls)}.py"}),
                    chat_id=1,
                )
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
        assert summary == "done with the task"
        assert looks_like_protocol_json(summary) is False

    def test_rounds_are_unlimited_by_default(self, tmp_path, monkeypatch):
        """No default cap: a long task simply runs until it is done.

        The old fixed budget (40) cut long agent tasks short; only an
        explicit ``max_rounds`` or the all-replay stall guard ends the loop
        early now.
        """
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) < 60:
                return _resp(
                    _tool_request("write", {"path": "f.tsx", "content": "x"}),
                    chat_id=1,
                )
            return _resp("created the files", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        summary, _, _ = run_tool_loop(
            initial_summary=_tool_request("write", {"path": "a.tsx", "content": "x"}),
            updated_files=[],
            chat_id=1,
            prompt="create many tsx files",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert summary == "created the files"
        assert len(calls) == 60


class TestStallGuard:
    """Unlimited rounds still must not spin on repeated calls forever."""

    def test_all_replay_rounds_force_a_prose_answer(self, tmp_path, monkeypatch):
        executed = []
        calls = []
        request = _tool_request("read", {"path": "a.py"})

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) >= 4:
                return _resp("here is the answer", chat_id=1)
            return _resp(request, chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        monkeypatch.setattr(
            "aye.controller.tool_loop.execute_tool",
            lambda name, args, root: executed.append(name) or "RESULT",
        )
        summary, _, _ = run_tool_loop(
            initial_summary=request,
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        # Round 0 runs the call once; rounds 1-3 replay it, then the guard
        # fires and forces the final prose round.
        assert executed == ["read"]
        assert len(calls) == 4
        assert "same tool calls" in calls[3]["message"]
        assert summary == "here is the answer"


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
        assert summary == "all clear"
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
        assert summary == "done"


class TestMixedNarrationAndCalls:
    """Narration travelling with a tool request must be acked, not lost."""

    def test_narration_is_acked_in_the_followup(self, tmp_path, monkeypatch):
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        run_tool_loop(
            initial_summary="Let me check the notes.\n"
            + _tool_request("read", {"path": "notes.txt"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert "Let me check the notes." in calls[0]["message"]
        assert "hello" in calls[0]["message"]

    def test_followup_carries_only_this_rounds_results(self, tmp_path, monkeypatch):
        (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")
        (tmp_path / "b.txt").write_text("BBB", encoding="utf-8")
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return _resp(_tool_request("read", {"path": "b.txt"}), chat_id=1)
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        run_tool_loop(
            initial_summary=_tool_request("read", {"path": "a.txt"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        second = calls[1]["message"]
        assert "AAA" not in second
        assert "BBB" in second

    def test_dropped_calls_are_reported_to_the_model(self, tmp_path, monkeypatch):
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        eight_reads = json.dumps(
            {
                "tool_calls": [
                    {"name": "read", "arguments": {"path": f"f{i}.txt"}}
                    for i in range(8)
                ]
            }
        )
        run_tool_loop(
            initial_summary=eight_reads,
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert "were dropped" in calls[0]["message"]
        assert "Re-request" in calls[0]["message"]

    def test_structured_tool_call_narration_is_acked(self, tmp_path, monkeypatch):
        """A structured field hides the prose it arrived with; ack it anyway.

        summary_with_tool_calls() swaps the prose answer for JSON, so without
        recovering the prose the model never learns its note was delivered
        and re-sends it on the next round.
        """
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "assistant_response": json.dumps(
                        {
                            "answer_summary": "Found the tests directory.",
                            "tool_call": [
                                {"name": "read", "arguments": {"path": "t.py"}}
                            ],
                        }
                    ),
                    "chat_id": 5,
                }
            return _resp("done", chat_id=6)

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
        # The follow-up after the structured round must ack the hidden prose.
        assert "Found the tests directory." in calls[1]["message"]
        assert "Do not repeat it" in calls[1]["message"]
        assert summary == "done"

    def test_initial_narration_is_acked_without_reprinting(self, tmp_path, monkeypatch):
        """Round-1 prose from a structured swap is acked, not echoed again."""
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return _resp("done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        run_tool_loop(
            initial_summary=_tool_request("glob", {"pattern": "*.py"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
            initial_narration="Checking the project layout.",
            initial_narration_shown=True,
        )
        assert "Checking the project layout." in calls[0]["message"]

    def test_identical_call_reruns_after_a_mutating_command(self, tmp_path, monkeypatch):
        """A shell command may change files, so cached results go stale."""
        executed = []
        read_req = _tool_request("read", {"path": "a.py"})
        cmd_req = _tool_request("cmd", {"command": "touch a.py"})
        rounds = [cmd_req, read_req]

        def fake_cli_invoke(**kwargs):
            return _resp(rounds.pop(0) if rounds else "done", chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        monkeypatch.setattr(
            "aye.controller.tool_loop.needs_confirmation", lambda name, registry: False
        )
        monkeypatch.setattr(
            "aye.controller.tool_loop.execute_tool",
            lambda name, args, root: executed.append((name, args)) or "ran",
        )
        run_tool_loop(
            initial_summary=read_req,
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert executed.count(("read", {"path": "a.py"})) == 2


class TestCrossRoundDedup:
    """parse_tool_calls() deduplicates within a round; the loop must also
    remember calls made in earlier rounds."""

    def _loop(self, tmp_path, monkeypatch, rounds, executed):
        """Drive the loop through *rounds* summaries, recording executions."""
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            nxt = rounds.pop(0) if rounds else "done"
            return _resp(nxt, chat_id=1)

        monkeypatch.setattr("aye.controller.tool_loop.cli_invoke", fake_cli_invoke)
        monkeypatch.setattr(
            "aye.controller.tool_loop.execute_tool",
            lambda name, args, root: (
                executed.append((name, args)) or f"RESULT[{name}]"
            ),
        )
        return calls

    def test_identical_call_runs_once_across_rounds(self, tmp_path, monkeypatch):
        executed = []
        request = _tool_request("read", {"path": "a.py"})
        self._loop(tmp_path, monkeypatch, [request], executed)

        run_tool_loop(
            initial_summary=request,
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert executed == [("read", {"path": "a.py"})]

    def test_repeat_is_told_to_the_model_with_the_old_output(
        self, tmp_path, monkeypatch
    ):
        executed = []
        request = _tool_request("read", {"path": "a.py"})
        calls = self._loop(tmp_path, monkeypatch, [request], executed)

        run_tool_loop(
            initial_summary=request,
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        second = calls[1]["message"]
        assert "already called this tool" in second
        assert "RESULT[read]" in second

    def test_different_arguments_still_execute(self, tmp_path, monkeypatch):
        executed = []
        self._loop(
            tmp_path, monkeypatch, [_tool_request("read", {"path": "b.py"})], executed
        )

        run_tool_loop(
            initial_summary=_tool_request("read", {"path": "a.py"}),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert executed == [
            ("read", {"path": "a.py"}),
            ("read", {"path": "b.py"}),
        ]

    def test_argument_order_does_not_defeat_dedup(self, tmp_path, monkeypatch):
        """Keys are normalized, so re-ordered arguments count as the same call."""
        executed = []
        reordered = json.dumps(
            {
                "tool_calls": [
                    {"name": "grep", "arguments": {"include": "*.py", "pattern": "x"}}
                ]
            }
        )
        self._loop(tmp_path, monkeypatch, [reordered], executed)

        run_tool_loop(
            initial_summary=json.dumps(
                {
                    "tool_calls": [
                        {
                            "name": "grep",
                            "arguments": {"pattern": "x", "include": "*.py"},
                        }
                    ]
                }
            ),
            updated_files=[],
            chat_id=1,
            prompt="q",
            conf=_conf(tmp_path),
            base_system_prompt="sys",
            source_files={},
            max_output_tokens=1000,
        )
        assert len(executed) == 1

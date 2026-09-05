"""Tests for the auto-test loop (test_loop.py)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import aye.controller.test_loop as tl


def _conf(tmp_path):
    return SimpleNamespace(root=tmp_path, selected_model="test-model", verbose=False)


def _api_resp(files, chat_id=7, summary=""):
    return {
        "assistant_response": json.dumps(
            {"answer_summary": summary, "source_files": files}
        ),
        "chat_id": chat_id,
    }


@pytest.fixture(autouse=True)
def _default_config_off(monkeypatch):
    """Deterministic config: env wins, config keys default to unset."""
    monkeypatch.delenv("AYE_AUTO_TEST", raising=False)
    monkeypatch.delenv("AYE_AUTO_TEST_MAX_ROUNDS", raising=False)
    monkeypatch.setattr(
        "aye.controller.test_loop.get_user_config",
        lambda key, default=None: default,
    )


CHANGED = [{"file_name": "calc.py", "file_content": "def add(a, b):\n    return a + b\n"}]
TEST_FILE = {
    "file_name": "tests/test_calc_auto.py",
    "file_content": "def test_add():\n    assert True\n",
}


class TestFlags:
    def test_disabled_by_default(self):
        assert tl.auto_test_enabled() is False

    def test_env_enables(self, monkeypatch):
        monkeypatch.setenv("AYE_AUTO_TEST", "on")
        assert tl.auto_test_enabled() is True

    def test_config_enables(self, monkeypatch):
        monkeypatch.setattr(
            "aye.controller.test_loop.get_user_config",
            lambda key, default=None: "on" if key == "auto_test" else default,
        )
        assert tl.auto_test_enabled() is True

    def test_max_rounds_env_and_clamp(self, monkeypatch):
        monkeypatch.setenv("AYE_AUTO_TEST_MAX_ROUNDS", "7")
        assert tl.auto_test_max_rounds() == 7
        monkeypatch.setenv("AYE_AUTO_TEST_MAX_ROUNDS", "99")
        assert tl.auto_test_max_rounds() == 10
        monkeypatch.setenv("AYE_AUTO_TEST_MAX_ROUNDS", "bogus")
        assert tl.auto_test_max_rounds() == 3


class TestRunLoop:
    def _wire(self, monkeypatch, model_responses, run_outcomes, applied):
        """Common patching: model rounds, pytest outcomes, write recorder."""
        calls = []

        def fake_cli_invoke(**kwargs):
            calls.append(kwargs)
            return model_responses.pop(0)

        monkeypatch.setattr(tl, "cli_invoke", fake_cli_invoke)
        outcomes = iter(run_outcomes)

        def fake_run(command, root):
            if "--version" in command:
                return 0, "pytest 8.x"
            return next(outcomes)

        monkeypatch.setattr(tl, "run_test_command", fake_run)
        monkeypatch.setattr(
            "aye.model.snapshot.apply_updates",
            lambda files, prompt, root: applied.append((files, prompt)),
        )
        return calls

    def test_generation_passes_first_run_and_prints_one_line(
        self, tmp_path, monkeypatch
    ):
        applied = []
        calls = self._wire(
            monkeypatch,
            [_api_resp([TEST_FILE], chat_id=7)],
            [(0, "1 passed")],
            applied,
        )
        console = MagicMock()

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="add numbers",
            conf=_conf(tmp_path),
            console=console,
            plugin_manager=object(),
            chat_id=1,
        )

        assert result is not None and result.ok
        assert result.rounds == 1
        assert result.test_files == ["tests/test_calc_auto.py"]
        # Minimal output: the "Files updated" line plus the green status.
        assert console.print.call_count == 2
        assert "passed" in console.print.call_args.args[0]
        first = console.print.call_args_list[0].args[0]
        first_text = getattr(first, "renderable", first)
        assert "Files updated" in str(first_text)
        # The generation prompt carries the change context and intent.
        assert "calc.py" in calls[0]["message"]
        assert "def add" in calls[0]["message"]
        assert "add numbers" in calls[0]["message"]
        # The generated tests were applied through the snapshot path.
        assert applied and applied[0][0] == [TEST_FILE]

    def test_failure_then_repair_then_pass(self, tmp_path, monkeypatch):
        # The code file exists on disk (the main flow wrote it), so the
        # repair round can snapshot its fresh content into the prompt.
        (tmp_path / "calc.py").write_text(CHANGED[0]["file_content"], encoding="utf-8")
        applied = []
        calls = self._wire(
            monkeypatch,
            [
                _api_resp([TEST_FILE], chat_id=7),
                _api_resp(
                    [{"file_name": "calc.py", "file_content": "fixed"}], chat_id=9
                ),
            ],
            [(1, "1 failed, FAILED test_add"), (0, "2 passed")],
            applied,
        )
        console = MagicMock()

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="add numbers",
            conf=_conf(tmp_path),
            console=console,
            plugin_manager=object(),
            chat_id=1,
        )

        assert result is not None and result.ok
        assert result.rounds == 2
        # Files-updated lines for both writes, then one final status line.
        assert console.print.call_count == 3
        # The repair round carries the failure output and current file state.
        repair_prompt = calls[1]["message"]
        assert "AUTO-TEST round 1" in repair_prompt
        assert "FAILED" in repair_prompt
        assert "def add" in repair_prompt  # fresh context snapshot
        # Both writes went through the snapshot path.
        assert len(applied) == 2
        assert applied[1][0][0]["file_name"] == "calc.py"

    def test_never_passes_returns_failure_after_budget(
        self, tmp_path, monkeypatch
    ):
        applied = []
        calls = self._wire(
            monkeypatch,
            [
                _api_resp([TEST_FILE]),
                _api_resp([TEST_FILE]),
                _api_resp([TEST_FILE]),
            ],
            [(1, "still failing")] * 3,
            applied,
        )
        console = MagicMock()

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="q",
            conf=_conf(tmp_path),
            console=console,
            plugin_manager=object(),
            chat_id=1,
            max_rounds=2,
        )

        assert result is not None and not result.ok
        # One initial run plus one per repair round.
        assert result.rounds == 3
        assert len(calls) == 3
        # Three Files-updated lines (generation + two repairs) + final status.
        assert console.print.call_count == 4
        assert "failed" in console.print.call_args.args[0]

    def test_skips_when_pytest_is_missing(self, tmp_path, monkeypatch):
        applied = []
        calls = self._wire(monkeypatch, [], [], applied)
        console = MagicMock()

        def no_pytest(command, root):
            assert "--version" in command
            return 1, "No module named pytest"

        monkeypatch.setattr(tl, "run_test_command", no_pytest)

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="q",
            conf=_conf(tmp_path),
            console=console,
            plugin_manager=object(),
            chat_id=1,
        )

        assert result is None
        assert calls == [] and applied == []
        assert console.print.call_count == 1
        assert "skipped" in console.print.call_args.args[0]

    def test_skips_when_model_returns_no_tests(self, tmp_path, monkeypatch):
        applied = []
        calls = self._wire(
            monkeypatch, [_api_resp([], chat_id=7)], [], applied
        )
        console = MagicMock()

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="q",
            conf=_conf(tmp_path),
            console=console,
            plugin_manager=object(),
            chat_id=1,
        )

        assert result is None
        assert applied == []
        assert console.print.call_count == 1

    def test_no_changed_files_is_a_noop(self, tmp_path):
        result = tl.run_auto_test_loop(
            changed_files=[],
            original_prompt="q",
            conf=_conf(tmp_path),
            console=MagicMock(),
            plugin_manager=object(),
            chat_id=1,
        )
        assert result is None

    def test_llm_error_degrades_to_none(self, tmp_path, monkeypatch):
        applied = []
        monkeypatch.setattr(
            tl, "cli_invoke", lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("api down")
            )
        )
        monkeypatch.setattr(
            tl, "run_test_command", lambda command, root: (0, "pytest 8.x")
        )
        monkeypatch.setattr(
            "aye.model.snapshot.apply_updates",
            lambda files, prompt, root: applied.append(files),
        )

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="q",
            conf=_conf(tmp_path),
            console=MagicMock(),
            plugin_manager=object(),
            chat_id=1,
        )
        assert result is None

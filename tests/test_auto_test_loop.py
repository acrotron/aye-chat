"""Tests for the auto-test loop (test_loop.py)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import aye.controller.test_loop as tl
from aye.model.models import LLMResponse, LLMSource


def _conf(tmp_path):
    return SimpleNamespace(
        root=tmp_path, selected_model="test-model", verbose=False
    )


def _resp(files, chat_id=7):
    return LLMResponse(
        summary="ok",
        updated_files=files,
        chat_id=chat_id,
        source=LLMSource.API,
        summary_already_printed=False,
    )


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
    def test_generation_passes_first_run(self, tmp_path, monkeypatch):
        llm_calls = []

        def fake_invoke(**kwargs):
            llm_calls.append(kwargs)
            assert "calc.py" in kwargs["prompt"]
            assert "def add" in kwargs["prompt"]
            assert "add numbers" in kwargs["prompt"]  # original request context
            return _resp([TEST_FILE])

        proc = []
        monkeypatch.setattr(tl, "invoke_llm", fake_invoke)
        monkeypatch.setattr(
            tl, "process_llm_response", lambda **kw: proc.append(kw) or 5
        )
        runs = []

        def fake_run(command, root):
            runs.append(command)
            if "--version" in command:
                return 0, "pytest 8.x"
            return 0, "1 passed"

        monkeypatch.setattr(tl, "run_test_command", fake_run)

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="add numbers",
            conf=_conf(tmp_path),
            console=MagicMock(),
            plugin_manager=object(),
            chat_id=1,
        )

        assert result is not None and result.ok
        assert result.rounds == 1
        assert result.test_files == ["tests/test_calc_auto.py"]
        assert len(llm_calls) == 1
        assert len(proc) == 1
        # The pytest run targets the generated test file from the project root.
        assert "--version" not in runs[-1]
        assert "pytest" in runs[-1]
        assert "tests/test_calc_auto.py" in runs[-1]

    def test_failure_then_repair_then_pass(self, tmp_path, monkeypatch):
        llm_calls = []

        def fake_invoke(**kwargs):
            llm_calls.append(kwargs)
            if len(llm_calls) == 1:
                return _resp([TEST_FILE], chat_id=7)
            # The repair round must carry the pytest failure output.
            assert "FAILED" in kwargs["prompt"]
            assert "AUTO-TEST round 1" in kwargs["prompt"]
            assert kwargs["chat_id"] == 7
            return _resp(
                [{"file_name": "calc.py", "file_content": "def add(a, b):\n    return a + b  # fixed\n"}],
                chat_id=9,
            )

        monkeypatch.setattr(tl, "invoke_llm", fake_invoke)
        monkeypatch.setattr(tl, "process_llm_response", lambda **kw: kw["response"].chat_id)
        outcomes = iter([(1, "1 failed, FAILED test_add"), (0, "2 passed")])

        def fake_run(command, root):
            if "--version" in command:
                return 0, "pytest 8.x"
            return next(outcomes)

        monkeypatch.setattr(tl, "run_test_command", fake_run)

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="add numbers",
            conf=_conf(tmp_path),
            console=MagicMock(),
            plugin_manager=object(),
            chat_id=1,
        )

        assert result is not None and result.ok
        assert result.rounds == 2
        assert len(llm_calls) == 2

    def test_never_passes_returns_failure_after_budget(self, tmp_path, monkeypatch):
        llm_calls = []

        def fake_invoke(**kwargs):
            llm_calls.append(kwargs)
            return _resp([TEST_FILE])

        monkeypatch.setattr(tl, "invoke_llm", fake_invoke)
        monkeypatch.setattr(tl, "process_llm_response", lambda **kw: 5)

        def fake_run(command, root):
            if "--version" in command:
                return 0, "pytest 8.x"
            return 1, "still failing"

        monkeypatch.setattr(tl, "run_test_command", fake_run)

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="q",
            conf=_conf(tmp_path),
            console=MagicMock(),
            plugin_manager=object(),
            chat_id=1,
            max_rounds=2,
        )

        assert result is not None and not result.ok
        # One initial run plus one per repair round.
        assert result.rounds == 3
        assert len(llm_calls) == 3

    def test_skips_when_pytest_is_missing(self, tmp_path, monkeypatch):
        invoked = []

        monkeypatch.setattr(
            tl, "invoke_llm", lambda **kw: invoked.append(kw) or _resp([])
        )
        monkeypatch.setattr(
            tl, "run_test_command", lambda command, root: (1, "No module named pytest")
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
        assert invoked == []

    def test_skips_when_model_returns_no_tests(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tl, "invoke_llm", lambda **kw: _resp([]))

        def fake_run(command, root):
            if "--version" in command:
                return 0, "pytest 8.x"
            raise AssertionError("pytest should not run")

        monkeypatch.setattr(tl, "run_test_command", fake_run)

        result = tl.run_auto_test_loop(
            changed_files=CHANGED,
            original_prompt="q",
            conf=_conf(tmp_path),
            console=MagicMock(),
            plugin_manager=object(),
            chat_id=1,
        )
        assert result is None

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
        def boom(**kwargs):
            raise RuntimeError("api down")

        monkeypatch.setattr(tl, "invoke_llm", boom)
        monkeypatch.setattr(
            tl, "run_test_command", lambda command, root: (0, "pytest 8.x")
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

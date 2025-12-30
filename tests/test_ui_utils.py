"""Tests for aye.presenter.ui_utils.

Goal: keep tests deterministic and fast (no real sleeping), while exercising
nearly all branches added in the ui_utils refactor.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rich.console import Console
from rich.spinner import Spinner

from aye.presenter.ui_utils import (
    DEFAULT_THINKING_MESSAGES,
    StoppableSpinner,
    thinking_spinner,
)


def _spinner_text(spinner: Spinner) -> str:
    """Return spinner.text as a plain string across Rich versions.

    In this codebase we sometimes assign `spinner.text = "..."` (a str).
    In other cases Rich may store it as a `rich.text.Text`.
    """
    t = spinner.text
    return t.plain if hasattr(t, "plain") else str(t)


class ImmediateThread:
    """A fake Thread that runs the target synchronously on start()."""

    def __init__(self, target=None, daemon=None, args=(), kwargs=None, **_):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False
        self.joined = False

    def start(self):
        self.started = True
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        self.joined = True


class NoWaitEvent:
    """A fake Event whose wait() returns immediately."""

    def wait(self, timeout=None):
        return True


@pytest.fixture
def mock_console():
    """Mock a Rich Console with a usable status() context manager."""
    console = MagicMock(spec=Console)
    cm = MagicMock()
    cm.__enter__ = MagicMock()
    cm.__exit__ = MagicMock(return_value=False)
    console.status.return_value = cm
    return console


class TestStoppableSpinner:
    def test_start_enters_status_and_creates_spinner(self, mock_console):
        s = StoppableSpinner(mock_console, messages=["Hello"], interval=0.01)
        s.start()

        mock_console.status.assert_called_once()
        spinner = mock_console.status.call_args[0][0]
        assert isinstance(spinner, Spinner)
        assert spinner.name == "dots"
        assert _spinner_text(spinner) == "Hello"

        mock_console.status.return_value.__enter__.assert_called_once()
        assert s.is_stopped() is False

    def test_start_is_idempotent(self, mock_console):
        s = StoppableSpinner(mock_console, messages=["One"], interval=0.01)
        s.start()
        s.start()

        # should only enter once
        mock_console.status.assert_called_once()
        mock_console.status.return_value.__enter__.assert_called_once()

    def test_stop_is_idempotent_and_exits_status(self, mock_console):
        s = StoppableSpinner(mock_console, messages=["One"], interval=0.01)
        s.start()
        s.stop()
        s.stop()

        mock_console.status.return_value.__exit__.assert_called_once_with(None, None, None)
        assert s.is_stopped() is True

    def test_does_not_start_thread_for_single_message(self, mock_console, monkeypatch):
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", ImmediateThread)
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Event", NoWaitEvent)

        s = StoppableSpinner(mock_console, messages=["Only"], interval=0.0)
        s.start()
        assert s._timer_thread is None

        s.stop()

    def test_rotates_messages_and_updates_spinner_text(self, mock_console, monkeypatch):
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", ImmediateThread)
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Event", NoWaitEvent)

        s = StoppableSpinner(mock_console, messages=["A", "B", "C"], interval=0.0)
        s.start()

        # ImmediateThread runs rotation synchronously; should reach last message.
        assert s._spinner is not None
        assert _spinner_text(s._spinner) == "C"
        assert s._state["index"] == 2

        # stop() should join + clear thread and exit status CM
        s.stop()
        assert s._timer_thread is None
        mock_console.status.return_value.__exit__.assert_called_once()

    def test_stop_joins_thread_when_present(self, mock_console, monkeypatch):
        # Use a thread instance we can inspect.
        created_threads = []

        def thread_factory(*args, **kwargs):
            t = ImmediateThread(*args, **kwargs)
            created_threads.append(t)
            return t

        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", thread_factory)
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Event", NoWaitEvent)

        s = StoppableSpinner(mock_console, messages=["A", "B"], interval=0.0)
        s.start()
        assert len(created_threads) == 1
        assert created_threads[0].started is True

        s.stop()
        assert created_threads[0].joined is True

    def test_empty_messages_list_falls_back_to_default_messages(self, mock_console):
        # In ui_utils: `messages or DEFAULT_THINKING_MESSAGES`.
        # So [] means "use defaults", not "no messages".
        s = StoppableSpinner(mock_console, messages=[], interval=0.01)
        s.start()
        spinner = mock_console.status.call_args[0][0]
        assert isinstance(spinner, Spinner)
        assert _spinner_text(spinner) == DEFAULT_THINKING_MESSAGES[0]
        s.stop()

    def test_default_messages_used_when_messages_none(self, mock_console):
        s = StoppableSpinner(mock_console, messages=None, interval=0.01)
        s.start()
        spinner = mock_console.status.call_args[0][0]
        assert _spinner_text(spinner) == DEFAULT_THINKING_MESSAGES[0]
        s.stop()


class TestThinkingSpinner:
    def test_default_text_used(self, mock_console):
        with thinking_spinner(mock_console):
            pass

        mock_console.status.assert_called_once()
        spinner = mock_console.status.call_args[0][0]
        assert isinstance(spinner, Spinner)
        assert _spinner_text(spinner) == "Thinking..."
        assert spinner.name == "dots"

    def test_messages_none_uses_text_param_as_single_message(self, mock_console, monkeypatch):
        # Ensure no thread is created for the implicit single-message list.
        created = {"count": 0}

        def thread_factory(*args, **kwargs):
            created["count"] += 1
            return ImmediateThread(*args, **kwargs)

        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", thread_factory)

        with thinking_spinner(mock_console, text="X", messages=None):
            pass

        assert created["count"] == 0
        spinner = mock_console.status.call_args[0][0]
        assert _spinner_text(spinner) == "X"

    def test_empty_messages_list_falls_back_to_text(self, mock_console):
        with thinking_spinner(mock_console, text="Fallback", messages=[]):
            pass

        spinner = mock_console.status.call_args[0][0]
        assert _spinner_text(spinner) == "Fallback"

    def test_multiple_messages_starts_thread_and_cycles_to_last(self, mock_console, monkeypatch):
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", ImmediateThread)
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Event", NoWaitEvent)

        with thinking_spinner(mock_console, messages=["First", "Second"], interval=0.0) as sp:
            # ImmediateThread runs synchronously; should already be on the last message.
            assert _spinner_text(sp) == "Second"

        # status CM should be exited
        mock_console.status.return_value.__exit__.assert_called_once()

    def test_thread_created_daemon_true(self, mock_console, monkeypatch):
        created_threads = []

        def thread_factory(*args, **kwargs):
            t = ImmediateThread(*args, **kwargs)
            created_threads.append(t)
            return t

        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", thread_factory)
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Event", NoWaitEvent)

        with thinking_spinner(mock_console, messages=["A", "B"], interval=0.0):
            pass

        assert len(created_threads) == 1
        assert created_threads[0].daemon is True

    def test_exception_propagates_and_cleanup_runs(self, mock_console, monkeypatch):
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", ImmediateThread)
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Event", NoWaitEvent)

        with pytest.raises(ValueError, match="boom"):
            with thinking_spinner(mock_console, messages=["A", "B"], interval=0.0):
                raise ValueError("boom")

        # Even on exception, __exit__ must be called.
        mock_console.status.return_value.__exit__.assert_called_once()

    def test_interval_zero_exercises_steps_calculation_branch(self, mock_console, monkeypatch):
        # interval=0.0 => steps=max(1,int(0/0.1)) => 1
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Thread", ImmediateThread)
        monkeypatch.setattr("aye.presenter.ui_utils.threading.Event", NoWaitEvent)

        with thinking_spinner(mock_console, messages=["A", "B", "C"], interval=0.0) as sp:
            assert _spinner_text(sp) == "C"

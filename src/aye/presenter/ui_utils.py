# ui_utils.py

import threading
from contextlib import contextmanager
from typing import List, Optional

from rich.console import Console
from rich.spinner import Spinner


# Default progressive messages for LLM operations
DEFAULT_THINKING_MESSAGES = [
    "Building prompt...",
    "Sending to LLM...",
    "Waiting for response...",
    "Still waiting...",
    "This is taking longer than usual...",
]


class StoppableSpinner:
    """A spinner that can be started and stopped programmatically.

    This implementation uses `Console.status(...)` (instead of `rich.live.Live`)
    to be compatible across Rich versions and to work cleanly with mocked
    consoles in tests.

    Usage:
        spinner = StoppableSpinner(console)
        spinner.start()
        # ... do work ...
        spinner.stop()
    """

    def __init__(
        self,
        console: Console,
        messages: Optional[List[str]] = None,
        interval: float = 10.0,
    ):
        self._console = console
        self._messages = messages or DEFAULT_THINKING_MESSAGES
        self._interval = interval

        self._spinner: Optional[Spinner] = None

        # Context manager returned by console.status(...)
        self._status_cm = None

        self._timer_thread: Optional[threading.Thread] = None
        self._state = {"index": 0, "stop": False}

        self._started = False
        self._stopped = False

    def _update_message(self) -> None:
        """Background function to cycle through messages."""
        # Wait in small increments so stop() can join quickly.
        tick = 0.1
        steps = max(1, int(max(0.0, self._interval) / tick))

        while not self._state["stop"]:
            for _ in range(steps):
                if self._state["stop"]:
                    return
                threading.Event().wait(tick)

            if self._state["stop"]:
                return

            # Move to next message if available
            if self._state["index"] + 1 < len(self._messages):
                self._state["index"] += 1
                if self._spinner is not None:
                    self._spinner.text = self._messages[self._state["index"]]
            else:
                # No more messages; stop updating.
                return

    def start(self) -> None:
        """Start the spinner. Safe to call multiple times."""
        if self._started:
            return

        self._started = True
        self._stopped = False
        self._state = {"index": 0, "stop": False}

        # Create spinner with initial message
        initial_text = self._messages[0] if self._messages else ""
        self._spinner = Spinner("dots", text=initial_text)

        # Enter the status context manager programmatically
        self._status_cm = self._console.status(self._spinner)
        self._status_cm.__enter__()

        # Start message rotation thread if we have multiple messages
        if len(self._messages) > 1:
            self._timer_thread = threading.Thread(target=self._update_message, daemon=True)
            self._timer_thread.start()

    def stop(self) -> None:
        """Stop the spinner. Safe to call multiple times."""
        if self._stopped:
            return
        self._stopped = True

        self._state["stop"] = True

        if self._timer_thread is not None:
            self._timer_thread.join(timeout=0.5)
            self._timer_thread = None

        if self._status_cm is not None:
            # Exit cleanly; returning False indicates exceptions (if any) should propagate.
            self._status_cm.__exit__(None, None, None)
            self._status_cm = None

    def is_stopped(self) -> bool:
        return self._stopped


@contextmanager
def thinking_spinner(
    console: Console,
    text: str = "Thinking...",
    messages: Optional[List[str]] = None,
    interval: float = 15.0,
):
    """Context manager for consistent spinner behavior across LLM invocations.

    This function intentionally uses `console.status(Spinner(...))` so it is
    compatible with mocked `Console` objects in unit tests.

    Args:
        console: Rich console instance
        text: Initial text to display with the spinner (used if messages is None)
        messages: Optional list of messages to cycle through at intervals
        interval: Seconds between message changes
    """
    if messages is None:
        messages = [text]

    spinner = Spinner("dots", text=(messages[0] if messages else text))
    state = {"index": 0, "stop": False}
    timer_thread: Optional[threading.Thread] = None

    def update_message_loop() -> None:
        tick = 0.1
        steps = max(1, int(max(0.0, interval) / tick))

        while not state["stop"]:
            for _ in range(steps):
                if state["stop"]:
                    return
                threading.Event().wait(tick)

            if state["stop"]:
                return

            if state["index"] + 1 < len(messages):
                state["index"] += 1
                spinner.text = messages[state["index"]]
            else:
                return

    try:
        with console.status(spinner):
            if len(messages) > 1:
                timer_thread = threading.Thread(target=update_message_loop, daemon=True)
                timer_thread.start()
            yield spinner
    finally:
        state["stop"] = True
        if timer_thread is not None:
            timer_thread.join(timeout=0.5)

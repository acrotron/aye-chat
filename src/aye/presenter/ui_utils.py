# ui_utils.py
import threading
from contextlib import contextmanager
from typing import List, Optional
from rich.console import Console
from rich.spinner import Spinner
from rich.live import Live


# Default progressive messages for LLM operations
DEFAULT_THINKING_MESSAGES = [
    "Building prompt...",
    "Sending to LLM...",
    "Waiting for response...",
    "Still waiting...",
    "This is taking longer than usual..."
]


class StoppableSpinner:
    """
    A spinner that can be started and stopped programmatically.
    
    Unlike the context manager `thinking_spinner`, this class allows
    external control over when the spinner stops - useful for stopping
    the spinner when streaming content starts arriving.
    
    Usage:
        spinner = StoppableSpinner(console)
        spinner.start()
        # ... do work ...
        spinner.stop()  # Call when done or when streaming starts
    """
    
    def __init__(
        self,
        console: Console,
        messages: Optional[List[str]] = None,
        interval: float = 10.0
    ):
        """
        Initialize the stoppable spinner.
        
        Args:
            console: Rich console instance
            messages: List of messages to cycle through. Defaults to
                     DEFAULT_THINKING_MESSAGES.
            interval: Seconds between message changes (default: 3.0)
        """
        self._console = console
        self._messages = messages or DEFAULT_THINKING_MESSAGES
        self._interval = interval
        self._live: Optional[Live] = None
        self._spinner: Optional[Spinner] = None
        self._timer_thread: Optional[threading.Thread] = None
        self._state = {"index": 0, "stop": False}
        self._started = False
        self._stopped = False
    
    def _update_message(self):
        """Background function to cycle through messages."""
        while not self._state["stop"]:
            # Wait for the interval or until stopped (check every 0.1s)
            for _ in range(int(self._interval * 10)):
                if self._state["stop"]:
                    return
                threading.Event().wait(0.1)
            
            if self._state["stop"]:
                return
            
            # Move to next message if available
            if self._state["index"] + 1 < len(self._messages):
                self._state["index"] += 1
                if self._spinner:
                    self._spinner.text = self._messages[self._state["index"]]
    
    def start(self):
        """Start the spinner. Safe to call multiple times (no-op if already started)."""
        if self._started:
            return
        
        self._started = True
        self._state = {"index": 0, "stop": False}
        
        # Create spinner with initial message
        self._spinner = Spinner("dots", text=self._messages[0])
        self._live = Live(
            self._spinner,
            console=self._console,
            refresh_per_second=10,
            transient=True
        )
        self._live.start()
        
        # Start message rotation thread if we have multiple messages
        if len(self._messages) > 1:
            self._timer_thread = threading.Thread(target=self._update_message, daemon=True)
            self._timer_thread.start()
    
    def stop(self):
        """Stop the spinner. Safe to call multiple times."""
        if self._stopped:
            return
        self._stopped = True
        
        # Signal the timer thread to stop
        self._state["stop"] = True
        
        # Stop the live display
        if self._live:
            self._live.stop()
            self._live = None
        
        # Wait for timer thread to finish
        if self._timer_thread:
            self._timer_thread.join(timeout=0.5)
            self._timer_thread = None
    
    def is_stopped(self) -> bool:
        """Check if the spinner has been stopped."""
        return self._stopped


@contextmanager
def thinking_spinner(
    console: Console, 
    text: str = "Thinking...",
    messages: Optional[List[str]] = None,
    interval: float = 15.0
):
    """
    Context manager for consistent spinner behavior across all LLM invocations.
    
    Args:
        console: Rich console instance
        text: Initial text to display with the spinner (used if messages is None)
        messages: Optional list of messages to cycle through at intervals
        interval: Seconds between message changes (default: 15)
        
    Usage:
        # Simple usage (backward compatible):
        with thinking_spinner(console):
            result = api_call()
        
        # With custom messages:
        with thinking_spinner(console, messages=[
            "Collecting files...",
            "Building prompt...",
            "Sending to LLM...",
            "Waiting for response..."
        ]):
            result = api_call()
    """
    # Default progressive messages if none provided
    if messages is None:
        messages = [text]  # Just use the single text message
    
    spinner = StoppableSpinner(console, messages=messages, interval=interval)
    spinner.start()
    
    try:
        yield spinner
    finally:
        spinner.stop()

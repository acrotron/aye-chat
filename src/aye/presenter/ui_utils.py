# ui_utils.py
import threading
from contextlib import contextmanager
from typing import List, Optional
from rich.console import Console
from rich.spinner import Spinner


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
        interval: Seconds between message changes (default: 20)
        
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
    
    # Shared state for the message index
    state = {"index": 0, "stop": False}
    
    def update_message():
        """Background function to cycle through messages."""
        while not state["stop"]:
            # Wait for the interval or until stopped
            for _ in range(int(interval * 10)):  # Check every 0.1s for quick exit
                if state["stop"]:
                    return
                threading.Event().wait(0.1)
            
            if state["stop"]:
                return
            
            # Move to next message
            if state["index"] + 1 < len(messages):
                state["index"] = (state["index"] + 1) % len(messages)
                current_message = messages[state["index"]]
                spinner.text = f"{current_message}"
            else:
                state["stop"] = True
    
    # Create spinner with initial message
    initial_message = messages[0]
    spinner = Spinner("dots", text=f"{initial_message}")
    
    # Start message rotation thread only if we have multiple messages
    timer_thread = None
    if len(messages) > 1:
        timer_thread = threading.Thread(target=update_message, daemon=True)
        timer_thread.start()
    
    try:
        with console.status(spinner):
            yield
    finally:
        # Stop the timer thread
        state["stop"] = True
        if timer_thread:
            timer_thread.join(timeout=0.5)  # Give it a moment to exit cleanly


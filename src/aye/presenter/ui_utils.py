# ui_utils.py
from contextlib import contextmanager
from rich.console import Console
from rich.spinner import Spinner


@contextmanager
def thinking_spinner(console: Console, text: str = "Thinking..."):
    """
    Context manager for consistent spinner behavior across all LLM invocations.
    
    Args:
        console: Rich console instance
        text: Text to display with the spinner
        
    Usage:
        with thinking_spinner(console):
            # Do LLM invocation
            result = api_call()
    """
    spinner = Spinner("dots", text=f"[yellow]{text}[/]")
    with console.status(spinner):
        yield

"""Streaming UI components for displaying LLM responses progressively.

This module provides the StreamingResponseDisplay class which handles
real-time display of LLM responses in a styled Rich panel with
word-by-word animation.
"""

import os
import time
from typing import Optional, Callable

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# Theme matching repl_ui.py for consistent styling
_STREAMING_THEME = Theme({
    "ui.response_symbol.name": "bold cornflower_blue",
    "ui.response_symbol.waves": "steel_blue",
    "ui.response_symbol.pulse": "bold pale_turquoise1",
    "ui.border": "dim slate_blue3",
})


def _create_response_panel(content: str, use_markdown: bool = True) -> Panel:
    """
    Create a styled response panel matching the final response display.
    
    This mirrors the layout in presenter/repl_ui.py print_assistant_response()
    so streaming output looks identical to final output.
    
    Args:
        content: The content to display
        use_markdown: If True, render as Markdown; if False, render as plain Text
        
    Returns:
        A styled Rich Panel
    """
    # Decorative "sonar pulse" marker
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]●[/] [ui.response_symbol.waves]))[/]"
    
    # A 2-column grid: marker + content
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()
    
    # Use Markdown for proper formatting, or Text for mid-animation
    if use_markdown and content:
        rendered_content = Markdown(content)
    else:
        rendered_content = Text(content) if content else Text("")
    
    grid.add_row(pulse, rendered_content)
    
    # Wrap in a rounded panel
    return Panel(
        grid,
        border_style="ui.border",
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


class StreamingResponseDisplay:
    """
    Manages a live-updating Rich panel for streaming LLM responses
    with word-by-word animation.
    
    Usage:
        display = StreamingResponseDisplay()
        display.start()
        display.update("First chunk...")
        display.update("First chunk... more content")
        display.stop()
    
    Or as a context manager:
        with StreamingResponseDisplay() as display:
            display.update("content")
    """
    
    def __init__(
        self, 
        console: Optional[Console] = None, 
        word_delay: Optional[float] = 0.12,
        on_first_content: Optional[Callable[[], None]] = None
    ):
        """
        Initialize the streaming display.
        
        Args:
            console: Optional Rich Console. If not provided, creates one with
                     the streaming theme.
            word_delay: Delay in seconds between words. Defaults to 0.12 (120ms).
                       Can be overridden via AYE_STREAM_WORD_DELAY env var.
            on_first_content: Optional callback invoked when the first content
                             is received (before starting the display). Useful
                             for stopping a spinner.
        """
        self._console = console or Console(theme=_STREAMING_THEME)
        self._live: Optional[Live] = None
        self._current_content: str = ""  # Full content received so far
        self._animated_content: str = ""  # Content that has been animated word-by-word
        self._started: bool = False
        self._first_content_received: bool = False
        self._on_first_content = on_first_content
        
        # Word delay configuration
        if word_delay is not None:
            self._word_delay = word_delay
        else:
            try:
                self._word_delay = float(os.environ.get("AYE_STREAM_WORD_DELAY", "0.12") or "0.12")
            except ValueError:
                self._word_delay = 0.12
    
    def start(self) -> None:
        """
        Start the live display.
        
        Call this before the first update. Adds spacing before the panel.
        """
        if self._started:
            return
        
        self._console.print()  # Add spacing before panel
        self._live = Live(
            _create_response_panel("", use_markdown=False),
            console=self._console,
            refresh_per_second=30,  # Higher refresh rate for smooth animation
            transient=False
        )
        self._live.start()
        self._started = True
    
    def _animate_words(self, new_text: str) -> None:
        """
        Animate new text word by word.
        
        Args:
            new_text: The new text to animate (delta from what's already shown)
        """
        if not new_text or not self._live:
            return
        
        # Split into tokens (words and whitespace)
        # We want to preserve whitespace structure
        i = 0
        n = len(new_text)
        
        while i < n:
            char = new_text[i]
            
            if char in '\n\r':
                # Newline - add it immediately
                self._animated_content += char
                i += 1
                # Update display with markdown rendering for newlines
                self._live.update(_create_response_panel(self._animated_content, use_markdown=True))
            elif char in ' \t':
                # Whitespace - collect all consecutive whitespace
                ws_start = i
                while i < n and new_text[i] in ' \t':
                    i += 1
                self._animated_content += new_text[ws_start:i]
                # Update display (no delay for whitespace)
                self._live.update(_create_response_panel(self._animated_content, use_markdown=False))
            else:
                # Word - collect consecutive non-whitespace
                word_start = i
                while i < n and new_text[i] not in ' \t\n\r':
                    i += 1
                word = new_text[word_start:i]
                self._animated_content += word
                
                # Update display with the new word
                # Use markdown=False during animation for speed, True on newlines
                self._live.update(_create_response_panel(self._animated_content, use_markdown=False))
                
                # Delay after each word
                if self._word_delay > 0:
                    time.sleep(self._word_delay)
    
    def update(self, content: str) -> None:
        """
        Update the displayed content with word-by-word animation.
        
        Args:
            content: The full content to display (not a delta).
                     This replaces the previous content.
        """
        if content == self._current_content:
            return
        
        # Fire the on_first_content callback before starting the display
        if not self._first_content_received:
            self._first_content_received = True
            if self._on_first_content:
                self._on_first_content()
        
        # Auto-start if not started
        if not self._started:
            self.start()
        
        # Calculate the new text that needs to be animated
        if content.startswith(self._current_content):
            # Content was appended - animate only the new part
            new_text = content[len(self._current_content):]
        else:
            # Content changed completely - reset and animate all
            self._animated_content = ""
            new_text = content
        
        self._current_content = content
        
        # Animate the new words
        if new_text and self._live:
            self._animate_words(new_text)
    
    def stop(self) -> None:
        """
        Stop the live display.
        
        Call this when streaming is complete. Does a final render with
        full Markdown formatting and adds spacing after the panel.
        """
        if self._live:
            # Final update with full markdown rendering
            if self._animated_content:
                self._live.update(_create_response_panel(self._animated_content, use_markdown=True))
            self._live.stop()
            self._console.print()  # Add spacing after panel
            self._live = None
        self._started = False
    
    def is_active(self) -> bool:
        """
        Check if the display is currently active.
        
        Returns:
            True if started and not stopped.
        """
        return self._started and self._live is not None
    
    def has_received_content(self) -> bool:
        """
        Check if any content has been received.
        
        Returns:
            True if update() has been called with non-empty content.
        """
        return self._first_content_received
    
    @property
    def content(self) -> str:
        """
        Get the current displayed content.
        
        Returns:
            The current content string.
        """
        return self._current_content
    
    def __enter__(self) -> 'StreamingResponseDisplay':
        """Context manager entry - starts the display."""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - stops the display."""
        self.stop()


def create_streaming_callback(display: StreamingResponseDisplay):
    """
    Create a callback function for use with cli_invoke.
    
    Args:
        display: The StreamingResponseDisplay instance to update.
    
    Returns:
        A callable that accepts content strings and updates the display.
    """
    def callback(content: str) -> None:
        display.update(content)
    
    return callback

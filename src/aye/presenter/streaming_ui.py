"""Streaming UI components for displaying LLM responses progressively.

This module provides the StreamingResponseDisplay class which handles
real-time display of LLM responses with a fixed viewport.

Key features:
- Fixed viewport height (tail-view during streaming)
- Live Markdown rendering
- Full content display after streaming completes
- No borders for easy copy/paste

Configuration (environment variables):
- AYE_STREAM_VIEWPORT_HEIGHT: Number of lines for viewport (default: 15)
- AYE_STREAM_FINAL_MARKDOWN: "on" (default) or "off" - render final as markdown
"""
from aye.presenter.repl_ui import deep_ocean_theme

import os
import time
import threading
from typing import Optional, Callable, List

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich import box


_STREAMING_THEME = deep_ocean_theme

# Marker width for alignment
_PULSE_MARKER_WIDTH = 9  # "(( ● )) "

# Default viewport height in lines
_DEFAULT_VIEWPORT_HEIGHT = 15


def _get_env_int(env_var: str, default: int) -> int:
    """Get an integer value from environment variable."""
    try:
        return int(os.environ.get(env_var, str(default)))
    except ValueError:
        return default


def _get_env_bool(env_var: str, default: bool) -> bool:
    """Get a boolean value from environment variable."""
    val = os.environ.get(env_var, "on" if default else "off").lower()
    return val in ("on", "true", "1", "yes")


def _create_pulse_marker() -> Text:
    """Create the styled pulse marker text."""
    marker = Text()
    marker.append("((", style="steel_blue")
    marker.append(" ● ", style="bold pale_turquoise1")
    marker.append("))", style="steel_blue")
    marker.append(" ")
    return marker


def _get_last_n_lines(text: str, n: int) -> str:
    """Get the last n lines of text."""
    lines = text.split('\n')
    if len(lines) <= n:
        return text
    return '\n'.join(lines[-n:])


def _build_viewport_display(content: str, viewport_height: int) -> Table:
    """Build the viewport display with pulse marker and content.
    
    Shows the last `viewport_height` lines of content (tail view).
    """
    grid = Table.grid(padding=0, expand=True)
    grid.add_column(width=_PULSE_MARKER_WIDTH, no_wrap=True)
    grid.add_column(ratio=1)
    
    # Get last N lines for viewport
    visible_content = _get_last_n_lines(content, viewport_height)
    
    if visible_content.strip():
        # Render as markdown for live preview
        md_content = Markdown(visible_content)
    else:
        md_content = Text("")
    
    grid.add_row(_create_pulse_marker(), md_content)
    return grid


def _build_final_display(content: str) -> Table:
    """Build the final markdown display with pulse marker."""
    grid = Table.grid(padding=0, expand=True)
    grid.add_column(width=_PULSE_MARKER_WIDTH, no_wrap=True)
    grid.add_column(ratio=1)
    
    if content.strip():
        md_content = Markdown(content)
    else:
        md_content = Text("")
    
    grid.add_row(_create_pulse_marker(), md_content)
    return grid


class StreamingResponseDisplay:
    """Streams LLM responses in a fixed viewport.
    
    This implementation provides:
    - Fixed viewport: Shows last N lines during streaming (like tail -f)
    - Live markdown: Content is rendered as markdown during streaming
    - Full output: After streaming, displays complete formatted content
    - No borders: Clean output for easy copy/paste
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        word_delay: Optional[float] = None,  # Kept for API compatibility
        stall_threshold: Optional[float] = None,  # Kept for API compatibility
        on_first_content: Optional[Callable[[], None]] = None,
    ):
        self._console = console or Console(theme=_STREAMING_THEME, force_terminal=True)
        
        self._current_content: str = ""
        self._rendered_content: str = ""  # Last rendered content

        self._started: bool = False
        self._stopped: bool = False
        self._first_content_received: bool = False
        self._on_first_content = on_first_content
        
        # Viewport configuration
        self._viewport_height = _get_env_int("AYE_STREAM_VIEWPORT_HEIGHT", _DEFAULT_VIEWPORT_HEIGHT)
        self._final_markdown = _get_env_bool("AYE_STREAM_FINAL_MARKDOWN", True)
        
        # Throttling
        self._min_render_interval = 0.1  # 100ms between renders
        self._last_render_time: float = 0.0
        
        # Rich Live display
        self._live: Optional[Live] = None

        self._lock = threading.RLock()

    def _should_render(self, content: str) -> bool:
        """Check if we should render based on throttling."""
        now = time.time()
        if (now - self._last_render_time) < self._min_render_interval:
            return False
        if content == self._rendered_content:
            return False
        return True

    def _render_viewport(self, content: str, force: bool = False) -> None:
        """Render content in the viewport."""
        with self._lock:
            if not force and not self._should_render(content):
                return
            
            self._last_render_time = time.time()
            self._rendered_content = content
            
            if self._live is not None:
                display = _build_viewport_display(content, self._viewport_height)
                self._live.update(display)

    def start(self) -> None:
        """Start the streaming display with fixed viewport."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stopped = False
            self._last_render_time = time.time()
            
            # Create Live display with fixed viewport behavior
            # - refresh_per_second: controls update rate
            # - vertical_overflow="visible": don't clip content
            # - transient=True: clear viewport when done (we'll print final separately)
            self._live = Live(
                _build_viewport_display("", self._viewport_height),
                console=self._console,
                refresh_per_second=10,
                vertical_overflow="visible",
                transient=True,  # Clear viewport when we stop
            )
            self._live.start()

    def update(self, content: str, is_final: bool = False) -> None:
        """Update with new content.

        Args:
            content: The full content so far (not a delta).
            is_final: If True, this is the final update.
        """
        with self._lock:
            if self._stopped:
                return
            
            if not is_final and content == self._current_content:
                return

            if not self._first_content_received:
                self._first_content_received = True
                if self._on_first_content:
                    self._on_first_content()

            if not self._started:
                self.start()

            self._current_content = content

            if is_final:
                # Force final render in viewport
                self._render_viewport(content, force=True)
                return

            # Render in viewport (may be throttled)
            self._render_viewport(content)

    def stop(self) -> None:
        """Stop streaming and display full content."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            content = self._current_content
            
            # Stop the Live display (clears viewport due to transient=True)
            if self._live is not None:
                self._live.stop()
                self._live = None

        # Print the full final content
        if content:
            self._console.print()  # Spacing
            if self._final_markdown:
                display = _build_final_display(content)
                self._console.print(display)
            else:
                # Print raw content with marker
                self._console.print(_create_pulse_marker(), end="")
                self._console.print(content)
            self._console.print()  # Spacing after

        with self._lock:
            self._started = False

    def is_active(self) -> bool:
        """Check if streaming is active."""
        return self._started and not self._stopped

    def has_received_content(self) -> bool:
        """Check if any content has been received."""
        return self._first_content_received

    @property
    def content(self) -> str:
        """Get the current content."""
        return self._current_content

    def __enter__(self) -> "StreamingResponseDisplay":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def create_streaming_callback(display: StreamingResponseDisplay):
    """Create a callback function for use with cli_invoke."""
    def callback(content: str, is_final: bool = False) -> None:
        display.update(content, is_final=is_final)
    return callback

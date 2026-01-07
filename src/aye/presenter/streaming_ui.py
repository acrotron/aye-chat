"""Streaming UI components for displaying LLM responses progressively.

This module provides the StreamingResponseDisplay class which handles
real-time display of LLM responses with scrolling support.

Uses Textual for a full-screen scrollable view during streaming.
Falls back to Rich Live display if Textual is not available.
"""

import os
import sys
import threading
import time
from typing import Callable, Optional

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Check if Textual is available
try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import ScrollableContainer
    from textual.widgets import Footer, Header, Static
    from textual.reactive import reactive
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False
    App = None
    ComposeResult = None


# Theme matching repl_ui.py for consistent styling
_STREAMING_THEME = Theme({
    "ui.response_symbol.name": "bold cornflower_blue",
    "ui.response_symbol.waves": "steel_blue",
    "ui.response_symbol.pulse": "bold pale_turquoise1",
    "ui.border": "dim slate_blue3",
    "ui.border.stall": "dim yellow",
    "ui.stall_spinner": "dim yellow",
})


def _get_env_float(env_var: str, default: float) -> float:
    """Get a float value from environment variable with fallback default."""
    try:
        return float(os.environ.get(env_var, str(default)) or str(default))
    except ValueError:
        return default


def _get_env_bool(env_var: str, default: bool) -> bool:
    """Get a boolean value from environment variable with fallback default."""
    val = os.environ.get(env_var, "").lower()
    if val in ("1", "true", "on", "yes"):
        return True
    if val in ("0", "false", "off", "no"):
        return False
    return default


def _create_response_panel(
    content: str,
    use_markdown: bool = True,
    show_stall_indicator: bool = False,
) -> Panel:
    """Create a styled response panel matching the final response display.
    
    Args:
        content: The text content to display.
        use_markdown: Whether to render content as Markdown.
        show_stall_indicator: Whether to show a stall indicator in the border.
    """
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]●[/] [ui.response_symbol.waves]))[/]"

    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    if use_markdown and content:
        rendered_content = Markdown(content)
    else:
        rendered_content = Text(content) if content else Text("")

    grid.add_row(pulse, rendered_content)

    border_style = "ui.border.stall" if show_stall_indicator else "ui.border"
    subtitle = "[dim yellow]waiting for more...[/]" if show_stall_indicator else None

    return Panel(
        grid,
        border_style=border_style,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
        subtitle=subtitle,
        subtitle_align="right",
    )


# =============================================================================
# Textual-based Streaming App
# =============================================================================

if TEXTUAL_AVAILABLE:
    class StreamingContentWidget(Static):
        """A widget that displays streaming Markdown content."""
        
        content_text = reactive("", recompose=True)
        
        def compose(self) -> ComposeResult:
            """Compose the widget content."""
            if self.content_text:
                yield Static(Markdown(self.content_text), id="markdown-content")
            else:
                yield Static(Text("Waiting for response...", style="dim"), id="markdown-content")

    class StreamingApp(App):
        """A Textual app for displaying streaming LLM responses with scrolling."""
        
        CSS = """
        Screen {
            background: $surface;
        }
        
        #scroll-container {
            height: 100%;
            width: 100%;
            padding: 1 2;
            background: $surface;
        }
        
        #content-widget {
            width: 100%;
            padding: 0 1;
        }
        
        #markdown-content {
            width: 100%;
        }
        
        Header {
            background: $primary-darken-2;
        }
        
        Footer {
            background: $primary-darken-3;
        }
        
        .status-streaming {
            color: $success;
        }
        
        .status-complete {
            color: $primary;
        }
        """
        
        BINDINGS = [
            Binding("q", "quit_app", "Quit", show=True),
            Binding("escape", "quit_app", "Quit", show=False),
            Binding("up", "scroll_up", "Scroll Up", show=False),
            Binding("down", "scroll_down", "Scroll Down", show=False),
            Binding("pageup", "page_up", "Page Up", show=True),
            Binding("pagedown", "page_down", "Page Down", show=True),
            Binding("home", "scroll_home", "Top", show=False),
            Binding("end", "scroll_end", "Bottom", show=False),
        ]
        
        def __init__(self, title: str = "Aye Chat Response"):
            super().__init__()
            self._title = title
            self._content = ""
            self._is_streaming = True
            self._auto_scroll = True
            self._lock = threading.Lock()
            self._update_pending = False
        
        def compose(self) -> ComposeResult:
            """Compose the app layout."""
            yield Header(show_clock=False)
            with ScrollableContainer(id="scroll-container"):
                yield StreamingContentWidget(id="content-widget")
            yield Footer()
        
        def on_mount(self) -> None:
            """Called when the app is mounted."""
            self.title = self._title
            self.sub_title = "Streaming... (scroll with ↑↓, q to exit)"
        
        def update_content(self, content: str, is_final: bool = False) -> None:
            """Update the displayed content (thread-safe).
            
            Args:
                content: The full content to display.
                is_final: Whether this is the final update.
            """
            with self._lock:
                self._content = content
                self._is_streaming = not is_final
                self._update_pending = True
            
            # Schedule UI update on the main thread
            self.call_from_thread(self._apply_update)
        
        def _apply_update(self) -> None:
            """Apply pending content update (must be called on main thread)."""
            with self._lock:
                if not self._update_pending:
                    return
                content = self._content
                is_streaming = self._is_streaming
                self._update_pending = False
            
            # Update the content widget
            try:
                widget = self.query_one("#content-widget", StreamingContentWidget)
                widget.content_text = content
            except Exception:
                pass
            
            # Update subtitle
            if is_streaming:
                self.sub_title = "Streaming... (scroll with ↑↓, q to exit)"
            else:
                self.sub_title = "Complete (q to exit)"
            
            # Auto-scroll to bottom if enabled
            if self._auto_scroll:
                self._scroll_to_end()
        
        def _scroll_to_end(self) -> None:
            """Scroll to the end of the content."""
            try:
                container = self.query_one("#scroll-container", ScrollableContainer)
                container.scroll_end(animate=False)
            except Exception:
                pass
        
        def action_quit_app(self) -> None:
            """Quit the app."""
            self.exit()
        
        def action_scroll_up(self) -> None:
            """Scroll up and disable auto-scroll."""
            self._auto_scroll = False
            try:
                container = self.query_one("#scroll-container", ScrollableContainer)
                container.scroll_up()
            except Exception:
                pass
        
        def action_scroll_down(self) -> None:
            """Scroll down."""
            try:
                container = self.query_one("#scroll-container", ScrollableContainer)
                container.scroll_down()
                # Re-enable auto-scroll if at bottom
                if container.scroll_y >= container.max_scroll_y:
                    self._auto_scroll = True
            except Exception:
                pass
        
        def action_page_up(self) -> None:
            """Page up and disable auto-scroll."""
            self._auto_scroll = False
            try:
                container = self.query_one("#scroll-container", ScrollableContainer)
                container.scroll_page_up()
            except Exception:
                pass
        
        def action_page_down(self) -> None:
            """Page down."""
            try:
                container = self.query_one("#scroll-container", ScrollableContainer)
                container.scroll_page_down()
                # Re-enable auto-scroll if at bottom
                if container.scroll_y >= container.max_scroll_y:
                    self._auto_scroll = True
            except Exception:
                pass
        
        def action_scroll_home(self) -> None:
            """Scroll to top."""
            self._auto_scroll = False
            try:
                container = self.query_one("#scroll-container", ScrollableContainer)
                container.scroll_home()
            except Exception:
                pass
        
        def action_scroll_end(self) -> None:
            """Scroll to bottom and re-enable auto-scroll."""
            self._auto_scroll = True
            self._scroll_to_end()
        
        @property
        def final_content(self) -> str:
            """Get the final content after app exits."""
            with self._lock:
                return self._content


# =============================================================================
# Main StreamingResponseDisplay Class
# =============================================================================

class StreamingResponseDisplay:
    """Manages a scrollable display for streaming LLM responses.
    
    Uses Textual for a full-screen scrollable view if available.
    Falls back to Rich Live display otherwise.
    
    The Textual mode provides:
    - Full scrolling with arrow keys, Page Up/Down, Home/End
    - Mouse wheel scrolling
    - Auto-scroll to bottom (disabled when user scrolls up)
    - Exit with 'q' or Escape
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        word_delay: Optional[float] = None,
        stall_threshold: Optional[float] = None,
        stall_indicator_enabled: Optional[bool] = None,
        on_first_content: Optional[Callable[[], None]] = None,
        use_textual: Optional[bool] = None,
    ):
        """Initialize the streaming display.
        
        Args:
            console: Rich console for output (used for fallback and final print).
            word_delay: Delay between words (only used in fallback mode).
            stall_threshold: Seconds before showing stall indicator (fallback only).
            stall_indicator_enabled: Whether to show stall indicator (fallback only).
            on_first_content: Callback when first content is received.
            use_textual: Force Textual mode on/off. None = auto-detect.
        """
        self._console = console or Console(theme=_STREAMING_THEME)
        self._on_first_content = on_first_content
        
        # Determine whether to use Textual
        if use_textual is not None:
            self._use_textual = use_textual and TEXTUAL_AVAILABLE
        else:
            # Auto-detect: use Textual if available and not disabled via env
            self._use_textual = TEXTUAL_AVAILABLE and _get_env_bool(
                "AYE_STREAM_USE_TEXTUAL", True
            )
        
        # State
        self._current_content: str = ""
        self._started: bool = False
        self._stopped: bool = False
        self._first_content_received: bool = False
        self._lock = threading.Lock()
        
        # Textual app instance (created on start)
        self._app: Optional["StreamingApp"] = None
        self._app_thread: Optional[threading.Thread] = None
        self._app_started_event = threading.Event()
        
        # Fallback mode settings
        self._word_delay = (
            word_delay
            if word_delay is not None
            else _get_env_float("AYE_STREAM_WORD_DELAY", 0.20)
        )
        self._stall_threshold = (
            stall_threshold
            if stall_threshold is not None
            else _get_env_float("AYE_STREAM_STALL_THRESHOLD", 5.0)
        )
        if stall_indicator_enabled is not None:
            self._stall_indicator_enabled = stall_indicator_enabled
        else:
            self._stall_indicator_enabled = _get_env_bool(
                "AYE_STREAM_STALL_INDICATOR", False
            )
        
        # Fallback mode state (Rich Live)
        self._fallback_live = None
        self._animated_content: str = ""
        self._is_animating: bool = False
        self._showing_stall_indicator: bool = False
        self._last_receive_time: float = 0.0
        self._stall_monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()

    def start(self) -> None:
        """Start the streaming display."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stopped = False
        
        if self._use_textual:
            self._start_textual()
        else:
            self._start_fallback()

    def _start_textual(self) -> None:
        """Start the Textual app in a background thread."""
        self._app = StreamingApp(title="Aye Chat Response")
        self._app_started_event.clear()
        
        def run_app():
            try:
                self._app_started_event.set()
                self._app.run()
            except Exception:
                pass
        
        self._app_thread = threading.Thread(target=run_app, daemon=True)
        self._app_thread.start()
        
        # Wait for app to start (with timeout)
        self._app_started_event.wait(timeout=2.0)
        # Give the app a moment to fully initialize
        time.sleep(0.1)

    def _start_fallback(self) -> None:
        """Start the Rich Live fallback display."""
        from rich.live import Live
        
        self._console.print()  # spacing before panel
        self._fallback_live = Live(
            _create_response_panel("", use_markdown=False),
            console=self._console,
            refresh_per_second=30,
            transient=False,
        )
        self._fallback_live.start()
        self._last_receive_time = time.time()
        
        if self._stall_indicator_enabled:
            self._stop_monitoring.clear()
            self._stall_monitor_thread = threading.Thread(
                target=self._monitor_stall, daemon=True
            )
            self._stall_monitor_thread.start()

    def update(self, content: str, is_final: bool = False) -> None:
        """Update the displayed content.
        
        Args:
            content: The full content to display (not a delta).
            is_final: If True, this is the final update.
        """
        with self._lock:
            if not is_final and content == self._current_content:
                return
            
            self._last_receive_time = time.time()
            
            if not self._first_content_received:
                self._first_content_received = True
                if self._on_first_content:
                    self._on_first_content()
            
            self._current_content = content
        
        # Auto-start if not started
        if not self._started:
            self.start()
        
        if self._use_textual:
            self._update_textual(content, is_final)
        else:
            self._update_fallback(content, is_final)

    def _update_textual(self, content: str, is_final: bool) -> None:
        """Update the Textual app content."""
        if self._app and self._app.is_running:
            self._app.update_content(content, is_final)

    def _update_fallback(self, content: str, is_final: bool) -> None:
        """Update the Rich Live fallback display."""
        with self._lock:
            stall_was_showing = self._showing_stall_indicator
            
            if is_final:
                self._animated_content = content
            else:
                if content.startswith(self._current_content):
                    new_text = content[len(self._animated_content):]
                else:
                    self._animated_content = ""
                    new_text = content
        
        if stall_was_showing:
            self._refresh_fallback(use_markdown=False, show_stall=False)
        
        if is_final:
            self._refresh_fallback(use_markdown=True, show_stall=False)
            return
        
        # For non-final updates, animate word by word
        if not is_final:
            self._animate_fallback(content)

    def _animate_fallback(self, content: str) -> None:
        """Animate content in fallback mode."""
        with self._lock:
            if not self._fallback_live:
                return
            self._is_animating = True
        
        try:
            # Get new text to animate
            new_text = content[len(self._animated_content):]
            if not new_text:
                return
            
            i = 0
            n = len(new_text)
            
            while i < n:
                char = new_text[i]
                
                if char in "\n\r":
                    with self._lock:
                        self._animated_content += char
                    i += 1
                    self._refresh_fallback(use_markdown=True, show_stall=False)
                elif char in " \t":
                    ws_start = i
                    while i < n and new_text[i] in " \t":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[ws_start:i]
                    self._refresh_fallback(use_markdown=False, show_stall=False)
                else:
                    word_start = i
                    while i < n and new_text[i] not in " \t\n\r":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[word_start:i]
                    self._refresh_fallback(use_markdown=False, show_stall=False)
                    
                    if self._word_delay > 0:
                        time.sleep(self._word_delay)
        finally:
            with self._lock:
                self._is_animating = False

    def _refresh_fallback(self, use_markdown: bool = False, show_stall: bool = False) -> None:
        """Refresh the fallback display."""
        with self._lock:
            if not self._fallback_live:
                return
            
            self._fallback_live.update(
                _create_response_panel(
                    self._animated_content,
                    use_markdown=use_markdown,
                    show_stall_indicator=show_stall,
                )
            )
            self._showing_stall_indicator = show_stall

    def _monitor_stall(self) -> None:
        """Monitor for stalls in fallback mode."""
        while not self._stop_monitoring.is_set():
            if self._stop_monitoring.wait(0.5):
                break
            
            with self._lock:
                if not self._started or not self._animated_content:
                    continue
                
                caught_up = (
                    not self._is_animating
                ) and (self._animated_content == self._current_content)
                if not caught_up:
                    continue
                
                time_since_receive = time.time() - self._last_receive_time
                should_show_stall = time_since_receive >= self._stall_threshold
                
                if should_show_stall != self._showing_stall_indicator:
                    self._refresh_fallback(
                        use_markdown=False,
                        show_stall=should_show_stall,
                    )

    def stop(self) -> None:
        """Stop the streaming display and print final content."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            final_content = self._current_content
        
        if self._use_textual:
            self._stop_textual(final_content)
        else:
            self._stop_fallback()

    def _stop_textual(self, final_content: str) -> None:
        """Stop the Textual app and print final content."""
        if self._app and self._app.is_running:
            # Mark as complete
            self._app.update_content(final_content, is_final=True)
            # Give user a moment to see the complete status
            time.sleep(0.3)
            # Exit the app
            self._app.exit()
        
        if self._app_thread:
            self._app_thread.join(timeout=2.0)
            self._app_thread = None
        
        self._app = None
        
        # Print final content to normal terminal for scrollback
        if final_content:
            self._console.print()
            self._console.print(
                _create_response_panel(final_content, use_markdown=True)
            )
            self._console.print()
        
        with self._lock:
            self._started = False

    def _stop_fallback(self) -> None:
        """Stop the Rich Live fallback display."""
        self._stop_monitoring.set()
        if self._stall_monitor_thread and self._stall_monitor_thread.is_alive():
            self._stall_monitor_thread.join(timeout=1.0)
        self._stall_monitor_thread = None
        
        with self._lock:
            live = self._fallback_live
        
        if live:
            if self._animated_content:
                self._refresh_fallback(use_markdown=True, show_stall=False)
            
            with self._lock:
                live.stop()
                self._console.print()
                self._fallback_live = None
                self._started = False

    def is_active(self) -> bool:
        """Check if the display is currently active."""
        if self._use_textual:
            return self._started and self._app is not None and self._app.is_running
        return self._started and self._fallback_live is not None

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

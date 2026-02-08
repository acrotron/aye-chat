"""Streaming UI components for displaying LLM responses progressively.

This module provides the StreamingResponseDisplay class which handles
real-time display of LLM responses in a styled Rich panel with
word-by-word animation and stall detection.

Streaming Markdown rendering uses a "stable prefix + tail" approach:
- Text is split at a safe boundary (respecting fenced code blocks)
- The stable prefix is rendered as Markdown
- The unstable tail is rendered as plain Text
- This avoids incomplete Markdown constructs (especially unclosed
  code fences) from breaking the entire display

Tailing (viewport follow):
- When streaming content exceeds the terminal height, only the last
  N visible lines are rendered (like ``tail -f``).
- A truncation indicator is shown at the top of the panel.
- On final render the full response is printed to terminal scrollback
  so the user can scroll back through the entire answer.
- Controlled via the AYE_STREAM_TAIL env var (default: on).
"""
from aye.presenter.repl_ui import deep_ocean_theme

import os
import re
import time
import threading
from typing import Optional, Callable, Tuple

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# Instead of manually trying to make the theme consistent I just directly used the theme from repl_ui.py
_STREAMING_THEME = deep_ocean_theme

# Regex to detect fenced code block markers (3+ backticks or tildes, optional leading whitespace)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)

# Truncation indicator shown at the top of tailed content
_TRUNCATION_INDICATOR_TEXT = "  \u2191 \u00b7\u00b7\u00b7 (streaming)"
_TRUNCATION_STYLE = "dim italic"


def _get_env_float(env_var: str, default: float) -> float:
    """Get a float value from environment variable with fallback default."""
    try:
        return float(os.environ.get(env_var, str(default)) or str(default))
    except ValueError:
        return default


def _get_env_bool(env_var: str, default: bool) -> bool:
    """Get a boolean value from environment variable with fallback default."""
    val = os.environ.get(env_var, "").strip().lower()
    if val in ("1", "on", "true", "yes"):
        return True
    if val in ("0", "off", "false", "no"):
        return False
    return default


def _split_streaming_markdown(text: str) -> Tuple[str, str]:
    """Split streaming text into a stable Markdown prefix and an unstable tail.

    The splitter ensures that incomplete block-level Markdown constructs
    (especially unclosed fenced code blocks) do not end up in the prefix,
    which would cause them to swallow all subsequent content when rendered.

    Heuristics (in order):
    1. Scan for fenced code block markers (``` or ~~~). If the text is
       currently inside an unclosed fence, cut at the start of that fence
       so the entire open block stays in the tail.
    2. If not inside a fence, cut at the last paragraph boundary (blank line).
    3. Failing that, cut at the last newline.
    4. If no newline exists, the entire text becomes the tail (prefix is empty).

    Returns:
        (prefix, tail) where prefix is safe to render as Markdown.
    """
    if not text:
        return ("", "")

    # --- Step 1: Fence safety ---
    inside_fence = False
    last_open_fence_pos = 0

    for match in _FENCE_RE.finditer(text):
        if not inside_fence:
            # Opening a fence
            inside_fence = True
            last_open_fence_pos = match.start()
        else:
            # Closing a fence
            inside_fence = False

    if inside_fence:
        # We are inside an unclosed fence \ cut right before it
        prefix = text[:last_open_fence_pos]
        tail = text[last_open_fence_pos:]
        return (prefix, tail)

    # --- Step 2: Paragraph boundary ---
    last_blank_line = text.rfind("\n\n")
    if last_blank_line != -1:
        # Cut after the blank line (include it in prefix)
        cut = last_blank_line + 2
        return (text[:cut], text[cut:])

    # --- Step 3: Last newline ---
    last_newline = text.rfind("\n")
    if last_newline != -1:
        cut = last_newline + 1
        return (text[:cut], text[cut:])

    # --- Step 4: No safe boundary \ everything is tail ---
    return ("", text)


def _tail_content(content: str, width: int, max_lines: int) -> Tuple[str, bool]:
    """Tail content to fit within *max_lines* when wrapped at *width*.

    Estimates the number of terminal rows each raw line would occupy
    after wrapping and keeps only the last lines that fit.

    Args:
        content: The raw streaming content.
        width: Available inner width in terminal columns.
        max_lines: Maximum number of wrapped terminal lines to keep.

    Returns:
        ``(tailed_content, is_truncated)`` where *is_truncated* is True
        when content was shortened.
    """
    if not content or max_lines <= 0 or width <= 0:
        return (content, False)

    raw_lines = content.split("\n")

    # Estimate the wrapped-line count for each raw line.
    def _wrapped_height(line: str) -> int:
        if not line:
            return 1  # empty line still occupies one terminal row
        return max(1, -(-len(line) // width))  # ceiling division

    line_heights = [_wrapped_height(line) for line in raw_lines]
    total_height = sum(line_heights)

    if total_height <= max_lines:
        return (content, False)

    # Accumulate from the end until we fill the budget.
    accumulated = 0
    start_index = len(raw_lines)
    for i in range(len(raw_lines) - 1, -1, -1):
        if accumulated + line_heights[i] > max_lines:
            break
        accumulated += line_heights[i]
        start_index = i

    # Ensure at least the last raw line is included.
    if start_index >= len(raw_lines):
        start_index = len(raw_lines) - 1

    tailed = "\n".join(raw_lines[start_index:])
    return (tailed, True)


def _render_streaming_markdown(
    content: str,
    show_stall_indicator: bool = False,
    is_truncated: bool = False,
):
    """Build a composite renderable for streaming: Markdown prefix + Text tail.

    Uses rich.console.Group to vertically stack the parts.  When
    *is_truncated* is True a dimmed indicator is prepended so the user
    knows earlier content has scrolled out of view.

    Returns:
        A Rich renderable (Group) containing the formatted parts.
    """
    prefix, tail = _split_streaming_markdown(content)

    parts = []

    # Truncation indicator (shown when tailing has kicked in)
    if is_truncated:
        parts.append(Text(_TRUNCATION_INDICATOR_TEXT, style=_TRUNCATION_STYLE))

    if prefix:
        parts.append(Markdown(prefix))

    if tail:
        parts.append(Text(tail))

    # Fallback: if content is non-empty but both parts are empty (shouldn't happen)
    if not parts and content:
        parts.append(Text(content))

    if show_stall_indicator:
        parts.append(Text("\n\u22ef waiting for more", style="ui.stall_spinner"))

    # If there's only one part and no stall, return it directly (avoid Group overhead)
    if len(parts) == 1:
        return parts[0]

    return Group(*parts)


def _create_response_panel(
    content: str,
    use_markdown: bool = True,
    show_stall_indicator: bool = False,
    streaming: bool = False,
    is_truncated: bool = False,
) -> Panel:
    """Create a styled response panel matching the final response display.

    Args:
        content: The text content to render.
        use_markdown: Whether to render content as Markdown.
        show_stall_indicator: Whether to append a stall indicator.
        streaming: If True and use_markdown is True, use the composite
                   streaming renderer (stable prefix Markdown + tail Text)
                   instead of rendering the entire content as Markdown.
        is_truncated: If True, show a truncation indicator at the top of
                      the content (only relevant when *streaming* is True).
    """
    # Decorative "sonar pulse" marker
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]\u25cf[/] [ui.response_symbol.waves]))[/]"

    # A 2-column grid: marker + content
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    if use_markdown and streaming and content:
        # Streaming markdown: composite renderable handles stall indicator internally
        rendered_content = _render_streaming_markdown(
            content,
            show_stall_indicator=show_stall_indicator,
            is_truncated=is_truncated,
        )
    elif use_markdown and content:
        # Final markdown render
        rendered_content = Markdown(content)
        if show_stall_indicator:
            rendered_content = Group(
                rendered_content,
                Text("\n\u22ef waiting for more", style="ui.stall_spinner"),
            )
    else:
        rendered_content = Text(content) if content else Text("")
        if show_stall_indicator:
            rendered_content.append("\n\u22ef waiting for more", style="ui.stall_spinner")

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
    """Manages a live-updating Rich panel for streaming LLM responses."""

    def __init__(
        self,
        console: Optional[Console] = None,
        word_delay: Optional[float] = 0.20,
        stall_threshold: Optional[float] = 3.0,
        on_first_content: Optional[Callable[[], None]] = None,
    ):
        self._console = console or Console(theme=_STREAMING_THEME)
        self._live: Optional[Live] = None

        self._current_content: str = ""  # Full content received so far
        self._animated_content: str = ""  # Content that has been animated

        self._started: bool = False
        self._first_content_received: bool = False
        self._on_first_content = on_first_content

        # Configuration with env var fallbacks
        self._word_delay = word_delay if word_delay is not None else _get_env_float("AYE_STREAM_WORD_DELAY", 0.20)
        self._stall_threshold = (
            stall_threshold if stall_threshold is not None else _get_env_float("AYE_STREAM_STALL_THRESHOLD", 3.0)
        )

        # Throttle: minimum interval between Markdown re-renders (seconds)
        self._min_render_interval = _get_env_float("AYE_STREAM_RENDER_INTERVAL", 0.08)
        self._last_render_time: float = 0.0

        # Tailing configuration
        self._tail_enabled: bool = _get_env_bool("AYE_STREAM_TAIL", True)

        # Synchronization: Live + internal state are touched by monitor thread
        # and by whichever thread calls update(). We must serialize them.
        self._lock = threading.RLock()

        # Stall detection state
        # NOTE: this must track *when we last received new content from the stream*,
        # not when we last refreshed the UI. If we update this timestamp when we draw
        # the stall indicator, the indicator will blink on/off.
        self._last_receive_time: float = 0.0
        self._is_animating: bool = False
        self._showing_stall_indicator: bool = False

        self._stall_monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()

    # ------------------------------------------------------------------
    # Tailing helpers
    # ------------------------------------------------------------------

    def _compute_available_lines(self, show_stall: bool) -> int:
        """Return how many wrapped content lines fit in the terminal.

        During streaming, prompt_toolkit is *not* active (no completion
        menu), so we only need to account for panel chrome, the stall
        indicator, and a small safety buffer.
        """
        terminal_height = self._console.size.height
        panel_chrome = 2   # top + bottom border
        # "\n\u22ef waiting for more" occupies ~2 rows (blank line + text)
        stall_lines = 2 if show_stall else 0
        buffer = 2         # safety margin for cursor / prompt restoration

        available = terminal_height - panel_chrome - stall_lines - buffer
        return max(3, available)

    def _compute_inner_width(self) -> int:
        """Return the usable content width inside the streaming panel.

        Subtracts panel borders, padding, the grid gap, and the pulse
        marker column from the terminal width.
        """
        terminal_width = self._console.size.width
        # panel border (2) + panel padding h=1 each side (2)
        # + grid gap (1) + pulse marker column (~7 visible chars)
        overhead = 12
        return max(20, terminal_width - overhead)

    # ------------------------------------------------------------------
    # Display refresh
    # ------------------------------------------------------------------

    def _refresh_display(
        self,
        use_markdown: bool = False,
        show_stall: bool = False,
        streaming: bool = False,
        force: bool = False,
    ) -> None:
        """Refresh the live display with current animated content.

        When streaming Markdown is active, refreshes are throttled to avoid
        expensive re-parsing on every word. The throttle is bypassed when
        ``force=True`` (used for stall-state transitions and final renders).

        When tailing is enabled and *streaming* is True, only the last
        visible portion of the content is rendered so the panel always
        follows the bottom of the response.

        Args:
            use_markdown: Render content as Markdown.
            show_stall: Show the stall indicator.
            streaming: Use composite streaming Markdown renderer.
            force: Bypass the throttle (for state transitions / final).
        """
        with self._lock:
            if not self._live:
                return

            # Throttle: skip render if too soon, unless forced
            now = time.time()
            if not force and (now - self._last_render_time) < self._min_render_interval:
                return

            content = self._animated_content
            is_truncated = False

            # --- Tailing logic (streaming only) ---
            if streaming and self._tail_enabled and content:
                available = self._compute_available_lines(show_stall)
                inner_width = self._compute_inner_width()
                content, is_truncated = _tail_content(content, inner_width, available)
                if is_truncated:
                    # Re-tail with one fewer line to make room for the
                    # truncation indicator.
                    content, _ = _tail_content(
                        self._animated_content, inner_width, available - 1,
                    )

            self._live.update(
                _create_response_panel(
                    content,
                    use_markdown=use_markdown,
                    show_stall_indicator=show_stall,
                    streaming=streaming,
                    is_truncated=is_truncated,
                )
            )
            self._last_render_time = now
            self._showing_stall_indicator = show_stall

    # ------------------------------------------------------------------
    # Final render helper
    # ------------------------------------------------------------------

    def _render_final_and_stop(self) -> None:
        """Clear Live, stop it, and print the full final panel to scrollback.

        This avoids the "double output" problem: Live.stop() with
        transient=False would freeze whatever was last rendered (possibly
        a tailed/cropped view).  By clearing first and then printing the
        full panel via console.print, the complete response ends up in
        real terminal scrollback where the user can scroll through it.
        """
        with self._lock:
            live = self._live
            final_content = self._animated_content

            if not live:
                return

            # Clear the Live region so stop() doesn't leave a stale frame.
            # Text("") renders as effectively nothing.
            live.update(Text(""))
            live.stop()
            self._live = None
            self._started = False

        # Print the full final response outside the lock (console.print
        # is safe to call without holding our lock).
        if final_content:
            final_panel = _create_response_panel(
                final_content,
                use_markdown=True,
                show_stall_indicator=False,
                streaming=False,
                is_truncated=False,
            )
            self._console.print(final_panel)

        self._console.print()  # spacing after panel

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the live display."""
        with self._lock:
            if self._started:
                return

            self._console.print()  # spacing before panel
            self._live = Live(
                _create_response_panel("", use_markdown=False),
                console=self._console,
                refresh_per_second=30,
                transient=False,
            )
            self._live.start()
            self._started = True
            self._last_receive_time = time.time()

            # Start stall monitoring thread
            self._stop_monitoring.clear()
            self._stall_monitor_thread = threading.Thread(target=self._monitor_stall, daemon=True)
            self._stall_monitor_thread.start()

    def _monitor_stall(self) -> None:
        """Monitor for stalls.

        A true stall is:
        - we are not animating
        - AND the animated output has caught up to the received content
        - AND we have not received new stream content for >= stall_threshold

        Important: do NOT use a timestamp that is updated when the stall indicator is rendered,
        otherwise the stall indicator will blink (it resets its own timer).
        """
        while not self._stop_monitoring.is_set():
            if self._stop_monitoring.wait(0.5):
                break

            with self._lock:
                if not self._started or not self._animated_content:
                    continue

                caught_up = (not self._is_animating) and (self._animated_content == self._current_content)
                if not caught_up:
                    # If we were showing stall but new content is now pending/animating,
                    # the animation path will refresh with show_stall=False.
                    continue

                time_since_receive = time.time() - self._last_receive_time
                should_show_stall = time_since_receive >= self._stall_threshold

                # Only redraw when the stall state changes \u2014 force-flush the throttle.
                # Delegated to _refresh_display so tailing is applied consistently.
                if should_show_stall != self._showing_stall_indicator:
                    self._refresh_display(
                        use_markdown=True,
                        show_stall=should_show_stall,
                        streaming=True,
                        force=True,
                    )

    def _animate_words(self, new_text: str) -> None:
        """Animate new text word by word with streaming Markdown rendering."""
        if not new_text:
            return

        with self._lock:
            if not self._live:
                return
            self._is_animating = True

        try:
            i = 0
            n = len(new_text)

            while i < n:
                char = new_text[i]

                if char in "\n\r":
                    with self._lock:
                        self._animated_content += char
                    i += 1
                    # Newlines often complete a Markdown block \u2014 force render
                    self._refresh_display(
                        use_markdown=True, show_stall=False, streaming=True, force=True,
                    )

                elif char in " \t":
                    ws_start = i
                    while i < n and new_text[i] in " \t":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[ws_start:i]
                    self._refresh_display(
                        use_markdown=True, show_stall=False, streaming=True,
                    )

                else:
                    word_start = i
                    while i < n and new_text[i] not in " \t\n\r":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[word_start:i]
                    self._refresh_display(
                        use_markdown=True, show_stall=False, streaming=True,
                    )

                    if self._word_delay > 0:
                        time.sleep(self._word_delay)

        finally:
            with self._lock:
                self._is_animating = False

    def update(self, content: str, is_final: bool = False) -> None:
        """Update the displayed content.

        By default, updates are animated word-by-word.
        If ``is_final=True``, animation is skipped and the content is
        rendered immediately as a full Markdown panel printed to terminal
        scrollback (Live is stopped).

        Args:
            content: The full content to display (not a delta).
            is_final: If True, stop animating and render final content immediately.
        """
        with self._lock:
            # For finalization, we must still run even if content matches.
            if not is_final and content == self._current_content:
                return

            # This is the key timestamp for stall detection:
            # it should only change when new stream content arrives.
            self._last_receive_time = time.time()

            # Fire the on_first_content callback before starting the display
            if not self._first_content_received:
                self._first_content_received = True
                if self._on_first_content:
                    self._on_first_content()

        # Auto-start if not started
        if not self._started:
            self.start()

        new_text = ""

        # Decide how to update state under lock
        with self._lock:
            stall_was_showing = self._showing_stall_indicator

            if is_final:
                # Immediately snap to the final content (no word-by-word delays).
                self._current_content = content
                self._animated_content = content
            else:
                if content.startswith(self._current_content):
                    new_text = content[len(self._current_content):]
                else:
                    self._animated_content = ""
                    new_text = content

                self._current_content = content

        # --- Final render path ---
        # Stop monitoring, clear Live, print full panel to scrollback.
        if is_final:
            self._stop_monitoring.set()
            self._render_final_and_stop()
            return

        # --- Streaming render path ---

        # If stall indicator is currently shown, hide it immediately.
        # Force-flush so the change is visible without throttle delay.
        if stall_was_showing:
            self._refresh_display(
                use_markdown=True, show_stall=False, streaming=True, force=True,
            )

        # Streaming render: animate only the delta.
        if new_text:
            self._animate_words(new_text)

    def stop(self) -> None:
        """Stop the live display.

        If ``update(is_final=True)`` was already called, Live is already
        stopped and this method only cleans up the monitoring thread.
        Otherwise it acts as a safety-net: clears Live, stops it, and
        prints whatever content has been accumulated so far.
        """
        # Stop the monitoring thread
        self._stop_monitoring.set()
        if self._stall_monitor_thread and self._stall_monitor_thread.is_alive():
            self._stall_monitor_thread.join(timeout=1.0)
        self._stall_monitor_thread = None

        with self._lock:
            if not self._live:
                # Already stopped (e.g. by update(is_final=True)).
                return

        # Safety-net: render final output and stop Live.
        self._render_final_and_stop()

    def is_active(self) -> bool:
        return self._started and self._live is not None

    def has_received_content(self) -> bool:
        return self._first_content_received

    @property
    def content(self) -> str:
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

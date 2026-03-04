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

Resize handling:
- When the terminal is resized (especially narrowed), content reflows
  instantly to more lines at the OS/terminal level. Rich's internal
  height tracking becomes stale, causing orphaned lines ("duplicate
  boxes") on subsequent renders.

- The fix uses **polling-based resize detection** combined with
  SIGWINCH (on POSIX) for a cross-platform solution:

  1. **Polling (all platforms, including Windows)**: Before every
     render and at the top of each animation word, the current
     terminal size is compared against the last known size. If
     it changed, renders are suppressed and a debounced restart
     is armed.

  2. **SIGWINCH (POSIX only)**: On Linux/macOS, the SIGWINCH signal
     handler provides immediate flag-setting. Not available on
     Windows.

  3. **Render suppression**: ``_refresh_display()`` returns early
     while ``_restart_live_on_resize`` is True. The animation
     loop continues to accumulate words in memory but produces
     no visible output.

  4. **Debounced restart**: Worker threads wait for a cooldown to
     expire (user has stopped dragging), then stop the old Live
     and create a fresh one with accurate geometry.

  NO raw ANSI escape codes are used for screen clearing. This avoids
  the ``[2J[H`` literal text problem on Windows terminals without
  VT processing. Instead, Rich's own Live stop/start handles cleanup.
"""
from aye.presenter.repl_ui import deep_ocean_theme

import os
import re
import signal
import sys
import time
import threading
from typing import Optional, Callable, Tuple

from rich import box
from rich.box import Box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


_STREAMING_THEME = deep_ocean_theme

# Regex to detect fenced code block markers (3+ backticks or tildes)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)

# Truncation indicator shown at the top of tailed content
_TRUNCATION_INDICATOR_TEXT = "  \u2191 \u00b7\u00b7\u00b7 (streaming)"
_TRUNCATION_STYLE = "dim italic"

# Custom box with only top and bottom borders (no left/right sides)
_HORIZONTAL_ONLY_BOX = Box(
    "\u2500\u2500\u2500\u2500\n"  # top: all horizontal lines (no corners)
    "    \n"                    # head: spaces (no vertical borders)
    "    \n"                    # head row separator: spaces
    "    \n"                    # mid: spaces
    "    \n"                    # row: spaces
    "    \n"                    # foot row: spaces
    "    \n"                    # foot: spaces
    "\u2500\u2500\u2500\u2500"    # bottom: all horizontal lines (no corners)
)


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

    inside_fence = False
    last_open_fence_pos = 0

    for match in _FENCE_RE.finditer(text):
        if not inside_fence:
            inside_fence = True
            last_open_fence_pos = match.start()
        else:
            inside_fence = False

    if inside_fence:
        prefix = text[:last_open_fence_pos]
        tail = text[last_open_fence_pos:]
        return (prefix, tail)

    last_blank_line = text.rfind("\n\n")
    if last_blank_line != -1:
        cut = last_blank_line + 2
        return (text[:cut], text[cut:])

    last_newline = text.rfind("\n")
    if last_newline != -1:
        cut = last_newline + 1
        return (text[:cut], text[cut:])

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

    def _wrapped_height(line: str) -> int:
        if not line:
            return 1
        return max(1, -(-len(line) // width))  # ceiling division

    line_heights = [_wrapped_height(line) for line in raw_lines]
    total_height = sum(line_heights)

    if total_height <= max_lines:
        return (content, False)

    accumulated = 0
    start_index = len(raw_lines)
    for i in range(len(raw_lines) - 1, -1, -1):
        if accumulated + line_heights[i] > max_lines:
            break
        accumulated += line_heights[i]
        start_index = i

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

    if is_truncated:
        parts.append(Text(_TRUNCATION_INDICATOR_TEXT, style=_TRUNCATION_STYLE))

    if prefix:
        parts.append(Markdown(prefix))

    if tail:
        parts.append(Text(tail))

    if not parts and content:
        parts.append(Text(content))

    if show_stall_indicator:
        parts.append(Text("\n\u22ef waiting for more", style="ui.stall_spinner"))

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
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]\u25cf[/] [ui.response_symbol.waves]))[/]"

    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    if use_markdown and streaming and content:
        rendered_content = _render_streaming_markdown(
            content,
            show_stall_indicator=show_stall_indicator,
            is_truncated=is_truncated,
        )
    elif use_markdown and content:
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

    return Panel(
        grid,
        border_style="ui.border",
        box=_HORIZONTAL_ONLY_BOX,
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

        self._current_content: str = ""
        self._animated_content: str = ""

        self._started: bool = False
        self._first_content_received: bool = False
        self._on_first_content = on_first_content

        self._word_delay = word_delay if word_delay is not None else _get_env_float("AYE_STREAM_WORD_DELAY", 0.20)
        self._stall_threshold = (
            stall_threshold if stall_threshold is not None else _get_env_float("AYE_STREAM_STALL_THRESHOLD", 3.0)
        )

        self._min_render_interval = _get_env_float("AYE_STREAM_RENDER_INTERVAL", 0.08)
        self._last_render_time: float = 0.0

        self._tail_enabled: bool = _get_env_bool("AYE_STREAM_TAIL", True)

        # Main lock for all state access.
        self._lock = threading.RLock()

        self._last_receive_time: float = 0.0
        self._is_animating: bool = False
        self._showing_stall_indicator: bool = False

        self._stall_monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()

        # -----------------------------------------------------------
        # Resize handling \u2014 polling + SIGWINCH.
        #
        # Primary mechanism (ALL platforms, including Windows):
        #   Before every render, compare the current terminal size
        #   against stored geometry.  If it changed, suppress renders
        #   and arm a debounced restart.
        #
        # Secondary mechanism (POSIX only):
        #   SIGWINCH handler sets the flag and cooldown immediately.
        #   Not available on Windows.
        #
        # NO raw ANSI escape codes are written.  This avoids the
        # "[2J[H" literal text problem on Windows terminals without
        # VT processing enabled.  Instead, the old Live is stopped
        # (Rich handles its own terminal region cleanup) and a new
        # Live is created with fresh geometry.
        # -----------------------------------------------------------
        self._resize_cooldown_until: float = 0.0
        self._resize_cooldown_secs: float = _get_env_float("AYE_STREAM_RESIZE_COOLDOWN", 0.5)
        self._restart_live_on_resize: bool = False
        self._prev_sigwinch_handler = None

        self._settle_delay_secs: float = _get_env_float("AYE_STREAM_SETTLE_DELAY", 0.05)

        # Track terminal geometry for polling-based resize detection.
        # Initialised to 0; set properly in start().
        self._last_known_width: int = 0
        self._last_known_height: int = 0

    # ------------------------------------------------------------------
    # Resize detection (polling \u2014 works on ALL platforms)
    # ------------------------------------------------------------------

    def _detect_resize(self) -> bool:
        """Check if the terminal size has changed since the last check.

        This is the **primary** resize detection mechanism and works on
        all platforms including Windows (which lacks SIGWINCH).

        When a resize is detected:
        1. The restart flag is set and cooldown is armed.
        2. The stored geometry is updated to the new size.

        No screen clearing is done here \u2014 that is handled by the
        Live stop/start cycle in ``_restart_live_for_resize()``.

        Returns True if a resize was detected.
        """
        try:
            current_width = self._console.size.width
            current_height = self._console.size.height
        except Exception:
            return False

        if (current_width == self._last_known_width
                and current_height == self._last_known_height):
            return False

        # Geometry changed \u2014 terminal was resized.
        self._last_known_width = current_width
        self._last_known_height = current_height

        # Arm the debounced restart.
        self._resize_cooldown_until = time.time() + self._resize_cooldown_secs
        self._restart_live_on_resize = True

        return True

    # ------------------------------------------------------------------
    # SIGWINCH handling (POSIX only \u2014 fast path)
    # ------------------------------------------------------------------

    def _install_sigwinch_handler(self) -> None:
        """Install our SIGWINCH handler after live.start().

        On POSIX systems (Linux, macOS), SIGWINCH fires immediately
        when the terminal resizes.  Our handler sets the restart flag
        and extends the cooldown.

        On Windows this is a no-op (SIGWINCH does not exist); the
        polling-based ``_detect_resize()`` handles everything.
        """
        sigwinch = getattr(signal, "SIGWINCH", None)
        if sigwinch is None:
            return  # Windows \u2014 no SIGWINCH.

        def _our_handler(signum, frame):
            self._resize_cooldown_until = time.time() + self._resize_cooldown_secs
            self._restart_live_on_resize = True

        self._prev_sigwinch_handler = signal.signal(sigwinch, _our_handler)

    def _restore_sigwinch_handler(self) -> None:
        """Restore the SIGWINCH handler that was active before start()."""
        sigwinch = getattr(signal, "SIGWINCH", None)
        if sigwinch is None or self._prev_sigwinch_handler is None:
            return
        try:
            signal.signal(sigwinch, self._prev_sigwinch_handler)
        except Exception:
            pass
        self._prev_sigwinch_handler = None

    # ------------------------------------------------------------------
    # Debounced Live restart
    # ------------------------------------------------------------------

    def _restart_live_for_resize(self) -> None:
        """Stop the current Live and start a fresh one.

        Called from worker threads AFTER the resize cooldown has
        expired.  Ensures:
        1. The user has finished resizing.
        2. We use the FINAL terminal dimensions.
        3. Only ONE restart happens per resize operation.

        No raw ANSI escape codes are written.  Rich's own
        ``live.stop()`` handles clearing the Live region, and the
        new ``live.start()`` renders fresh content at the correct
        geometry.
        """
        with self._lock:
            if not self._restart_live_on_resize:
                return
            self._restart_live_on_resize = False

            live = self._live
            if live is None:
                return

            content = self._animated_content

            # Update stored geometry to the current (final) size.
            try:
                self._last_known_width = self._console.size.width
                self._last_known_height = self._console.size.height
            except Exception:
                pass

            # Stop the old Live.  Rich will handle clearing its own
            # terminal region \u2014 no raw ANSI codes needed.
            try:
                live.update(Text(""))
            except Exception:
                pass
            try:
                live.stop()
            except Exception:
                pass

            # Settle delay.
            if self._settle_delay_secs > 0:
                time.sleep(self._settle_delay_secs)

            # Create a fresh Live with accurate geometry.
            self._live = Live(
                _create_response_panel("", use_markdown=False),
                console=self._console,
                auto_refresh=False,
                vertical_overflow="visible",
                transient=False,
            )
            self._live.start()

            # Render current content into the new Live.
            if content:
                is_truncated = False
                display_content = content
                if self._tail_enabled:
                    available = self._compute_available_lines(show_stall=False)
                    inner_width = self._compute_inner_width()
                    display_content, is_truncated = _tail_content(
                        content, inner_width, available,
                    )
                    if is_truncated:
                        display_content, _ = _tail_content(
                            content, inner_width, available - 1,
                        )

                self._live.update(
                    _create_response_panel(
                        display_content,
                        use_markdown=True,
                        show_stall_indicator=False,
                        streaming=True,
                        is_truncated=is_truncated,
                    )
                )
                self._live.refresh()

            self._last_render_time = time.time()

    def _handle_resize_if_needed(self) -> bool:
        """Check for pending resize and handle it if cooldown has expired.

        Also runs polling-based resize detection for platforms without
        SIGWINCH (Windows).

        Returns True if a resize was detected or processed.
        """
        # Polling-based detection \u2014 works on all platforms.
        if self._detect_resize():
            time.sleep(0.02)
            return True

        if not self._restart_live_on_resize:
            return False

        now = time.time()
        if now < self._resize_cooldown_until:
            time.sleep(0.02)
            return True  # Still in cooldown \u2014 suppress but signal "busy"

        # Cooldown expired \u2014 safe to restart.
        self._restart_live_for_resize()
        return True

    # ------------------------------------------------------------------
    # Tailing helpers
    # ------------------------------------------------------------------

    def _compute_available_lines(self, show_stall: bool) -> int:
        """Return how many wrapped content lines fit in the terminal."""
        terminal_height = self._console.size.height
        panel_chrome = 2
        stall_lines = 2 if show_stall else 0
        buffer = 2

        available = terminal_height - panel_chrome - stall_lines - buffer
        return max(3, available)

    def _compute_inner_width(self) -> int:
        """Return the usable content width inside the streaming panel."""
        terminal_width = self._console.size.width
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

        Resize handling:
            1. Runs ``_detect_resize()`` to catch geometry changes on
               all platforms (including Windows which lacks SIGWINCH).
            2. If ``_restart_live_on_resize`` is True, returns
               immediately \u2014 no ``live.update()``, no ``live.refresh()``.

        Args:
            use_markdown: Render content as Markdown.
            show_stall: Show the stall indicator.
            streaming: Use composite streaming Markdown renderer.
            force: Bypass the throttle.  Does NOT bypass resize check.
        """
        with self._lock:
            if not self._live:
                return

            # \u2500\u2500 Polling-based resize detection \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
            self._detect_resize()

            # \u2500\u2500 Resize gate \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
            if self._restart_live_on_resize:
                return

            now = time.time()

            # Throttle guard.
            if not force and (now - self._last_render_time) < self._min_render_interval:
                return

            content = self._animated_content
            is_truncated = False

            if streaming and self._tail_enabled and content:
                available = self._compute_available_lines(show_stall)
                inner_width = self._compute_inner_width()
                content, is_truncated = _tail_content(content, inner_width, available)
                if is_truncated:
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

            self._live.refresh()
            self._last_render_time = now
            self._showing_stall_indicator = show_stall

    # ------------------------------------------------------------------
    # Final render helper
    # ------------------------------------------------------------------

    def _render_final_and_stop(self) -> None:
        """Clear Live, stop it, and print the full final panel to scrollback."""
        with self._lock:
            live = self._live
            final_content = self._animated_content

            if not live:
                return

            self._restore_sigwinch_handler()

            try:
                live.update(Text(""))
                live.refresh()
                live.stop()
            except Exception:
                pass

            self._live = None
            self._started = False

        if final_content:
            final_panel = _create_response_panel(
                final_content,
                use_markdown=True,
                show_stall_indicator=False,
                streaming=False,
                is_truncated=False,
            )
            self._console.print(final_panel)

        self._console.print()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the live display."""
        with self._lock:
            if self._started:
                return

            # Snapshot current geometry for polling-based detection.
            try:
                self._last_known_width = self._console.size.width
                self._last_known_height = self._console.size.height
            except Exception:
                self._last_known_width = 80
                self._last_known_height = 24

            self._console.print()
            self._live = Live(
                _create_response_panel("", use_markdown=False),
                console=self._console,
                auto_refresh=False,
                vertical_overflow="visible",
                transient=False,
            )
            self._live.start()
            self._started = True
            self._last_receive_time = time.time()

            self._install_sigwinch_handler()

            self._stop_monitoring.clear()
            self._stall_monitor_thread = threading.Thread(
                target=self._monitor_stall, daemon=True,
            )
            self._stall_monitor_thread.start()

    def _monitor_stall(self) -> None:
        """Monitor for stalls and handle resize restarts."""
        while not self._stop_monitoring.is_set():
            if self._stop_monitoring.wait(0.05):
                break

            # Handle pending resize if cooldown has expired.
            if self._handle_resize_if_needed():
                continue

            with self._lock:
                if not self._started or not self._animated_content:
                    continue

                caught_up = (
                    (not self._is_animating)
                    and (self._animated_content == self._current_content)
                )
                if not caught_up:
                    continue

                time_since_receive = time.time() - self._last_receive_time
                should_show_stall = time_since_receive >= self._stall_threshold

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
                # Handle pending resize if cooldown has expired.
                self._handle_resize_if_needed()

                char = new_text[i]

                if char in "\n\r":
                    with self._lock:
                        self._animated_content += char
                    i += 1
                    self._refresh_display(
                        use_markdown=True, show_stall=False,
                        streaming=True, force=True,
                    )

                elif char in " \t":
                    ws_start = i
                    while i < n and new_text[i] in " \t":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[ws_start:i]
                    self._refresh_display(
                        use_markdown=True, show_stall=False,
                        streaming=True,
                    )

                else:
                    word_start = i
                    while i < n and new_text[i] not in " \t\n\r":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[word_start:i]
                    self._refresh_display(
                        use_markdown=True, show_stall=False,
                        streaming=True,
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
            if not is_final and content == self._current_content:
                return

            self._last_receive_time = time.time()

            if not self._first_content_received:
                self._first_content_received = True
                if self._on_first_content:
                    self._on_first_content()

        if not self._started:
            self.start()

        new_text = ""

        with self._lock:
            stall_was_showing = self._showing_stall_indicator

            if is_final:
                self._current_content = content
                self._animated_content = content
            else:
                if content.startswith(self._current_content):
                    new_text = content[len(self._current_content):]
                else:
                    self._animated_content = ""
                    new_text = content

                self._current_content = content

        if is_final:
            self._stop_monitoring.set()
            self._render_final_and_stop()
            return

        if stall_was_showing:
            self._refresh_display(
                use_markdown=True, show_stall=False,
                streaming=True, force=True,
            )

        if new_text:
            self._animate_words(new_text)

    def stop(self) -> None:
        """Stop the live display."""
        self._stop_monitoring.set()
        if self._stall_monitor_thread and self._stall_monitor_thread.is_alive():
            self._stall_monitor_thread.join(timeout=1.0)
        self._stall_monitor_thread = None

        with self._lock:
            if not self._live:
                return

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

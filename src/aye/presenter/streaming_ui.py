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
- When terminal resize is detected during streaming:
  1. SIGWINCH signal handler (Unix) provides instant notification
  2. On Windows, faster polling (25ms vs 50ms) compensates for lack of signal
  3. Pre/post dimension check catches resizes during panel building
  4. Stop the Live instance immediately (erases the transient box)
  5. Force-clear any ghost artifacts using platform-appropriate clearing
  6. Wait for resize to stabilize (cooldown period)
  7. Only after cooldown expires AND no new resize detected,
     create a fresh Live instance and resume rendering
- The code NEVER prints during resize - verified by checking that
  all render paths return early when _resize_in_progress is True
  or _live is None.
- Resize is detected via SIGWINCH (instant on Unix) AND polling in stall
  monitor thread as fallback for Windows/non-Unix systems.

Live overflow:
- Live uses Rich's default vertical overflow ("ellipsis").  This
  ensures Rich's internal height tracking is always correct --
  cursor-up movements match actual rendered height on every refresh.
- Because tailing already keeps content within terminal height, the
  ellipsis overflow should never trigger in practice.
- Using ``vertical_overflow="visible"`` would break Live's height
  tracking when content exceeds the terminal, causing duplicate
  boxes / artifacts.  Do NOT use it.

Transient Live:
- Live uses ``transient=True`` so that ``stop()`` erases the live
  region instead of permanently printing it.
- The final response is then printed permanently via
  ``console.print()`` so it appears in terminal scrollback.

Debugging:
- Set AYE_STREAM_DEBUG=on to enable detailed logging of render events.
- Logs show timestamps, resize detection, cooldown state, and render calls.
"""
from aye.presenter.repl_ui import deep_ocean_theme

import os
import re
import signal
import sys
import time
import threading
from typing import Optional, Callable, Tuple, Any

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


_STREAMING_THEME = deep_ocean_theme

# Regex to detect fenced code block markers (3+ backticks or tildes)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)

# Truncation indicator shown at the top of tailed content
_TRUNCATION_INDICATOR_TEXT = "  \u2191 \u00b7\u00b7\u00b7 (streaming)"
_TRUNCATION_STYLE = "dim italic"

# How long to wait after last resize before resuming (seconds)
_RESIZE_COOLDOWN_SECS = 0.25

# How long to wait for terminal to settle before stopping Live during resize (seconds)
# This gives the terminal emulator time to finish its resize operation
_RESIZE_SETTLE_DELAY = 0.02

# How many lines to clear when cleaning up ghost artifacts
# Unix needs more aggressive clearing due to SIGWINCH timing issues
# Windows ghosts are typically smaller/rarer
_GHOST_CLEAR_LINES_UNIX = 25
_GHOST_CLEAR_LINES_WINDOWS = 10

# Polling intervals for resize detection in stall monitor thread
# Unix has SIGWINCH for instant notification, so polling is just a fallback
# Windows has no signal, so we poll more frequently to reduce ghost window
_POLL_INTERVAL_UNIX = 0.05      # 50ms - SIGWINCH handles most cases
_POLL_INTERVAL_WINDOWS = 0.025  # 25ms - faster polling to compensate for no signal


def _is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == 'win32'


def _get_poll_interval() -> float:
    """Get the appropriate poll interval for the current platform.
    
    Windows lacks SIGWINCH, so we poll more frequently to reduce
    the window where ghosts can appear during resize.
    """
    if _is_windows():
        return _POLL_INTERVAL_WINDOWS
    return _POLL_INTERVAL_UNIX


def _get_ghost_clear_lines() -> int:
    """Get the number of lines to clear for ghost cleanup.
    
    Windows typically has smaller/fewer ghost artifacts due to
    lack of SIGWINCH (no signal timing issues), so we clear less.
    """
    if _is_windows():
        return _GHOST_CLEAR_LINES_WINDOWS
    return _GHOST_CLEAR_LINES_UNIX


# =============================================================================
# Debug Logging
# =============================================================================

_DEBUG_ENABLED: Optional[bool] = None


def _is_debug_enabled() -> bool:
    """Check if debug logging is enabled via AYE_STREAM_DEBUG env var."""
    global _DEBUG_ENABLED
    if _DEBUG_ENABLED is None:
        val = os.environ.get("AYE_STREAM_DEBUG", "").strip().lower()
        _DEBUG_ENABLED = val in ("1", "on", "true", "yes")
    return _DEBUG_ENABLED


def _debug_log(message: str) -> None:
    """Log a debug message with timestamp if debug is enabled.
    
    Writes directly to stderr to avoid interfering with Rich output.
    """
    if not _is_debug_enabled():
        return
    timestamp = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
    sys.stderr.write(f"[STREAM_DEBUG {timestamp}] {message}\n")
    sys.stderr.flush()


# =============================================================================
# Environment Helpers
# =============================================================================


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


# =============================================================================
# Markdown Splitting
# =============================================================================


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


# =============================================================================
# Tailing
# =============================================================================


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


# =============================================================================
# Renderable Builders
# =============================================================================


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
    _debug_log(f"_create_response_panel called: content_len={len(content)}, streaming={streaming}, is_truncated={is_truncated}")
    
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
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


# =============================================================================
# StreamingResponseDisplay
# =============================================================================


class StreamingResponseDisplay:
    """Manages a live-updating Rich panel for streaming LLM responses.

    Uses ``transient=True`` on Rich's Live so that ``stop()`` **erases**
    the live region instead of permanently printing it.

    Does NOT use ``vertical_overflow="visible"`` \u2014 that setting breaks
    Live's internal height tracking, causing cursor-up movements to
    mismatch actual rendered height and producing duplicate boxes.
    Rich's default overflow ("ellipsis") keeps height tracking correct.
    Tailing ensures content fits the terminal, so ellipsis never triggers.

    The final response is printed permanently to terminal scrollback via
    ``console.print()`` after Live is stopped.

    Handles terminal resizes by:
    - Detecting resize via SIGWINCH signal (Unix) for instant notification.
    - Faster polling on Windows (25ms) to compensate for lack of signal.
    - Pre/post dimension check catches resizes during panel building.
    - When resize detected:
      1. Set flag immediately to block all renders
      2. Wait briefly for terminal to stabilize (settle delay)
      3. Stop Live (may partially fail during resize)
      4. Force-clear any remaining ghost artifacts (platform-appropriate)
      5. Set cooldown to prevent renders during resize
    - During cooldown: skip all renders (code paths check _live is None).
    - After cooldown: create fresh Live lazily on next render attempt.
    - This ensures NO printing happens during resize.
    """

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

        # Terminal resize tracking
        self._last_known_width: int = 0
        self._last_known_height: int = 0
        # Timestamp until which we should wait before resuming
        self._resize_cooldown_until: float = 0.0
        # Flag indicating we're in "resize pause" state
        self._resize_in_progress: bool = False
        # Flag set by SIGWINCH handler - checked at start of every render
        self._sigwinch_received: bool = False
        # Store original SIGWINCH handler to restore later
        self._original_sigwinch_handler: Any = None
        
        _debug_log("StreamingResponseDisplay initialized")

    # ------------------------------------------------------------------
    # SIGWINCH Signal Handling (Unix only)
    # ------------------------------------------------------------------

    def _install_sigwinch_handler(self) -> None:
        """Install SIGWINCH signal handler for instant resize detection.
        
        On Unix systems, the terminal sends SIGWINCH when resized.
        We register a handler to set a flag immediately, reducing
        the resize detection latency from ~50ms (polling) to near-zero.
        
        On Windows/non-Unix systems, this is a no-op and we rely on polling.
        """
        if not hasattr(signal, 'SIGWINCH'):
            _debug_log("_install_sigwinch_handler: SIGWINCH not available (non-Unix)")
            return
        
        try:
            self._original_sigwinch_handler = signal.signal(
                signal.SIGWINCH,
                self._on_sigwinch
            )
            _debug_log("_install_sigwinch_handler: handler installed")
        except (OSError, ValueError) as e:
            # Can fail if not running in main thread or signal not available
            _debug_log(f"_install_sigwinch_handler: failed to install: {e}")
            self._original_sigwinch_handler = None

    def _uninstall_sigwinch_handler(self) -> None:
        """Restore the original SIGWINCH handler."""
        if not hasattr(signal, 'SIGWINCH'):
            return
        
        if self._original_sigwinch_handler is not None:
            try:
                signal.signal(signal.SIGWINCH, self._original_sigwinch_handler)
                _debug_log("_uninstall_sigwinch_handler: original handler restored")
            except (OSError, ValueError) as e:
                _debug_log(f"_uninstall_sigwinch_handler: failed to restore: {e}")
            self._original_sigwinch_handler = None

    def _on_sigwinch(self, signum: int, frame: Any) -> None:
        """Signal handler called immediately when terminal is resized.
        
        This runs in the context of the signal, so we do minimal work:
        just set a flag. The actual resize handling happens in the
        render path when it checks this flag.
        
        IMPORTANT: Signal handlers should be reentrant and do minimal work.
        We only set a boolean flag here - no locks, no I/O.
        """
        # Set flag atomically - no lock needed for simple bool assignment
        self._sigwinch_received = True
        _debug_log("_on_sigwinch: SIGWINCH received, flag set")

    def _check_sigwinch_flag(self) -> bool:
        """Check and clear the SIGWINCH flag.
        
        Returns:
            True if SIGWINCH was received since last check, False otherwise.
        """
        if self._sigwinch_received:
            self._sigwinch_received = False
            _debug_log("_check_sigwinch_flag: flag was set, cleared")
            return True
        return False

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
    # Resize handling - Wait and Resume approach
    # ------------------------------------------------------------------

    def _detect_resize(self) -> bool:
        """Detect if terminal has been resized by polling width and height.

        Returns:
            True if a resize was detected, False otherwise.
        """
        current_width = self._console.size.width
        current_height = self._console.size.height

        if current_width != self._last_known_width or current_height != self._last_known_height:
            _debug_log(f"_detect_resize: size changed from ({self._last_known_width}x{self._last_known_height}) to ({current_width}x{current_height})")
            self._last_known_width = current_width
            self._last_known_height = current_height
            return True

        return False

    def _get_current_dimensions(self) -> Tuple[int, int]:
        """Get fresh terminal dimensions.
        
        Returns:
            (width, height) tuple
        """
        return (self._console.size.width, self._console.size.height)

    def _clear_potential_ghosts(self) -> None:
        """Clear terminal region where ghost boxes might appear.
        
        Uses platform-appropriate clearing strategy:
        - Unix: More aggressive clearing (25 lines) due to SIGWINCH timing
        - Windows: Gentler clearing (10 lines) with minimal cursor movement
        
        Called after Live.stop() during resize handling to clean up any
        artifacts that Rich's stop() didn't properly erase.
        
        ANSI codes used:
        - \033[J  : Clear from cursor to end of screen (ED - Erase Display)
        - \033[K  : Clear from cursor to end of line (EL - Erase Line)
        - \033[A  : Move cursor up one line (CUU - Cursor Up)
        - \033[B  : Move cursor down one line (CUD - Cursor Down)
        - \033[G  : Move cursor to column 1 (CHA - Cursor Horizontal Absolute)
        """
        lines_to_clear = _get_ghost_clear_lines()
        _debug_log(f"_clear_potential_ghosts: clearing {lines_to_clear} lines (windows={_is_windows()})")
        
        try:
            output = self._console.file
            terminal_height = self._console.size.height
            lines_to_clear = min(lines_to_clear, terminal_height)
            
            # Step 1: Clear from current position to end of screen
            # This catches any ghosts BELOW the cursor
            output.write("\033[J")
            
            # Step 2: Move UP and clear each line
            # This catches ghosts ABOVE the cursor
            for i in range(lines_to_clear):
                output.write("\033[A")   # Move up
                output.write("\033[G")   # Move to column 1
                output.write("\033[K")   # Clear entire line
            
            # Step 3: Clear from here to end of screen again
            output.write("\033[J")
            
            # Step 4: Move cursor back down - minimal movement to avoid blank gap
            # On Windows, we want very little gap. On Unix, we can afford more.
            if _is_windows():
                # Windows: move down just 1-2 lines to minimize blank space
                down_moves = min(2, lines_to_clear)
            else:
                # Unix: move down a bit more since we cleared more
                down_moves = min(3, lines_to_clear // 4)
            
            for _ in range(down_moves):
                output.write("\033[B")   # Move down
            
            output.write("\033[G")       # Ensure we're at column 1
            output.flush()
            
            _debug_log(f"_clear_potential_ghosts: completed, moved down {down_moves} lines")
            
        except Exception as e:
            # Best effort - don't crash on write errors
            _debug_log(f"_clear_potential_ghosts: failed with {e}")

    def _handle_resize_start(self) -> None:
        """Handle resize detection with robust cleanup.
        
        Strategy:
        1. Set resize flag immediately to block all renders
        2. Wait briefly for terminal to stabilize (reduces chance of
           stop() operating on stale dimensions)
        3. Stop Live (may partially fail during resize)
        4. Force-clear any remaining ghost artifacts (platform-appropriate)
        5. Set cooldown to prevent renders during resize
        
        Called when resize is first detected. Does NOT create a new Live.
        The Live will be created lazily when we're ready to render again
        (after cooldown).
        """
        _debug_log("_handle_resize_start: beginning resize handling")
        
        # Step 1: Set resize flag IMMEDIATELY to block renders
        # This is the first thing we do to minimize the window for bad renders
        self._resize_in_progress = True
        self._resize_cooldown_until = time.time() + _RESIZE_COOLDOWN_SECS
        
        # Step 2: Brief wait for terminal to settle
        # This gives the terminal emulator time to finish its resize operation,
        # reducing the chance of stop() operating on stale dimensions
        time.sleep(_RESIZE_SETTLE_DELAY)
        _debug_log(f"_handle_resize_start: waited {_RESIZE_SETTLE_DELAY}s for terminal to settle")
        
        # Step 3: Stop Live
        had_live = self._live is not None
        if self._live:
            try:
                self._live.stop()
                _debug_log("_handle_resize_start: Live stopped")
            except Exception as e:
                _debug_log(f"_handle_resize_start: error stopping Live: {e}")
            finally:
                self._live = None  # Important: set to None so no renders happen
            
            # Step 4: Force-clear ghost region (only if we had a Live)
            # Rich's stop() may have left artifacts if terminal was mid-resize
            self._clear_potential_ghosts()
        else:
            _debug_log("_handle_resize_start: no Live to stop, skipping ghost clear")
        
        # Update size tracking to current dimensions
        self._last_known_width = self._console.size.width
        self._last_known_height = self._console.size.height
        
        _debug_log(f"_handle_resize_start: cooldown set for {_RESIZE_COOLDOWN_SECS}s, resize handling complete")

    def _can_resume_after_resize(self) -> bool:
        """Check if we can resume streaming after resize cooldown.
        
        This method ONLY checks conditions - it does NOT create a Live.
        Live creation happens lazily in _ensure_live().
        
        Returns:
            True if cooldown expired and no new resize, False if still waiting.
        """
        if not self._resize_in_progress:
            return True  # No resize in progress, all good
        
        now = time.time()
        
        # Check if still in cooldown
        if now < self._resize_cooldown_until:
            # Check for another resize during cooldown - extend if so
            if self._detect_resize():
                self._resize_cooldown_until = now + _RESIZE_COOLDOWN_SECS
                _debug_log("_can_resume_after_resize: resize continued, extending cooldown")
            _debug_log(f"_can_resume_after_resize: still in cooldown, {self._resize_cooldown_until - now:.3f}s remaining")
            return False
        
        # Cooldown expired - check one more time for resize
        if self._detect_resize():
            self._resize_cooldown_until = now + _RESIZE_COOLDOWN_SECS
            _debug_log("_can_resume_after_resize: late resize detected, extending cooldown")
            return False
        
        # All clear - can resume (but don't create Live here)
        _debug_log("_can_resume_after_resize: cooldown complete, ready to resume")
        self._resize_in_progress = False
        return True

    def _ensure_live(self) -> bool:
        """Ensure we have an active Live instance, creating one if needed.
        
        This is the ONLY place where Live is created after resize.
        This ensures we don't create Live during resize cooldown.
        
        Returns:
            True if Live is ready, False if we can't create one yet.
        """
        if self._live is not None:
            return True
        
        # Can't create Live during resize
        if self._resize_in_progress:
            _debug_log("_ensure_live: can't create Live, resize in progress")
            return False
        
        # Create fresh Live - no initial content render
        _debug_log("_ensure_live: creating new Live instance")
        self._live = Live(
            _create_response_panel("", use_markdown=False),  # Empty panel
            console=self._console,
            auto_refresh=False,
            transient=True,
        )
        self._live.start()
        _debug_log("_ensure_live: new Live started")
        return True

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

        Handles terminal resize by:
        1. Checking SIGWINCH flag first (instant detection on Unix)
        2. Checking resize via polling as fallback
        3. Pre/post dimension check around panel building
        4. Skipping all renders during cooldown
        5. Creating fresh Live lazily after cooldown
        
        GUARANTEE: This method will NOT print anything if resize is in progress
        or if we're in cooldown. The only printing happens via self._live.refresh()
        which requires self._live to be non-None.

        Args:
            use_markdown: Render content as Markdown.
            show_stall: Show the stall indicator.
            streaming: Use composite streaming Markdown renderer.
            force: Bypass the throttle.
        """
        with self._lock:
            # Step 0: Check SIGWINCH flag FIRST (instant resize detection)
            # This is checked before anything else to minimize bad renders
            if self._check_sigwinch_flag():
                _debug_log("_refresh_display: SIGWINCH flag set, triggering resize handling")
                if not self._resize_in_progress:
                    self._handle_resize_start()
                return  # EXIT: resize handling started
            
            # Step 1: Check if we're in resize cooldown
            if self._resize_in_progress:
                if not self._can_resume_after_resize():
                    _debug_log("_refresh_display: SKIP - resize cooldown active")
                    return  # EXIT: no printing during cooldown
                # Cooldown complete, _resize_in_progress is now False
                _debug_log("_refresh_display: resize cooldown complete")
            
            # Step 2: Check for NEW resize via polling (fallback for non-Unix)
            if self._live is not None and self._detect_resize():
                _debug_log("_refresh_display: NEW resize detected via polling, stopping Live")
                self._handle_resize_start()
                return  # EXIT: no printing, just stopped Live
            
            # Step 3: Ensure we have a Live instance
            if not self._ensure_live():
                _debug_log("_refresh_display: SKIP - no Live instance available")
                return  # EXIT: can't create Live yet (shouldn't happen here)
            
            # Step 4: Check SIGWINCH again after ensuring Live
            if self._check_sigwinch_flag():
                _debug_log("_refresh_display: late SIGWINCH after ensure_live, aborting render")
                self._handle_resize_start()
                return
            
            # Step 5: Throttle check
            now = time.time()
            if not force and (now - self._last_render_time) < self._min_render_interval:
                _debug_log(f"_refresh_display: SKIP - throttled (elapsed={now - self._last_render_time:.3f}s)")
                return

            # Step 6: SNAPSHOT DIMENSIONS BEFORE BUILDING PANEL
            # This is critical for catching resizes that happen during panel creation
            pre_render_dims = self._get_current_dimensions()
            _debug_log(f"_refresh_display: pre-render dimensions: {pre_render_dims}")

            # Step 7: Prepare content
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

            # Step 8: Build the panel (this takes time and resize can happen here)
            panel = _create_response_panel(
                content,
                use_markdown=use_markdown,
                show_stall_indicator=show_stall,
                streaming=streaming,
                is_truncated=is_truncated,
            )

            # Step 9: POST-BUILD DIMENSION CHECK
            # If dimensions changed while building the panel, abort!
            # This catches resizes that slipped through during panel creation
            post_render_dims = self._get_current_dimensions()
            if pre_render_dims != post_render_dims:
                _debug_log(f"_refresh_display: ABORT - dimensions changed during panel build: {pre_render_dims} -> {post_render_dims}")
                self._handle_resize_start()
                return  # EXIT: don't commit the stale render

            # Step 10: Final SIGWINCH check right before committing render
            if self._check_sigwinch_flag():
                _debug_log("_refresh_display: final SIGWINCH check caught resize, aborting")
                self._handle_resize_start()
                return

            # Step 11: Actually render - this is the ONLY place we print
            _debug_log(f"_refresh_display: RENDERING via live.update()/refresh() - content_len={len(content)}")

            self._live.update(panel)
            self._live.refresh()
            self._last_render_time = now
            self._showing_stall_indicator = show_stall
            
            _debug_log(f"_refresh_display: COMPLETE")

    # ------------------------------------------------------------------
    # Final render helper
    # ------------------------------------------------------------------

    def _render_final_and_stop(self) -> None:
        """Stop Live (transient erase) and print final panel to scrollback.

        Because Live uses ``transient=True``, ``stop()`` erases the live
        region from the visible terminal.  The full final response is then
        printed permanently via ``console.print()`` so it appears in the
        terminal scrollback.
        """
        _debug_log("_render_final_and_stop: STARTING")
        
        with self._lock:
            live = self._live
            final_content = self._animated_content
            
            # Clear resize state
            self._resize_in_progress = False

            if live:
                # Stop the transient Live.  This erases the live region.
                _debug_log("_render_final_and_stop: calling live.stop()")
                try:
                    live.stop()
                except Exception as e:
                    _debug_log(f"_render_final_and_stop: live.stop() raised: {e}")
                self._live = None

            self._started = False

        # Print the full final response permanently to scrollback.
        if final_content:
            _debug_log(f"_render_final_and_stop: printing final panel - content_len={len(final_content)}")
            final_panel = _create_response_panel(
                final_content,
                use_markdown=True,
                show_stall_indicator=False,
                streaming=False,
                is_truncated=False,
            )
            self._console.print(final_panel)
        else:
            _debug_log("_render_final_and_stop: no final content to print")

        _debug_log("_render_final_and_stop: printing blank line")
        self._console.print()
        
        _debug_log("_render_final_and_stop: COMPLETE")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the live display."""
        with self._lock:
            if self._started:
                _debug_log("start: SKIP - already started")
                return

            _debug_log("start: STARTING")

            # Initialize resize tracking to current terminal dimensions
            self._last_known_width = self._console.size.width
            self._last_known_height = self._console.size.height
            self._resize_in_progress = False
            self._resize_cooldown_until = 0.0
            self._sigwinch_received = False
            
            _debug_log(f"start: initial size = ({self._last_known_width}x{self._last_known_height})")

            # Install SIGWINCH handler for instant resize detection (Unix only)
            self._install_sigwinch_handler()

            _debug_log("start: printing initial blank line")
            self._console.print()
            
            _debug_log("start: creating Live instance")
            self._live = Live(
                _create_response_panel("", use_markdown=False),
                console=self._console,
                auto_refresh=False,
                transient=True,
            )
            
            _debug_log("start: calling live.start()")
            self._live.start()

            self._started = True
            self._last_receive_time = time.time()

            self._stop_monitoring.clear()
            self._stall_monitor_thread = threading.Thread(
                target=self._monitor_stall, daemon=True,
            )
            self._stall_monitor_thread.start()
            
            _debug_log("start: COMPLETE")

    def _monitor_stall(self) -> None:
        """Monitor for stalls and terminal resize.
        
        This thread runs at platform-appropriate intervals:
        - Unix: 50ms (SIGWINCH provides instant resize notification)
        - Windows: 25ms (faster polling compensates for lack of signal)
        
        Performs two functions:
        1. Detect terminal resize (fallback for non-Unix or missed SIGWINCH)
        2. Show/hide stall indicator when LLM response is delayed
        
        Resize detection here complements SIGWINCH signal handling,
        ensuring we catch resizes even on systems without SIGWINCH.
        """
        poll_interval = _get_poll_interval()
        _debug_log(f"_monitor_stall: using poll interval {poll_interval}s")
        
        while not self._stop_monitoring.is_set():
            if self._stop_monitoring.wait(poll_interval):
                break

            with self._lock:
                # --------------------------------------------------------
                # SIGWINCH CHECK FIRST - instant resize detection
                # --------------------------------------------------------
                if self._started and self._check_sigwinch_flag():
                    _debug_log("_monitor_stall: SIGWINCH flag set, triggering handle")
                    if not self._resize_in_progress:
                        self._handle_resize_start()
                    continue  # Skip stall check this cycle
                
                # --------------------------------------------------------
                # POLLING RESIZE CHECK - fallback for non-Unix
                # --------------------------------------------------------
                if self._started and not self._resize_in_progress:
                    # Only check for new resize if we're not already handling one
                    if self._live is not None and self._detect_resize():
                        _debug_log("_monitor_stall: resize detected via polling, triggering handle")
                        self._handle_resize_start()
                        continue  # Skip stall check this cycle
                
                # If in resize cooldown, check if we can resume
                # (This advances the cooldown state even when not rendering)
                if self._resize_in_progress:
                    self._can_resume_after_resize()
                    continue  # Skip stall check during resize handling
                
                # --------------------------------------------------------
                # STALL INDICATOR LOGIC
                # --------------------------------------------------------
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
                    _debug_log(f"_monitor_stall: stall indicator changed to {should_show_stall}")
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
            # Allow animation to continue even during resize (content accumulates)
            # But don't return early if _live is None - we still accumulate content
            if not self._started:
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
            _debug_log(f"update: is_final=True, calling _render_final_and_stop()")
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
        _debug_log("stop: STARTING")
        
        self._stop_monitoring.set()
        if self._stall_monitor_thread and self._stall_monitor_thread.is_alive():
            self._stall_monitor_thread.join(timeout=1.0)
        self._stall_monitor_thread = None

        # Uninstall SIGWINCH handler
        self._uninstall_sigwinch_handler()

        with self._lock:
            # Check if we need to render final content
            has_content = bool(self._animated_content)
            is_started = self._started
            has_live = self._live is not None
            in_resize = self._resize_in_progress

        if not has_live and not in_resize:
            _debug_log("stop: no live instance and not in resize")
            # Still render final if we have content
            if has_content and is_started:
                self._render_final_and_stop()
            return

        self._render_final_and_stop()
        
        _debug_log("stop: COMPLETE")

    def is_active(self) -> bool:
        return self._started and (self._live is not None or self._resize_in_progress)

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

"""Shell output capture and auto-attach for failing commands.

Captures stdout/stderr from shell commands and attaches
the output to the next LLM prompt (one-shot semantics).

Configuration:
    shellcap=none (default): Do not capture any shell output
    shellcap=fail: Only capture output from failing commands
    shellcap=all: Capture output from all shell commands

Usage:
    - Call capture_shell_result() after every shell command execution.
    - Call maybe_attach_shell_result() before every invoke_llm() call.
"""

import os
from datetime import datetime
from typing import Any, Optional, Tuple

from rich import print as rprint

from aye.model.auth import get_user_config


# Config key for shell capture mode
SHELLCAP_KEY = "shellcap"


def is_capture_disabled() -> bool:
    """Check if shell capture is completely disabled.

    Returns:
        True if capture is disabled ('none'), False otherwise.
    """
    value = get_user_config(SHELLCAP_KEY, "none")
    return str(value).lower() in ("none", "off", "disabled", "0", "false")


def is_capture_all_enabled() -> bool:
    """Check if capture-all mode is enabled (capture all shell output, not just failures).

    When enabled, output from all shell commands is captured and attached
    to the next LLM prompt, regardless of exit code.

    Can be set via:
    - Environment variable: AYE_SHELLCAP=all
    - Config file (~/.ayecfg): shellcap=all

    Values:
    - 'none' (default): Do not capture any shell output
    - 'fail': Only capture failing commands
    - 'all': Capture all commands

    Returns:
        True if capture-all mode is enabled, False otherwise (default)
    """
    value = get_user_config(SHELLCAP_KEY, "none")
    return str(value).lower() in ("all", "always", "on", "true", "1")


def truncate_output(text: str, max_lines: int = 200) -> Tuple[str, bool]:
    """Truncate text to the last *max_lines* lines.

    Returns:
        Tuple of (truncated_text, was_truncated).
        Keeps the tail (last *max_lines* lines).
        Prepends a truncation marker if truncated.
    """
    if not text:
        return text, False

    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return text, False

    truncated = lines[-max_lines:]
    marker = f"...[truncated: showing last {max_lines} lines]\n"
    return marker + "".join(truncated), True


def _trim_front_bytes(text: str, target_bytes: int) -> str:
    """Trim *text* from the front so the result fits within *target_bytes* (UTF-8).

    Returns the tail of the string that fits within the byte budget.
    """
    if target_bytes <= 0:
        return ""

    encoded = text.encode("utf-8")
    if len(encoded) <= target_bytes:
        return text

    # Take the last target_bytes bytes, then decode safely to avoid
    # splitting a multi-byte character.
    trimmed = encoded[-target_bytes:]
    return trimmed.decode("utf-8", errors="ignore")


def enforce_byte_limit(
    stdout: str, stderr: str, max_bytes: int = 10240
) -> Tuple[str, str]:
    """Ensure the combined UTF-8 byte size of *stdout* + *stderr* <= *max_bytes*.

    Trims the longer of the two from the front first.
    Tiebreaker: when both are equal length, trim stdout first.

    Returns:
        Tuple of (stdout, stderr) trimmed to fit.
    """
    stdout_bytes = len(stdout.encode("utf-8"))
    stderr_bytes = len(stderr.encode("utf-8"))

    combined = stdout_bytes + stderr_bytes
    if combined <= max_bytes:
        return stdout, stderr

    excess = combined - max_bytes

    if stderr_bytes > stdout_bytes:
        # Trim stderr (the longer one) from the front first
        stderr = _trim_front_bytes(stderr, stderr_bytes - excess)
        # Re-check in case rounding / multi-byte chars left us over budget
        combined = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        if combined > max_bytes:
            remaining = max_bytes - len(stderr.encode("utf-8"))
            stdout = _trim_front_bytes(stdout, max(0, remaining))
    else:
        # Trim stdout first (also handles the tie case)
        stdout = _trim_front_bytes(stdout, stdout_bytes - excess)
        combined = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        if combined > max_bytes:
            remaining = max_bytes - len(stdout.encode("utf-8"))
            stderr = _trim_front_bytes(stderr, max(0, remaining))

    return stdout, stderr


def capture_shell_result(
    conf: Any, *, cmd: str, shell_response: Optional[dict]
) -> None:
    """Capture a shell command's output for auto-attach to the next LLM prompt.

    Stores the result on ``conf._last_shell_result`` and arms
    ``conf._pending_shell_attach = True`` when capture conditions are met.

    Capture behavior is controlled by the ``shellcap`` config setting:
    - ``shellcap=none`` (default): Do not capture any shell output
    - ``shellcap=fail``: Only capture failing commands
    - ``shellcap=all``: Capture all commands regardless of exit code

    Does nothing for:
    - ``None`` responses (defensive guard -- plugin didn't handle the command).
    - Interactive commands (``message`` key present without ``stdout``).
    - When shellcap is set to 'none' (default).
    - Successful commands when ``shellcap=fail``.

    Args:
        conf: Session configuration object (supports dynamic attributes).
        cmd: The shell command string as entered by the user.
        shell_response: The dict returned by the shell executor plugin.
    """
    # Check if capture is disabled entirely (shellcap=none)
    if is_capture_disabled():
        return

    # Defensive guard: None means the plugin didn't handle the command
    if shell_response is None:
        return

    # Interactive command guard.
    # Interactive commands (vim, top, etc.) go through os.system() and return
    # {"message": ..., "exit_code": ...} -- note "exit_code", NOT "returncode".
    # Their output cannot be reliably captured so we skip them entirely,
    # even when exit_code != 0.
    if "message" in shell_response and "stdout" not in shell_response:
        return

    stdout = shell_response.get("stdout", "") or ""
    stderr = shell_response.get("stderr", "") or ""
    returncode = shell_response.get("returncode")
    error = shell_response.get("error")

    # Determine failure: either a non-zero return code or an error message
    failed = (returncode is not None and returncode != 0) or (
        error is not None and bool(error)
    )

    # Check capture mode
    capture_all = is_capture_all_enabled()

    # Skip successful commands unless capture-all is enabled
    if not failed and not capture_all:
        return

    # Skip if there's no output to capture
    if not stdout.strip() and not stderr.strip() and not error:
        return

    # --- Tier 1: line truncation (tail 200 lines) ---
    stdout_truncated, stdout_was_truncated = truncate_output(stdout)
    stderr_truncated, stderr_was_truncated = truncate_output(stderr)

    # --- Tier 2: byte-size limit (10 KB combined) ---
    stdout_final, stderr_final = enforce_byte_limit(
        stdout_truncated, stderr_truncated
    )

    truncated = stdout_was_truncated or stderr_was_truncated
    # Mark truncated if byte limit trimmed further
    if stdout_final != stdout_truncated or stderr_final != stderr_truncated:
        truncated = True

    conf._last_shell_result = {
        "cmd": cmd,
        "cwd": os.getcwd(),
        "returncode": returncode,
        "stdout": stdout_final,
        "stderr": stderr_final,
        "timestamp": datetime.now().isoformat(),
        "truncated": truncated,
        "failed": failed,
    }
    conf._pending_shell_attach = True

    # Only print the capture notice in verbose mode
    if getattr(conf, "verbose", False):
        if failed:
            rprint(
                "[dim](Captured failing command output; will attach to next AI prompt)[/dim]"
            )
        else:
            rprint(
                "[dim](Captured command output; will attach to next AI prompt)[/dim]"
            )


def maybe_attach_shell_result(conf: Any, prompt: str) -> str:
    """Attach pending shell output to an LLM prompt (one-shot).

    If there is a pending shell result, appends a structured block to *prompt*
    and **immediately clears** the pending flag so subsequent LLM calls do not
    re-attach.

    Args:
        conf: Session configuration object.
        prompt: The user's prompt text.

    Returns:
        The original prompt (unchanged) when nothing is pending, or the
        augmented prompt with the captured output appended.
    """
    if not getattr(conf, "_pending_shell_attach", False):
        return prompt

    result = getattr(conf, "_last_shell_result", None)
    if result is None:
        return prompt

    # Clear immediately -- one-shot semantics
    conf._pending_shell_attach = False

    cmd = result.get("cmd", "unknown")
    cwd = result.get("cwd", "unknown")
    returncode = result.get("returncode", "unknown")
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    failed = result.get("failed", True)

    # Adjust header based on whether it was a failure or just captured output
    if failed:
        header = "Captured output from last failing command:"
    else:
        header = "Captured output from last command:"

    parts = [
        prompt,
        "",
        "---",
        header,
        f"$ {cmd}",
        f"cwd: {cwd}",
        f"exit_code: {returncode}",
    ]

    if stdout and stdout.strip():
        parts.append("")
        parts.append("STDOUT:")
        parts.append(stdout)

    if stderr and stderr.strip():
        parts.append("")
        parts.append("STDERR:")
        parts.append(stderr)

    parts.append("---")

    return "\n".join(parts)

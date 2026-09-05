"""Auto-test loop: generate tests for generated code, run them, iterate to green.

Requested feature ("auto-test loop! When code is generated - put it into a
sandbox (temp folder), generate tests automatically, and run them then
iterate on code until tests are passing"), built on aye's existing pieces:

1. After the assistant writes code files, one extra LLM round writes pytest
   tests for the changes (returned through the normal file-writing path).
2. The tests run via ``python -m pytest`` from the project root.
3. Failures go back to the model as a repair round; the corrected files are
   written and the tests re-run, until green or the repair budget is spent.

Sandboxing: a temp-folder copy cannot resolve a real project's imports and
environment, so the loop runs in place and leans on aye's native sandbox --
every write goes through ``apply_updates()`` and is snapshotted, so
``restore`` reverts any broken intermediate state. The loop is opt-in:
``auto_test = on`` in ~/.ayecfg or ``AYE_AUTO_TEST=on``.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from aye.model.auth import get_user_config
from aye.model.tools import ToolError, run_test_command
from aye.controller.llm_handler import process_llm_response
from aye.controller.llm_invoker import invoke_llm

# Per-file and total clipping for prompt embedding; a repair round gets a
# larger slice of the pytest output because that is the signal to act on.
_CLIP_FILE_CHARS = 4_000
_CLIP_TOTAL_CHARS = 24_000
_CLIP_OUTPUT_CHARS = 6_000
_CLIP_PROMPT_CHARS = 300

_DEFAULT_MAX_ROUNDS = 3


@dataclass
class AutoTestResult:
    """Outcome of one auto-test loop.

    Attributes:
        ok: True when the tests ultimately passed.
        rounds: Number of pytest runs performed (1 = passed immediately).
        test_files: Test files the loop is tracking, relative to the root.
        last_output: The final pytest output, for callers that want detail.
    """

    ok: bool
    rounds: int
    test_files: List[str]
    last_output: str = ""


def auto_test_enabled() -> bool:
    """Return True when the auto-test loop is switched on.

    Reads the ``auto_test`` config key, or ``AYE_AUTO_TEST``. Off by default:
    the loop costs extra LLM rounds and runs freshly generated code.
    """
    raw = os.environ.get("AYE_AUTO_TEST") or get_user_config("auto_test", "off")
    return str(raw).strip().lower() in {"on", "1", "true", "yes"}


def auto_test_max_rounds() -> int:
    """Return the repair-round budget (``auto_test_max_rounds`` / env)."""
    raw = os.environ.get("AYE_AUTO_TEST_MAX_ROUNDS") or get_user_config(
        "auto_test_max_rounds", str(_DEFAULT_MAX_ROUNDS)
    )
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ROUNDS
    return max(1, min(value, 10))


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (clipped)"


def _render_changed(changed_files: List[Dict[str, Any]]) -> str:
    """Render the just-written files for prompt embedding, size-capped."""
    parts: List[str] = []
    total = 0
    for index, entry in enumerate(changed_files):
        name = str(entry.get("file_name", "?"))
        content = str(entry.get("file_content", ""))
        rendered = f"### {name}\n```\n{_clip(content, _CLIP_FILE_CHARS)}\n```\n"
        total += len(rendered)
        if total > _CLIP_TOTAL_CHARS:
            parts.append(f"### {name}\n(content omitted for length)\n")
            if index > 0:
                parts.append("(further files omitted for length)")
                break
            continue
        parts.append(rendered)
    return "\n".join(parts).strip()


def _test_paths(files: List[Dict[str, Any]], root: Path) -> List[str]:
    """Normalize written file names to root-relative posix paths."""
    root = root.resolve()
    paths: List[str] = []
    for entry in files or []:
        raw = str(entry.get("file_name", "") or "").strip()
        if not raw:
            continue
        candidate = Path(raw)
        try:
            resolved = (
                candidate if candidate.is_absolute() else root / candidate
            ).resolve()
            rel = resolved.relative_to(root).as_posix()
        except ValueError:
            rel = candidate.as_posix()
        if rel not in paths:
            paths.append(rel)
    return paths


def _pytest_command(test_files: List[str]) -> str:
    quoted = " ".join(f'"{name}"' for name in test_files)
    return f'"{sys.executable}" -m pytest {quoted} -q'


def run_auto_test_loop(
    changed_files: List[Dict[str, Any]],
    original_prompt: str,
    conf: Any,
    console: Console,
    plugin_manager: Any,
    chat_id: Optional[int] = None,
    max_rounds: Optional[int] = None,
) -> Optional[AutoTestResult]:
    """Generate tests for just-written code, run them, and iterate to green.

    Args:
        changed_files: The files the assistant just wrote (name + content
            dicts, as carried by :class:`LLMResponse.updated_files`).
        original_prompt: The user's request, for test-intent context.
        conf: Config object exposing ``root``, ``selected_model``, ``verbose``.
        console: Rich console for progress and result messages.
        plugin_manager: Plugin manager passed through to :func:`invoke_llm`.
        chat_id: Chat id to continue the conversation in.
        max_rounds: Repair-round budget; defaults to :func:`auto_test_max_rounds`.

    Returns:
        The loop outcome, or None when the loop was skipped (no changed
        files, pytest unavailable, or the model produced no tests). Errors
        never propagate: they degrade to a skip or a failed result so the
        chat session continues.
    """
    root = Path(getattr(conf, "root", None) or Path.cwd())
    changed = [f for f in (changed_files or []) if f.get("file_name")]
    if not changed:
        return None

    budget = auto_test_max_rounds() if max_rounds is None else max(1, max_rounds)

    try:
        probe_code, _ = run_test_command(
            f'"{sys.executable}" -m pytest --version', root
        )
    except ToolError as exc:
        console.print(f"[yellow]Auto-test skipped: {exc}[/]")
        return None
    if probe_code != 0:
        console.print(
            "[yellow]Auto-test skipped: pytest is not available for this "
            "Python (install it with `pip install pytest`).[/]"
        )
        return None

    current_chat = chat_id

    def call_llm(prompt: str):
        nonlocal current_chat
        response = invoke_llm(
            prompt=prompt,
            conf=conf,
            console=console,
            plugin_manager=plugin_manager,
            chat_id=current_chat,
            verbose=bool(getattr(conf, "verbose", False)),
        )
        if response.chat_id is not None:
            current_chat = response.chat_id
        return response

    def write(response, prompt: str) -> None:
        new_id = process_llm_response(
            response=response, conf=conf, console=console, prompt=prompt
        )
        if new_id is not None:
            current_chat = new_id

    console.print("[dim]Auto-test: generating tests for the new changes...[/]")
    generate_prompt = (
        f'AUTO-TEST: the assistant just wrote these file(s) for the user '
        f'request "{_clip(original_prompt, _CLIP_PROMPT_CHARS)}":\n\n'
        f"{_render_changed(changed)}\n\n"
        "Write pytest tests for the new or changed behaviour:\n"
        "- Return ONLY test file(s) through the normal file-writing "
        "response, e.g. tests/test_<module>_auto.py. Do not modify the "
        "implementation files in this step.\n"
        "- They must pass with `python -m pytest <files> -q` from the "
        "project root, offline (no network), and clean up after themselves.\n"
        "- They run automatically right after your answer; failures come "
        "back to you to fix."
    )
    try:
        generation = call_llm(generate_prompt)
    except Exception as exc:  # noqa: BLE001 - degrade, never kill the chat
        console.print(f"[yellow]Auto-test stopped: {exc}[/]")
        return None

    test_files = _test_paths(generation.updated_files, root)
    if not test_files:
        console.print(
            "[yellow]Auto-test: the model returned no test files; skipping.[/]"
        )
        return None
    write(generation, generate_prompt)

    command = _pytest_command(test_files)
    rounds = 0
    last_output = ""
    while True:
        rounds += 1
        try:
            code, last_output = run_test_command(command, root)
        except ToolError as exc:
            console.print(f"[yellow]Auto-test stopped: {exc}[/]")
            return AutoTestResult(False, rounds, test_files, last_output)

        if code == 0:
            console.print(
                f"[green]Auto-test passed: {len(test_files)} test file(s), "
                f"{rounds} run(s).[/]"
            )
            return AutoTestResult(True, rounds, test_files, last_output)
        if rounds > budget:
            break

        console.print(
            f"[yellow]Auto-test round {rounds} failed - asking the model "
            "to fix it...[/]"
        )
        repair_prompt = (
            f"AUTO-TEST round {rounds}: the tests failed.\n\n"
            f"```\n{_clip(last_output, _CLIP_OUTPUT_CHARS)}\n```\n\n"
            f"Test files: {', '.join(test_files)}\n"
            f'Original request: {_clip(original_prompt, _CLIP_PROMPT_CHARS)}\n\n'
            "Fix the failure -- correct the implementation and/or the tests "
            "-- and return the fixed file(s) through the normal "
            "file-writing response. The tests re-run immediately after "
            "your answer."
        )
        try:
            repair = call_llm(repair_prompt)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Auto-test stopped: {exc}[/]")
            return AutoTestResult(False, rounds, test_files, last_output)
        write(repair, repair_prompt)

    console.print(
        f"[red]Auto-test still failing after {budget} repair round(s). "
        "All changes are snapshotted - `restore` reverts them if wanted.[/]"
    )
    return AutoTestResult(False, rounds, test_files, last_output)

"""Auto-test loop: generate tests for generated code, run them, iterate to green.

Requested feature ("auto-test loop! When code is generated - put it into a
sandbox (temp folder), generate tests automatically, and run them then
iterate on code until tests are passing"), built directly on the low-level
core so it stays invisible:

- It never uses the chat pipeline (no streaming bubbles, no diffs, no
  narration). Aye-chat's intent is to be less chatty; this loop is an
  internal quality tool. The user sees one spinner while tests are being
  generated / fixed, one while they run, and a single status line at the
  end ("Auto-test passed" / failed).
- Model calls go straight through :func:`cli_invoke` with the base system
  prompt; files come back in the standard ``source_files`` response field
  and are applied via :func:`apply_updates`, so every write is snapshotted.
- Tests run via ``python -m pytest`` from the project root; failures feed
  the next repair round until green or the budget is spent.

Sandboxing: a temp-folder copy cannot resolve a real project's imports and
environment, so the loop runs in place and leans on aye's native sandbox --
snapshots. ``restore`` reverts any broken intermediate state.

Opt-in: ``auto_test = on`` in ~/.ayecfg or ``AYE_AUTO_TEST=on``.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from aye.controller.llm_invoker import _parse_api_response
from aye.model.api import cli_invoke
from aye.model.auth import get_user_config
from aye.model.config import SYSTEM_PROMPT, DEFAULT_MAX_OUTPUT_TOKENS, MODELS
from aye.model.tools import ToolError, run_test_command
from aye.presenter.repl_ui import print_files_updated
from aye.presenter.ui_utils import StoppableSpinner

# Per-file and total clipping for prompt embedding; a repair round gets a
# larger slice of the pytest output because that is the signal to act on.
_CLIP_FILE_CHARS = 4_000
_CLIP_TOTAL_CHARS = 24_000
_CLIP_OUTPUT_CHARS = 6_000
_CLIP_PROMPT_CHARS = 300
_CONTEXT_CAP_CHARS = 16_000

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


def _max_output_tokens(conf: Any) -> int:
    for model in MODELS:
        if model.get("id") == getattr(conf, "selected_model", None):
            return model.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
    return DEFAULT_MAX_OUTPUT_TOKENS


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
            parts.append(f"### {name}\n(content omitted for length)")
            parts.append("(further files omitted for length)")
            break
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


def _context_snapshot(names: List[str], root: Path) -> Dict[str, str]:
    """Fresh on-disk contents of the tracked files, size-capped.

    Repair rounds see the *current* state of the code and test files this
    way, without needing tool rounds to read them back.
    """
    context: Dict[str, str] = {}
    total = 0
    for rel in names:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if total + len(text) > _CONTEXT_CAP_CHARS:
            text = text[: max(0, _CONTEXT_CAP_CHARS - total)]
            if not text:
                break
        context[rel] = text
        total += len(text)
        if total >= _CONTEXT_CAP_CHARS:
            break
    return context


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

    Quiet by design: the only user-visible output is phase spinners and one
    final status line. Skips and hard stops print a single yellow line.

    Args:
        changed_files: The files the assistant just wrote (name + content
            dicts, as carried by ``LLMResponse.updated_files``).
        original_prompt: The user's request, for test-intent context.
        conf: Config object exposing ``root``, ``selected_model``.
        console: Rich console for the spinner and status line.
        plugin_manager: Unused; kept for signature parity with the chat flow.
        chat_id: Chat id to continue the conversation in.
        max_rounds: Repair-round budget; defaults to :func:`auto_test_max_rounds`.

    Returns:
        The loop outcome, or None when the loop was skipped (no changed
        files, pytest unavailable, or the model produced no tests). Errors
        never propagate: they degrade to a skip or a failed result so the
        chat session continues.
    """
    from aye.model.snapshot import apply_updates

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

    def call_model(
        prompt: str, messages: List[str], context: Optional[Dict[str, str]]
    ) -> Tuple[Dict[str, Any], Optional[int]]:
        """One lean LLM round: spinner in, parsed response out."""
        nonlocal current_chat
        spinner = StoppableSpinner(console, messages=messages, interval=15.0)
        spinner.start()
        try:
            api_resp = cli_invoke(
                message=prompt,
                chat_id=current_chat or -1,
                source_files=context or {},
                model=conf.selected_model,
                system_prompt=SYSTEM_PROMPT,
                max_output_tokens=_max_output_tokens(conf),
                telemetry=None,
                on_stream_update=None,
                attachments=None,
            )
        finally:
            spinner.stop()
        assistant_resp, resp_chat = _parse_api_response(api_resp)
        if resp_chat is not None:
            current_chat = resp_chat
        return assistant_resp, resp_chat

    def apply(files: List[Dict[str, Any]], prompt: str) -> List[str]:
        files = [f for f in files if f.get("file_name")]
        if files:
            apply_updates(files, prompt, root=root.resolve())
            # Same one-liner the main flow prints; users liked knowing the
            # auto-test files landed ("Files updated: tests/...").
            print_files_updated(console, [str(f.get("file_name")) for f in files])
        return [str(f.get("file_name")) for f in files]

    console_print = console.print

    # --- Phase 1: generate tests for the changes --------------------------
    generate_prompt = (
        f'AUTO-TEST: the assistant just wrote these file(s) for the user '
        f'request "{_clip(original_prompt, _CLIP_PROMPT_CHARS)}":\n\n'
        f"{_render_changed(changed)}\n\n"
        "Write pytest tests for the new or changed behaviour:\n"
        "- Return ONLY test file(s) in `source_files`, e.g. "
        "tests/test_<module>_auto.py. Do not modify the implementation "
        "files in this step.\n"
        "- They must pass with `python -m pytest <files> -q` from the "
        "project root, offline (no network), and clean up after themselves.\n"
        "- They run automatically right after your answer; failures come "
        "back to you to fix.\n"
        "Keep `answer_summary` empty or to a single short line."
    )
    try:
        generation, _ = call_model(generate_prompt, ["generating tests..."], None)
    except Exception as exc:  # noqa: BLE001 - degrade, never kill the chat
        console_print(f"[yellow]Auto-test stopped: {exc}[/]")
        return None

    written = apply(generation.get("source_files", []), generate_prompt)
    test_files = _test_paths(generation.get("source_files", []), root)
    if not test_files:
        console_print(
            "[yellow]Auto-test: no test files were generated; skipping.[/]"
        )
        return None

    # --- Phase 2: run, repair, repeat --------------------------------------
    tracked = sorted({*test_files, *[str(f.get("file_name")) for f in changed]})
    command = _pytest_command(test_files)
    rounds = 0
    last_output = ""
    while True:
        rounds += 1
        spinner = StoppableSpinner(
            console, messages=["running tests..."], interval=15.0
        )
        spinner.start()
        try:
            code, last_output = run_test_command(command, root)
        except ToolError as exc:
            spinner.stop()
            console_print(f"[yellow]Auto-test stopped: {exc}[/]")
            return AutoTestResult(False, rounds, test_files, last_output)
        spinner.stop()

        if code == 0:
            console_print(f"[green]Auto-test passed ({rounds} run(s)).[/]")
            return AutoTestResult(True, rounds, test_files, last_output)
        if rounds > budget:
            break

        repair_prompt = (
            f"AUTO-TEST round {rounds}: the tests failed.\n\n"
            f"```\n{_clip(last_output, _CLIP_OUTPUT_CHARS)}\n```\n\n"
            f"Test files: {', '.join(test_files)}\n"
            f'Original request: {_clip(original_prompt, _CLIP_PROMPT_CHARS)}\n\n'
            "Current contents of the affected files:\n\n"
            f"{_render_changed([{'file_name': k, 'file_content': v} for k, v in _context_snapshot(tracked, root).items()])}\n\n"
            "Fix the failure -- correct the implementation and/or the tests "
            "-- and return every fixed file in `source_files`. The tests "
            "re-run immediately after your answer. Keep `answer_summary` "
            "empty or to a single short line."
        )
        try:
            repair, _ = call_model(
                repair_prompt, ["fixing code and tests..."], None
            )
        except Exception as exc:  # noqa: BLE001
            console_print(f"[yellow]Auto-test stopped: {exc}[/]")
            return AutoTestResult(False, rounds, test_files, last_output)
        apply(repair.get("source_files", []), repair_prompt)

    console_print(
        f"[red]Auto-test failed after {budget} repair round(s); all changes "
        "are snapshotted - `restore` reverts them if wanted.[/]"
    )
    return AutoTestResult(False, rounds, test_files, last_output)



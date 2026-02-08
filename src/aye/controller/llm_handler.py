from pathlib import Path
from typing import Any, Optional, List

from rich import print as rprint
from rich.console import Console
from rich.padding import Padding

from aye.presenter.repl_ui import (
    print_assistant_response,
    print_no_files_changed,
    print_files_updated,
    print_error,
    set_last_assistant_response,
)
from aye.presenter import diff_presenter
from aye.model.snapshot import apply_updates, get_diff_base_for_file
from aye.model.file_processor import filter_unchanged_files, make_paths_relative
from aye.model.models import LLMResponse
from aye.model.auth import get_user_config
from aye.model.autodiff_config import is_autodiff_enabled
from aye.model.write_validator import (
    check_files_against_ignore_patterns,
    is_strict_mode_enabled,
    format_ignored_files_warning,
)


_HAS_USED_RESTORE_KEY = "has_used_restore"


def _has_used_restore_globally() -> bool:
    return get_user_config(_HAS_USED_RESTORE_KEY, "off").lower() == "on"


def _maybe_print_restore_tip(conf: Any, console: Console) -> None:
    """Print a one-time (per session) hint about undo/restore."""
    if getattr(conf, "_restore_tip_shown", False):
        return

    if _has_used_restore_globally():
        return

    conf._restore_tip_shown = True

    msg = (
        "[bright_black]By the way: if you don't like the results, you can roll back instantly "
        "with `restore` command.[/]"
    )
    console.print(Padding(msg, (0, 4, 0, 4)))


def _run_autodiff(updated_files: List[dict], batch_id: str, conf: Any, console: Console) -> None:
    """Display diffs for all updated files against their snapshot versions."""
    verbose = getattr(conf, 'verbose', False)
    debug = get_user_config("debug", "off").lower() == "on"

    console.print(Padding("[dim]───── Auto-diff (autodiff=on) ─────[/]", (1, 0, 0, 0)))

    for item in updated_files:
        file_name = item.get("file_name")
        if not file_name:
            continue

        file_path = Path(file_name)

        diff_base = get_diff_base_for_file(batch_id, file_path)

        if diff_base is None:
            if verbose or debug:
                rprint(f"[yellow]Warning: Could not find snapshot for {file_name}, skipping autodiff[/]")
            continue

        snapshot_ref, is_git_ref = diff_base

        console.print(f"\n[bold cyan]{file_name}[/]")

        try:
            diff_presenter.show_diff(file_path, snapshot_ref, is_stash_ref=is_git_ref)
        except Exception as e:
            if verbose or debug:
                rprint(f"[yellow]Warning: Could not show diff for {file_name}: {e}[/]")

    console.print(Padding("[dim]───── End auto-diff ─────[/]", (1, 0, 0, 0)))


def process_llm_response(
    response: LLMResponse,
    conf: Any,
    console: Console,
    prompt: str,
    chat_id_file: Optional[Path] = None
) -> Optional[int]:
    """Unified handler for LLM responses from any source (API or local model)."""
    new_chat_id = None
    if response.chat_id is not None and chat_id_file:
        new_chat_id = response.chat_id
        chat_id_file.parent.mkdir(parents=True, exist_ok=True)
        chat_id_file.write_text(str(new_chat_id), encoding="utf-8")

    # Always capture the summary for `raw` / `printraw` when present.
    # (Even if it was already printed via streaming UI.)
    if response.summary and response.summary.strip():
        set_last_assistant_response(response.summary)

        # Only print if it was not already rendered by streaming UI.
        if not getattr(response, "summary_already_printed", False):
            print_assistant_response(response.summary)

    updated_files = response.updated_files

    updated_files = filter_unchanged_files(updated_files)
    updated_files = make_paths_relative(updated_files, conf.root)

    if not updated_files:
        print_no_files_changed(console)
    else:
        root_path = Path(conf.root) if hasattr(conf, 'root') else Path.cwd()
        allowed_files, ignored_files = check_files_against_ignore_patterns(
            updated_files, root_path
        )

        strict_mode = is_strict_mode_enabled()
        if ignored_files:
            warning_msg = format_ignored_files_warning(ignored_files, strict_mode)
            console.print(Padding(warning_msg, (1, 4, 0, 4)))

            if strict_mode:
                updated_files = allowed_files

        if not updated_files:
            print_no_files_changed(console)
        else:
            try:
                batch_id = apply_updates(updated_files, prompt)
                file_names = [item.get("file_name") for item in updated_files if "file_name" in item]
                if file_names:
                    print_files_updated(console, file_names)
                    _maybe_print_restore_tip(conf, console)

                    if is_autodiff_enabled():
                        _run_autodiff(updated_files, batch_id, conf, console)

            except Exception as e:
                rprint(f"[red]Error applying updates:[/] {e}")

    return new_chat_id


def handle_llm_error(exc: Exception) -> None:
    """Unified error handler for LLM invocation errors."""
    import traceback

    if hasattr(exc, "response") and getattr(exc.response, "status_code", None) == 403:
        traceback.print_exc()
        print_error(
            Exception(
                "[red]❌ Unauthorized:[/] the stored token is invalid or missing.\n"
                "Log in again with `aye auth login` or set a valid "
                "`AYE_TOKEN` environment variable.\n"
                "Obtain your personal access token at https://ayechat.ai"
            )
        )
    else:
        print_error(exc)

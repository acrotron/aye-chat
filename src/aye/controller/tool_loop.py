"""Agentic tool loop: execute tool calls and continue the conversation.

When the model answers with a tool-call JSON in ``answer_summary``, this module
runs the requested tools and sends the results back to the API as an ordinary
follow-up user message. In ``default`` permission mode shell commands are
confirmed with the user first (Enter to run, Esc to skip). The loop repeats
until the model answers in prose or the round budget is exhausted.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from aye.controller.approval import confirm_command
from aye.model.api import cli_invoke
from aye.model.tool_protocol import (
    ToolCall,
    build_tools_prompt,
    format_tool_results,
    parse_tool_calls,
    summary_with_tool_calls,
)
from aye.model.tools import build_registry, execute_tool, needs_confirmation
from aye.presenter.tool_presenter import (
    SHELL_TOOL_NAMES,
    present_tool_call,
    present_tool_result,
)

# Upper bound on tool rounds per user request, so a stubborn model cannot spin
# forever. The last round's system prompt forbids further calls explicitly.
MAX_TOOL_ROUNDS = 5

_DECLINED_SHELL_OUTPUT = "Error: the user declined to run this command."

# Sent when the model replied with a placeholder ("Let me investigate...")
# instead of invoking a tool. One nudged round; if the model still refuses to
# call tools, the loop breaks and its next reply is shown as the answer.
_STUB_NUDGE = (
    "You replied with a placeholder instead of doing the work. The user asked "
    "you to investigate and fix a problem in this project. Use the available "
    "tools now (read, grep, glob, web_search, bash/cmd) to complete the task, "
    "then answer directly.\n\n"
    "Original request: {prompt}"
)


def _round_system_prompt(base_system_prompt: str, is_final_round: bool) -> str:
    """Append the tools block (with round flags) to the base system prompt."""
    return base_system_prompt + build_tools_prompt(
        list(build_registry().values()), is_final_round=is_final_round
    )


def _execute(call: ToolCall, root: Path, console: Optional[Console] = None) -> str:
    """Run one tool call, applying the permission gates.

    Args:
        call: The requested tool call.
        root: Project root for path resolution.
        console: Rich console for the confirmation panel.

    Returns:
        The tool output text, or a decline message when the user skipped a
        shell command.
    """
    registry = build_registry()

    if needs_confirmation(call.name, registry):
        command = str(call.arguments.get("command", "") or "")
        if not confirm_command(command, console=console):
            return _DECLINED_SHELL_OUTPUT

    return execute_tool(call.name, call.arguments, root)


def run_tool_loop(
    initial_summary: str,
    updated_files: List[Dict[str, Any]],
    chat_id: Optional[int],
    prompt: str,
    conf: Any,
    base_system_prompt: str,
    source_files: Dict[str, str],
    max_output_tokens: int,
    verbose: bool = False,
    attachments: Optional[List[Dict[str, Any]]] = None,
    console: Optional[Console] = None,
    stub_retry: bool = False,
) -> Tuple[str, List[Dict[str, Any]], Optional[int]]:
    """Run tool rounds until the model answers in prose or the budget runs out.

    Args:
        initial_summary: The model's first ``answer_summary`` (a tool-call JSON).
        updated_files: Files the model wants written, accumulated so far.
        chat_id: Chat id to continue in.
        prompt: The user's original request.
        conf: Config object exposing ``root`` and ``selected_model``.
        base_system_prompt: System prompt without the tools block.
        source_files: Project context files to resend each round.
        max_output_tokens: Output token budget per round.
        verbose: Print extra activity detail (currently unused; kept for parity).
        attachments: Image attachments to forward each round.
        console: Rich console used for the shell confirmation panel.
        stub_retry: When True, the initial reply was a placeholder promising
            investigation; send one nudge to force real tool usage first.

    Returns:
        ``(final_summary, merged_updated_files, chat_id)``.
    """
    from aye.controller.llm_invoker import _parse_api_response

    summary = initial_summary
    files = list(updated_files)
    results: List[tuple] = []
    root = Path(getattr(conf, "root", None) or Path.cwd())
    console = console if console is not None else Console()

    for round_index in range(MAX_TOOL_ROUNDS):
        # Round 0 with stub_retry: no tool calls to run yet, just nudge.
        if round_index == 0 and stub_retry:
            followup = _STUB_NUDGE.format(prompt=prompt)
        else:
            calls = parse_tool_calls(summary)
            if not calls:
                break
            for call in calls:
                if call.name in SHELL_TOOL_NAMES:
                    present_tool_call(call, console)
                output = _execute(call, root, console=console)
                results.append((call, output))
                present_tool_result(call, output, console)
            followup = format_tool_results(prompt, results)

        is_final_round = round_index + 1 == MAX_TOOL_ROUNDS
        system = _round_system_prompt(base_system_prompt, is_final_round)

        api_resp = cli_invoke(
            chat_id=chat_id,
            message=followup,
            source_files=source_files,
            model=conf.selected_model,
            system_prompt=system,
            max_output_tokens=max_output_tokens,
            telemetry=None,
            on_stream_update=None,
            attachments=attachments,
        )
        assistant_resp, chat_id = _parse_api_response(api_resp)
        summary = summary_with_tool_calls(
            assistant_resp.get("answer_summary", ""),
            assistant_resp.get("tool_call") or assistant_resp.get("tool_calls"),
        )
        files.extend(assistant_resp.get("source_files", []))

    # Deduplicate files by name, keeping the last occurrence.
    merged: List[Dict[str, Any]] = []
    seen_names = set()
    for entry in files:
        name = entry.get("file_name")
        if name in seen_names:
            continue
        seen_names.add(name)
        merged.append(entry)

    return summary, merged, chat_id
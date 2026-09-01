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
from rich.text import Text

from aye.controller.approval import confirm_command
from aye.model.api import cli_invoke
from aye.model.tool_protocol import (
    ToolCall,
    build_tools_prompt,
    format_tool_results,
    split_summary,
    strip_tool_protocol,
    summary_with_tool_calls,
)
from aye.model.tools import build_registry, execute_tool, needs_confirmation
from aye.presenter.streaming_ui import StreamingResponseDisplay, create_streaming_callback
from aye.presenter.tool_presenter import SHELL_TOOL_NAMES, ToolSession
from aye.presenter.ui_utils import StoppableSpinner, DEFAULT_THINKING_MESSAGES
from aye.model.auth import get_user_config

# Tool rounds are UNLIMITED by default: agent work (explore, read, test,
# fix) has no predictable size and a fixed budget kept cutting long tasks
# short. The user can still impose a cap via the `max_tool_rounds` config
# key / AYE_MAX_TOOL_ROUNDS, and Ctrl+C always interrupts the loop.
#
# What unlimited must not mean is spinning on nothing: _MAX_STALLED_ROUNDS
# consecutive rounds in which every requested call was an exact repeat (no
# new output produced) force a final prose answer instead.

# Consecutive all-replay rounds tolerated before the loop declares the
# model stuck and forces it to answer with what it has.
_MAX_STALLED_ROUNDS = 3

_DECLINED_SHELL_OUTPUT = "Error: the user declined to run this command."

# Tools that may change files on disk. After one of them runs, earlier tool
# outputs can be stale (a read no longer reflects the file), so the loop drops
# its remembered results and re-executes repeated calls instead of replaying
# outdated content back to the model.
_MUTATING_TOOLS = frozenset({"bash", "cmd", "write"})

# Returned instead of re-running a call the model already made this request.
# parse_tool_calls() only deduplicates within a single round, so a model that
# loses track across rounds would otherwise re-read the same file repeatedly,
# burning the round budget. Replaying the earlier output is both cheaper and a
# clearer signal than silently running it again.
_REPEATED_CALL_NOTE = (
    "You already called this tool with these exact arguments earlier in this "
    "request. The previous result is repeated below; do not request it again.\n\n"
)

# Appended to the follow-up when the model requested more tools than the
# per-round cap runs. Without it the model waits for results that never come
# and re-requests the dropped calls forever.
_DROPPED_CALLS_NOTE = (
    "Only the first {executed} of your {requested} tool requests were run this "
    "turn; the rest were dropped. Re-request the dropped ones now, or continue "
    "with what you have."
)


def _call_key(call: ToolCall) -> tuple:
    """Return a hashable identity for *call* (name plus normalized arguments)."""
    try:
        return (call.name, json.dumps(call.arguments, sort_keys=True))
    except (TypeError, ValueError):
        return (call.name, str(call.arguments))

# Sent when the model replied with a placeholder ("Let me investigate...")
# instead of invoking a tool. One nudged round; if the model still refuses to
# call tools, the loop breaks and its next reply is shown as the answer.
_STUB_NUDGE = (
    "You replied with a placeholder instead of doing the work. The user asked "
    "you to investigate and fix a problem in this project. Use the available "
    "tools now (ls, read, grep, glob, web_search, fetch_url, shell) to "
    "complete the task, then answer directly.\n\n"
    "Original request: {prompt}"
)


def _is_verbose():
    return get_user_config("verbose", "off").lower() == "on"


def _is_debug():
    return get_user_config("debug", "off").lower() == "on"


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


def _print_narration(console: Console, narration: str) -> None:
    """Show the model's prose that accompanied a tool request.

    Rendered dim: it is running commentary, not the final answer, but hiding
    it entirely made streamed narration vanish mid-request and left the user
    staring at a spinner while tools ran.
    """
    console.print(Text(narration, style="dim"))


def _invoke_round(
    chat_id: Optional[int],
    followup: str,
    system: str,
    conf: Any,
    source_files: Dict[str, str],
    max_output_tokens: int,
    attachments: Optional[List[Dict[str, Any]]],
    console: Console,
    stream: bool,
) -> Tuple[Dict[str, Any], Optional[StreamingResponseDisplay], Dict[str, Any]]:
    """Send one round's follow-up and stream its reply like a first response.

    Returns:
        ``(api_resp, display, round_state)``. ``round_state["rendered_final"]``
        is True when the streaming callback finalized visible content for this
        response (so the caller must not print the summary again).
    """
    round_state: Dict[str, Any] = {}
    display: Optional[StreamingResponseDisplay] = None
    callback = None
    spinner = StoppableSpinner(
        console, messages=DEFAULT_THINKING_MESSAGES, interval=15.0
    )

    if stream:
        display = StreamingResponseDisplay(on_first_content=spinner.stop)
        callback = create_streaming_callback(display, state=round_state)

    spinner.start()
    try:
        api_resp = cli_invoke(
            chat_id=chat_id,
            message=followup,
            source_files=source_files,
            model=conf.selected_model,
            system_prompt=system,
            max_output_tokens=max_output_tokens,
            telemetry=None,
            on_stream_update=callback,
            attachments=attachments,
        )
    finally:
        spinner.stop()
        if display is not None and display.is_active():
            # The backend skipped the final render (structured tool round);
            # rescue any narration still sitting in the live frame instead of
            # letting it vanish with the frame.
            narration = strip_tool_protocol(display.content)
            if narration.strip():
                display.update(narration, is_final=True)
                round_state["rendered_final"] = True
            else:
                display.discard()

    return api_resp, display, round_state


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
    max_rounds: Optional[int] = None,
    stream: bool = False,
    initial_narration_shown: bool = False,
    initial_narration: str = "",
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]], Optional[int]]:
    """Run tool rounds until the model answers in prose or the budget runs out.

    The model may narrate around its tool requests ("I'll check the tests,
    then fix them" + JSON); that narration is shown to the user and echoed
    back with the results so the model does not repeat it, which used to look
    like the model "keeps invoking the same tools".

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
        max_rounds: Optional tool round cap. None (the default) means no
            limit: the loop runs until the model answers in prose, gets
            interrupted with Ctrl+C, or trips the all-replay stall guard.
        stream: Stream each round's reply through a live panel, like the first
            response; narration is finalized into a bubble, tool JSON is not.
        initial_narration_shown: Round 1's narration was already rendered by
            the caller's streaming display (suppress the dim echo).
        initial_narration: Round 1's prose when its tool request arrived as a
            structured field (which replaces, and so hides, the prose).
        state: Optional out-dict; ``state["summary_already_printed"]`` is set
            when the final answer was rendered by this loop's stream.

    Returns:
        ``(final_summary, merged_updated_files, chat_id)``.
    """
    from aye.controller.llm_invoker import _parse_api_response

    summary = initial_summary
    files = list(updated_files)
    root = Path(getattr(conf, "root", None) or Path.cwd())
    console = console if console is not None else Console()
    state = state if state is not None else {}

    # Outputs of calls already executed this request, keyed by name+arguments.
    # parse_tool_calls() deduplicates within a round; this carries across them.
    seen_calls: Dict[tuple, str] = {}

    # Whether the summary currently held was already rendered by a stream.
    narration_shown = initial_narration_shown
    # Prose hidden inside a structured-field round: summary_with_tool_calls()
    # replaces the answer text with JSON, so the prose is recovered here and
    # acked with the next follow-up instead of being lost.
    pending_narration = initial_narration
    # Consecutive rounds that produced no new tool output (every call was a
    # replay). Without a round budget this is the only spinning risk left.
    stalled_rounds = 0
    budget_exhausted = False

    round_index = 0
    while True:
        if max_rounds is not None and round_index >= max_rounds:
            budget_exhausted = True
            break

        # Create a new session for this round to print immediately
        session = ToolSession()

        # Round 0 with stub_retry: no tool calls to run yet, just nudge.
        if round_index == 0 and stub_retry:
            followup = _STUB_NUDGE.format(prompt=prompt)
        else:
            parsed = split_summary(summary)
            if not parsed.calls:
                break

            narration = parsed.narration or pending_narration
            pending_narration = ""

            # Show the prose that travelled with the request (streaming
            # already rendered it; otherwise echo it dim so it is not lost).
            if narration and not narration_shown:
                _print_narration(console, narration)

            round_results: List[tuple] = []
            ran_mutating = False
            executed_any_new = False
            for call in parsed.calls:
                key = _call_key(call)
                if key in seen_calls:
                    # Replay the earlier result rather than running it again.
                    output = _REPEATED_CALL_NOTE + seen_calls[key]
                else:
                    output = _execute(call, root, console=console)
                    seen_calls[key] = output
                    executed_any_new = True
                    if call.name in _MUTATING_TOOLS:
                        ran_mutating = True
                round_results.append((call, output))
                if call.name in SHELL_TOOL_NAMES:
                    session.add_shell_result(call, output)
                else:
                    session.add_call_line(call, output)

            # A command may have changed the files; remembered outputs are
            # potentially stale, so repeated calls must run again.
            if ran_mutating:
                seen_calls.clear()

            # No new output this round means the model is re-requesting work
            # it already has. Give it a few chances to self-correct, then
            # force a prose answer rather than loop forever.
            stalled_rounds = 0 if executed_any_new else stalled_rounds + 1
            if stalled_rounds >= _MAX_STALLED_ROUNDS:
                break

            parts = []
            if narration:
                # Echo the narration so the model knows it was delivered and
                # does not re-emit it with the next request.
                parts.append(
                    "Your note before these tool calls was shown to the user. "
                    "Do not repeat it:\n" + narration
                )
                parts.append("")
            dropped = parsed.requested - len(parsed.calls)
            if dropped > 0:
                parts.append(
                    _DROPPED_CALLS_NOTE.format(
                        executed=len(parsed.calls), requested=parsed.requested
                    )
                )
                parts.append("")
            parts.append(format_tool_results(prompt, round_results))
            followup = "\n".join(parts)

        # Render tool activity for this round immediately, before the spinner.
        # Verbose shows just the call lines ("that a tool ran"); the full shell
        # output is diagnostic and stays on debug.
        if not session.is_empty():
            if _is_debug():
                session.render(console)
            elif _is_verbose():
                session.render_call_lines(console)

        is_final_round = (
            max_rounds is not None and round_index + 1 == max_rounds
        )
        system = _round_system_prompt(base_system_prompt, is_final_round)

        api_resp, display, round_state = _invoke_round(
            chat_id=chat_id,
            followup=followup,
            system=system,
            conf=conf,
            source_files=source_files,
            max_output_tokens=max_output_tokens,
            attachments=attachments,
            console=console,
            stream=stream,
        )
        state["summary_already_printed"] = bool(round_state.get("rendered_final"))
        narration_shown = bool(
            display is not None and display.has_received_content()
        )

        assistant_resp, chat_id = _parse_api_response(api_resp)
        answer = assistant_resp.get("answer_summary", "")
        structured = assistant_resp.get("tool_call") or assistant_resp.get("tool_calls")
        summary = summary_with_tool_calls(answer, structured)
        if structured and summary != answer:
            # The structured field won, hiding the prose it arrived with.
            pending_narration = strip_tool_protocol(answer).strip()
        files.extend(assistant_resp.get("source_files", []))

        round_index += 1

    # The loop ended while the model still requested tools -- either a user
    # configured cap ran out, or the model kept re-requesting calls it had
    # already made. Never return the raw tool-call JSON as the answer: force
    # one final prose round with the tools block removed so the model must
    # answer directly.
    if split_summary(summary).calls:
        reason = (
            "The tool call limit for this request was reached."
            if budget_exhausted
            else "You have requested the same tool calls several times and "
            "their results are already in the conversation."
        )
        followup = (
            f"{reason} Give your final answer now in prose. Do not request "
            "any more tools."
        )
        api_resp, display, round_state = _invoke_round(
            chat_id=chat_id,
            followup=followup,
            system=base_system_prompt,
            conf=conf,
            source_files=source_files,
            max_output_tokens=max_output_tokens,
            attachments=attachments,
            console=console,
            stream=stream,
        )
        state["summary_already_printed"] = bool(round_state.get("rendered_final"))

        assistant_resp, chat_id = _parse_api_response(api_resp)
        # Tools were not offered this round, so a structured tool-call field
        # (if the backend attaches one anyway) is spurious; keep the prose.
        summary = assistant_resp.get("answer_summary", "")
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

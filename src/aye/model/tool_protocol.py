"""Wire protocol for LLM-initiated tool calls.

The hosted API pins the response body to ``answer_summary`` and
``source_files``; extra top-level fields are stripped (verified against the
live endpoint). So a tool request travels *inside* ``answer_summary``: the
model sets it to a bare JSON object and nothing else.

    {"tool_calls": [{"name": "grep", "arguments": {"pattern": "def foo"}}]}

``parse_tool_calls`` recognizes that shape and returns the requests; anything
else is treated as a normal prose answer. When the backend whitelists a real
``tool_calls`` field, only ``parse_tool_calls`` needs to change.

The return leg needs no protocol support: results are sent back as an ordinary
user message built by ``format_tool_results``.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from aye.model.tools import ToolSpec

# Cap on how many calls are honored from a single model turn. Bounds the work
# one response can trigger while still allowing useful parallelism.
MAX_CALLS_PER_ROUND = 4

# Strips a ```json ... ``` fence, which models add despite instructions.
_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation requested by the model."""
    name: str
    arguments: Dict[str, Any]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_tools_prompt(specs: List[ToolSpec], is_final_round: bool = False) -> str:
    """Describe the available tools and the request format for the system prompt.

    Args:
        specs: Tools to offer this turn.
        is_final_round: When True, forbids further tool requests so the loop
            cannot run past its budget.

    Returns:
        A prompt block, or an empty string when no tools are offered.
    """
    if not specs:
        return ""

    lines = [
        "\n\n--- TOOLS",
        "",
        "You are an agentic coding assistant working in a terminal. "
        "Investigate before you answer: when the request touches this "
        "codebase, find the relevant files with glob/grep, read them, and "
        "run tests or builds with bash/cmd when useful. Do not answer from "
        "memory when the code is right here; only answer directly for "
        "general knowledge that needs no tool.",
        "",
        "Available tools:",
    ]

    for spec in specs:
        lines.append(f"  {spec.name} - {spec.description}")
        for param, note in spec.parameters.items():
            marker = "required" if param in spec.required else "optional"
            lines.append(f"      {param} ({marker}): {note}")

    lines += [
        "",
        "To call tools, set `answer_summary` to EXACTLY this JSON and nothing "
        "else. No prose, no explanation, no code fence:",
        "",
        '    {"tool_calls": [{"name": "<tool>", "arguments": {...}}]}',
        "",
        "Rules:",
        f"- Request at most {MAX_CALLS_PER_ROUND} tools at once. Independent "
        "lookups should go in one request rather than separate rounds.",
        "- Leave `source_files` empty when requesting tools.",
        "- Results are returned to you, and you may then answer or request more.",
        "- Do not request the same tool with the same arguments twice.",
        "- Do not request a tool for something already in the prompt, or for "
        "general knowledge you have.",
        "- When in doubt, use a tool. Reading is cheap; guessing is not.",
        "",
        "Notes:",
        "- `web_search` is available, but it defaults to DuckDuckGo (no API "
        "key) and can fail or return nothing. Never invent results or URLs; "
        "if the search errors or comes back empty, say so plainly.",
        "- The project is your only source of truth; ground every answer in "
        "what the tools actually returned, not in memory.",
        "- Shell output is truncated for display, so prefer commands with "
        "focused output (e.g. `pytest tests/test_x.py -q`) over dumping a "
        "whole suite or log.",
        "- Shell commands run from the project root; paths are relative to it.",
        "- Writes snapshot the previous file state automatically and you can "
        "edit freely, but read a file before rewriting it and always write "
        "the complete new file, never a diff.",
        "",
        "When you have what you need, answer normally in prose.",
        "Answer directly and concisely: give what the user asked for and stop. "
        "Do not repeat the tool results back, do not summarize or analyze the "
        "project unless the user asked for it, and do not pad the answer with "
        "checklists, tutorials, or setup instructions.",
        "--- END TOOLS",
    ]

    if is_final_round:
        lines.append(
            "\nIMPORTANT: You have reached the tool call limit for this "
            "request. Answer now with the information you have. Do not "
            "request more tools; if something could not be determined, say so."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_tool_calls(summary: Optional[str]) -> List[ToolCall]:
    """Extract tool calls from a model response.

    Args:
        summary: The model's ``answer_summary``.

    Returns:
        Requested calls in order, deduplicated and capped. Empty when the
        response is a normal prose answer.
    """
    if not summary or not summary.strip():
        return []

    text = summary.strip()

    # Cheap reject: a prose answer will not start with a brace, and stripping
    # a fence only matters when it wraps a JSON object.
    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group("body").strip()
    if not text.startswith("{"):
        # Last resort: models sometimes wrap the JSON in a sentence or two.
        # Extraction can only succeed when the object carries tool_calls,
        # so a code snippet containing braces cannot be misread as a call.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return []
        text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(payload, dict):
        return []

    raw = payload.get("tool_calls")

    # Tolerate the singular form; models reach for it despite the schema.
    if raw is None:
        single = payload.get("tool_call")
        if single is None:
            return []
        raw = [single]

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    calls: List[ToolCall] = []
    seen = set()

    for entry in raw:
        if not isinstance(entry, dict):
            continue

        name = str(entry.get("name", "")).strip()
        if not name:
            continue

        arguments = entry.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}

        # Identical calls in one round would duplicate work for no gain.
        try:
            key = (name, json.dumps(arguments, sort_keys=True))
        except (TypeError, ValueError):
            key = (name, str(arguments))
        if key in seen:
            continue
        seen.add(key)

        calls.append(ToolCall(name=name, arguments=arguments))
        if len(calls) >= MAX_CALLS_PER_ROUND:
            break

    return calls


def is_tool_request(summary: Optional[str]) -> bool:
    """Return True if *summary* is a tool request rather than a prose answer."""
    return bool(parse_tool_calls(summary))


# Matches placeholder replies that promise work ("Let me investigate...")
# without doing any: a short first-person intent phrase plus a work verb.
_STUB_RE = re.compile(
    r"\b(?:let(?:'s| me| us)|\bi'?ll\b|\bi will\b|\bi am going to)\b"
    r".{0,80}?\b(?:investigat\w*|look|check|start|begin|search|examine|explore|dive)\b",
    re.IGNORECASE,
)

# The things a model asks the user for instead of fetching them itself.
_DEFLECT_OBJECT = (
    r"(?:tools?|glob|grep|read|files?|repo(?:sitory)?|code|contents?|"
    r"structure|director(?:y|ies)|folders?|outputs?|results?)"
)

# Matches replies that refuse to act and ask the user to supply tool output.
# The model *has* tools, so "please run glob" or "I don't have the file
# contents yet" is a failure to call them, not a real answer. These read as
# complete sentences (never as a tool-call JSON), so without this check they
# are printed to the user as the final answer.
_DEFLECTION_RE = re.compile(
    r"(?:"
    # "please run the glob tool", "could you share the file contents"
    r"\b(?:please|could you|can you|would you|kindly)\b.{0,80}?"
    r"\b(?:run|use|call|invoke|provide|share|paste|attach|send|give|show)\b"
    r".{0,40}?" + _DEFLECT_OBJECT +
    r"|"
    # "I need you to run glob", "you'll have to provide the files"
    r"\b(?:i need you to|you (?:need|have) to|you'?ll (?:need|have) to)\b.{0,40}?"
    r"\b(?:run|use|call|invoke|provide|share|paste|attach|send|give|show)\b"
    r".{0,40}?" + _DEFLECT_OBJECT +
    r"|"
    # "I don't have the repository tool output yet", "I can't see the files"
    r"\bi\b.{0,20}?\b(?:do not|don'?t|cannot|can'?t|haven'?t|have not)\b"
    r".{0,40}?\b(?:have|see|access|been given|received)\b.{0,40}?" + _DEFLECT_OBJECT +
    r"|"
    # "waiting for the tool results"
    r"\bwait(?:ing)?\s+for\b.{0,40}?" + _DEFLECT_OBJECT +
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Proactive stubs are terse; deflections spell out what they want and so run
# longer. Both caps keep a genuine prose answer from being misread as a stub.
_STUB_MAX_CHARS = 200
_DEFLECTION_MAX_CHARS = 400


def looks_like_stub(summary: Optional[str]) -> bool:
    """Return True if *summary* promises or requests tool work instead of doing it.

    Two shapes are caught, both of which mean the model has tools available
    but did not call them:

    - Proactive placeholders: "Let me investigate the codebase."
    - Deflections: "I don't have the file contents yet, please run read."

    The caller nudges the model to actually use its tools instead of showing
    either reply to the user.

    Args:
        summary: The model's ``answer_summary``.

    Returns:
        True for placeholder or deflection replies that contain no tool call.
    """
    if not summary or not summary.strip():
        return False
    text = " ".join(summary.strip().split())

    matched = (
        len(text) <= _STUB_MAX_CHARS and _STUB_RE.search(text)
    ) or (
        len(text) <= _DEFLECTION_MAX_CHARS and _DEFLECTION_RE.search(text)
    )
    if not matched:
        return False
    return not parse_tool_calls(summary)


def summary_with_tool_calls(summary: str, tool_calls: Any) -> str:
    """Prefer a structured ``tool_call`` field over a JSON-in-summary request.

    Newer backends expose tool requests as a real response field instead of
    requiring the model to stuff JSON into ``answer_summary``. Normalize both
    shapes to the internal ``{"tool_calls": [...]}`` form so the rest of the
    pipeline is agnostic.

    The structured field is only honored when it actually parses into usable
    calls; a malformed field falls back to the plain summary so raw JSON never
    reaches the UI as the answer.

    Args:
        summary: The model's ``answer_summary``.
        tool_calls: The structured ``tool_call`` / ``tool_calls`` field, if any.

    Returns:
        The summary, or the tool-call JSON when the structured field is set
        and valid.
    """
    if tool_calls:
        candidate = json.dumps({"tool_calls": tool_calls})
        if parse_tool_calls(candidate):
            return candidate
    return summary


# A protocol object appearing anywhere in the text, not only at the start.
# Models often narrate before emitting the request ("Let me look.\n{...}"),
# which a leading-brace-only check misses.
_EMBEDDED_PROTOCOL_RE = re.compile(r"\{\s*\"tool_calls?\"", re.IGNORECASE)


def looks_like_protocol_json(summary: Optional[str]) -> bool:
    """Return True for a summary that is raw tool-protocol JSON, not prose.

    A strict parse can miss malformed protocol objects (``{"tool_calls":
    "oops"}`` parses to no calls but is still not user-facing text). This
    catches such objects whether they lead the text or follow narration, so
    the caller can refuse to render them as a chat answer.

    Args:
        summary: Text to inspect.

    Returns:
        True when *summary* contains a raw tool-call protocol object.
    """
    if not summary or not summary.strip():
        return False
    text = summary.strip()
    if text.startswith("{"):
        return "tool_calls" in text or "tool_call" in text
    return bool(_EMBEDDED_PROTOCOL_RE.search(text))


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def describe_call(call: ToolCall) -> str:
    """Render a call as ``name(key=value, ...)`` for logs and prompts."""
    if not call.arguments:
        return f"{call.name}()"

    parts = []
    for key, value in call.arguments.items():
        text = str(value)
        if len(text) > 60:
            text = text[:60] + "\u2026"
        parts.append(f"{key}={text!r}")
    return f"{call.name}({', '.join(parts)})"


def format_tool_results(
    original_prompt: str,
    results: List[tuple],
) -> str:
    """Build the follow-up user message carrying tool output back to the model.

    Args:
        original_prompt: The user's original question, restated so the model
            does not lose track of it across rounds.
        results: ``(ToolCall, output)`` pairs in execution order.

    Returns:
        A prompt containing each result and the restated question.
    """
    blocks = ["Tool results:", ""]

    for call, output in results:
        blocks.append(f"### {describe_call(call)}")
        blocks.append("")
        blocks.append(str(output).rstrip() or "(no output)")
        blocks.append("")

    blocks.append("---")
    blocks.append(
        "Work autonomously now: you may use any tool as many times as needed "
        "to fully complete the original request. Do not stop after a partial "
        "result or give a provisional answer. Only once everything the user "
        "asked for is truly done, answer in plain prose with no more tool calls."
    )
    blocks.append("")
    blocks.append(f"Original request: {original_prompt}")

    return "\n".join(blocks)

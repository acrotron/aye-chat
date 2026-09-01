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
# one response can trigger while still allowing useful parallelism; anything
# past it is reported back to the model so it can re-request the remainder.
MAX_CALLS_PER_ROUND = 6

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
        "You are an autonomous coding AGENT running inside the user's "
        "terminal, on their real machine, with this project as your working "
        "directory. You are not a chat page in a browser: you act, you don't "
        "just advise. Investigate before you answer -- learn the layout with "
        "ls/glob, read the relevant files, grep for symbols, and run "
        "builds, tests, and git with the shell whenever that is more "
        "reliable than reasoning from memory. Work in as many tool rounds "
        "as the task needs: call tools, inspect the results, call again. "
        "Only answer in prose once the work is actually done, and never ask "
        "the user to run something you can run yourself.",
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
        "- `fetch_url` reads one web page as text; use it to follow up on "
        "search results or online docs instead of guessing their content.",
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

# Opening of a protocol object anywhere in the text. Models narrate before
# emitting the request ("Let me look at the files.\n{"tool_calls": ...}"),
# and that narration can itself contain braces (code snippets, f-strings),
# so a first-{ to-last-} slice misextracts. Matching the brace plus the
# "tool_calls" key keeps prose braces from being mistaken for protocol.
_PROTOCOL_OBJECT_START_RE = re.compile(r'\{\s*"tool_calls?"\s*:', re.IGNORECASE)

_JSON_DECODER = json.JSONDecoder()

# A fence-marker line immediately around a protocol object (```json ... ```).
_OPENING_FENCE_BEFORE = re.compile(
    r"(?:^|\n)([ \t]*(`{3,}|~{3,})[ \t]*\w*[ \t]*)$", re.MULTILINE
)
_CLOSING_FENCE_AFTER = re.compile(r"[ \t]*\n[ \t]*(`{3,}|~{3,})[ \t]*(?=\n|$)")


@dataclass(frozen=True)
class ParsedSummary:
    """A model response split into its prose and tool-request parts.

    Attributes:
        narration: The prose with every protocol-shaped object removed --
        parseable requests and unparseable blobs alike, so no tool JSON
        ever reaches the user. Empty when the response was a bare tool
        request.
        calls: Requested calls, deduplicated and capped.
        requested: Number of unique calls before the cap; ``requested -
            len(calls)`` is how many were silently dropped.
    """

    narration: str
    calls: List[ToolCall]
    requested: int


def _balanced_object_span(text: str, start: int) -> Optional[tuple]:
    """Extent of the brace-balanced object starting at *start*, or None.

    Used for protocol-shaped blobs that do not parse (``{"tool_calls":
    oops}``): they carry no calls, but they are still machine traffic that
    must not reach the user, so their extent is removed from the narration.
    String literals are respected so braces inside them do not count.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return (start, index + 1)
    return None


def _extract_protocol_spans(text: str) -> List[tuple]:
    """Find every protocol object in *text*.

    Args:
        text: Response text to scan.

    Returns:
        ``(start, end, payload)`` spans in document order. ``payload`` is
        the decoded object for parseable ones and ``None`` for
        protocol-shaped blobs that do not parse (removal-only spans).
    """
    spans: List[tuple] = []
    pos = 0
    while True:
        match = _PROTOCOL_OBJECT_START_RE.search(text, pos)
        if not match:
            return spans
        try:
            payload, end = _JSON_DECODER.raw_decode(text, match.start())
        except json.JSONDecodeError:
            balanced = _balanced_object_span(text, match.start())
            if balanced:
                spans.append((match.start(), balanced[1], None))
                pos = balanced[1]
            else:
                pos = match.end()
            continue
        if isinstance(payload, dict) and (
            "tool_calls" in payload or "tool_call" in payload
        ):
            spans.append((match.start(), end, payload))
            pos = end
        else:
            pos = match.end()


def _expand_span_over_fence(text: str, start: int, end: int) -> tuple:
    """Widen an object span to swallow a code fence wrapping just it.

    Models sometimes fence only the JSON (```json ... ```); removing the object
    alone would leave orphan fence markers in the narration.

    Args:
        text: The full response text.
        start: Index of the object's opening brace.
        end: Index just past the object's closing brace.

    Returns:
        The widened ``(start, end)`` span.
    """
    widened_start = start
    fence = _OPENING_FENCE_BEFORE.search(text[:start])
    if fence:
        widened_start = fence.start(1)

    widened_end = end
    closing = _CLOSING_FENCE_AFTER.match(text, end)
    if closing:
        widened_end = closing.end()

    return (widened_start, widened_end)


def _entries_from_payload(payload: Dict[str, Any]) -> List[ToolCall]:
    """Read ToolCall entries from one decoded protocol object."""
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

    entries: List[ToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        name = str(entry.get("name", "")).strip()
        if not name:
            continue

        arguments = entry.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}

        entries.append(ToolCall(name=name, arguments=arguments))
    return entries


def _tidy_narration(text: str) -> str:
    """Collapse the whitespace holes left by removed protocol objects."""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cut_trailing_partial_protocol(text: str) -> str:
    """Drop a protocol object that is truncated at the end of *text*.

    A streamed reply can end mid-object (``{"tool_calls": [{"name": "gre``);
    it will never parse into calls, but leaving the fragment in the
    narration would show raw JSON to the user. Only genuinely unbalanced
    braces are cut: complete-but-garbage snippets in prose (balanced) stay.
    """
    cut_at = None
    for match in _PROTOCOL_OBJECT_START_RE.finditer(text):
        try:
            _JSON_DECODER.raw_decode(text, match.start())
        except json.JSONDecodeError:
            cut_at = match.start()
    if cut_at is None:
        return text
    fragment = text[cut_at:]
    if fragment.count("{") > fragment.count("}"):
        return text[:cut_at].rstrip()
    return text


_DANGLING_OBJECT_RE = re.compile(r"\{[^{}]*$")


def _rstrip_dangling_object_prefix(text: str) -> str:
    """Drop a trailing unterminated object prefix that looks like JSON.

    While a protocol object streams in, its opening brace can arrive before
    the "tool_calls" key is complete (``{"to``), a window the key-anchored
    cut misses. A dangling ``{`` fragment containing a quote or colon is
    JSON starting to arrive, not prose, so it is cut as well.
    """
    match = _DANGLING_OBJECT_RE.search(text)
    if not match:
        return text
    fragment = match.group(0)
    if '"' in fragment or ":" in fragment:
        return text[: match.start()].rstrip()
    return text


def split_summary(summary: Optional[str]) -> ParsedSummary:
    """Split a response into its narration prose and requested tool calls.

    Narration and tool JSON arrive mixed in one ``answer_summary`` on
    streaming backends ("I'll check the tests.\\n{"tool_calls": ...}"), so
    the two concerns must be separable: the narration is user-facing text,
    while the objects are the machine-readable request. Every parseable
    protocol object is lifted out wherever it appears; what remains is the
    narration.

    Args:
        summary: The model's ``answer_summary``.

    Returns:
        A :class:`ParsedSummary`; ``calls`` is empty for a prose answer.
    """
    if not summary or not summary.strip():
        return ParsedSummary("", [], 0)

    text = summary.strip()

    # A fence wrapping the whole body is unwrapped up front; fences around
    # only the JSON object are consumed per-span below.
    fence = _FENCE_RE.match(text)
    if fence:
        text = fence.group("body").strip()

    entries: List[ToolCall] = []
    pieces: List[str] = []
    pos = 0
    for start, end, payload in _extract_protocol_spans(text):
        span_start, span_end = _expand_span_over_fence(text, start, end)
        if payload is not None:
            entries.extend(_entries_from_payload(payload))
        pieces.append(text[pos:span_start])
        pos = span_end
    pieces.append(text[pos:])

    narration = _tidy_narration(
        _rstrip_dangling_object_prefix(_cut_trailing_partial_protocol("".join(pieces)))
    )

    # Identical calls in one round would duplicate work for no gain.
    unique: List[ToolCall] = []
    seen = set()
    for call in entries:
        try:
            key = (call.name, json.dumps(call.arguments, sort_keys=True))
        except (TypeError, ValueError):
            key = (call.name, str(call.arguments))
        if key in seen:
            continue
        seen.add(key)
        unique.append(call)

    return ParsedSummary(
        narration=narration,
        calls=unique[:MAX_CALLS_PER_ROUND],
        requested=len(unique),
    )


def parse_tool_calls(summary: Optional[str]) -> List[ToolCall]:
    """Extract tool calls from a model response.

    Args:
        summary: The model's ``answer_summary``.

    Returns:
        Requested calls in order, deduplicated and capped. Empty when the
        response is a normal prose answer.
    """
    return split_summary(summary).calls


def strip_tool_protocol(summary: Optional[str]) -> str:
    """Return *summary* with every protocol-shaped object removed.

    The inverse of :func:`parse_tool_calls` for display: a mixed response
    ("Let me check.\\n{"tool_calls": ...}") yields "Let me check." while a
    bare tool request yields an empty string. Unparseable protocol blobs
    (``{"tool_calls": oops}``) are removed too -- they carry no calls but
    are still machine traffic the user must not see.
    """
    return split_summary(summary).narration


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

"""Tool implementations available to the LLM during a chat turn.

Each tool is a small function over the project tree. Results are returned as
plain text ready to paste back into a prompt.

Safety model
------------
Every path argument is resolved against the project root and rejected if it
escapes it, so a model cannot read ``../../.ssh/id_rsa`` by asking nicely.
Ignore patterns (``.gitignore`` / ``.ayeignore``) are honored, so files the
user already excluded from context stay excluded here too.

Read outputs are capped (bytes, match counts, result counts) because the whole
result is fed straight back into the next prompt and would otherwise be able
to blow the context budget.

``write`` routes through ``snapshot.apply_updates()``, the same path the normal
``source_files`` response uses. That means a tool-driven write is snapshotted
before it lands and is undoable with ``restore``, exactly like any other edit.
It also honors ``block_ignored_file_writes`` when strict mode is on.

``bash`` and ``cmd`` execute arbitrary commands and cannot be sandboxed in any
meaningful sense. Access is governed by a permission mode (see
``permission_mode``): the default asks the user before every command, while
``full`` runs them unattended. File tools never prompt in either mode.
"""

import fnmatch
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib import parse

import httpx

from aye.model.auth import get_user_config
from aye.model.ignore_patterns import load_ignore_patterns

# Output caps. These bound how much a single tool result can add to the prompt.
MAX_READ_BYTES = 24_000
MAX_GLOB_RESULTS = 100
MAX_GREP_MATCHES = 60
MAX_GREP_FILE_BYTES = 1_000_000   # skip files larger than this when searching
MAX_LINE_CHARS = 300

# A single tool-driven write is bounded so a runaway response cannot fill a disk.
MAX_WRITE_BYTES = 400_000

# Shell execution limits.
SHELL_TIMEOUT_SECONDS = 120
MAX_SHELL_OUTPUT_BYTES = 16_000

# ---------------------------------------------------------------------------
# Permission modes
# ---------------------------------------------------------------------------
# Two modes, both of which grant the full tool set. They differ only in whether
# shell commands are confirmed with the user:
#
#   default  read/write/grep/glob run freely; bash and cmd ask first.
#   full     everything runs unattended.
#
# File tools are unprompted in both modes because their blast radius is bounded
# (sandboxed to the project root) and writes are snapshotted, so `restore`
# undoes a bad edit. A shell command has neither property.

PERMISSION_KEY = "tool_permission"

PERMISSION_DEFAULT = "default"
PERMISSION_FULL = "full"

VALID_PERMISSIONS = (PERMISSION_DEFAULT, PERMISSION_FULL)


def permission_mode() -> str:
    """Return the active permission mode.

    Reads the ``tool_permission`` config key, or ``AYE_TOOL_PERMISSION``.
    Unrecognized values fall back to ``default``, so a typo cannot silently
    grant unattended shell access.
    """
    raw = str(get_user_config(PERMISSION_KEY, PERMISSION_DEFAULT) or "").strip().lower()
    return raw if raw in VALID_PERMISSIONS else PERMISSION_DEFAULT


class ToolError(Exception):
    """Raised when a tool cannot run. The message is shown to the model."""


@dataclass(frozen=True)
class ToolSpec:
    """Declaration of a tool, used both to run it and to describe it to the LLM.

    Attributes:
        name: Identifier the model uses in a tool call.
        description: One line telling the model when to reach for this tool.
        parameters: Mapping of parameter name to a short type/purpose note.
        required: Parameter names that must be present.
        runner: Callable invoked as ``runner(arguments, root)``.
        mutating: True if the tool changes state outside its own output.
            Callers use this to build read-only registries.
        prompts_by_default: True if this tool needs user confirmation in
            ``default`` permission mode. Ignored in ``full`` mode.
    """
    name: str
    description: str
    parameters: Dict[str, str]
    required: tuple
    runner: Callable[[Dict[str, Any], Path], str]
    mutating: bool = False
    prompts_by_default: bool = False


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _resolve_in_root(raw: str, root: Path) -> Path:
    """Resolve *raw* against *root*, refusing anything outside the tree.

    Args:
        raw: A path as supplied by the model, absolute or relative.
        root: Project root that acts as the sandbox boundary.

    Returns:
        The resolved absolute path.

    Raises:
        ToolError: If the path is empty or escapes the project root.
    """
    if not raw or not str(raw).strip():
        raise ToolError("path is required")

    root = root.resolve()
    candidate = Path(str(raw).strip())
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()

    try:
        resolved.relative_to(root)
    except ValueError:
        raise ToolError(
            f"path escapes the project root: {raw}"
        ) from None

    return resolved


def _is_ignored(path: Path, root: Path, spec) -> bool:
    """Return True if *path* is excluded by ignore rules or is dot-prefixed."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True

    if any(part.startswith(".") for part in rel.parts):
        return True

    return bool(spec.match_file(rel.as_posix()))


def _relative(path: Path, root: Path) -> str:
    """Return *path* relative to *root* as a posix string, for display."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _clip_line(line: str) -> str:
    """Trim a single output line so one long line cannot dominate a result."""
    stripped = line.rstrip("\n")
    if len(stripped) <= MAX_LINE_CHARS:
        return stripped
    return stripped[:MAX_LINE_CHARS] + "\u2026"


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def run_read(arguments: Dict[str, Any], root: Path) -> str:
    """Return the contents of one file, line-numbered and size-capped.

    Args:
        arguments: ``path`` (required), optional ``start`` and ``limit`` line
            bounds (1-based, inclusive start).
        root: Project root.

    Returns:
        Line-numbered file content.

    Raises:
        ToolError: If the file is missing, unreadable, binary, or ignored.
    """
    path = _resolve_in_root(arguments.get("path", ""), root)

    if not path.exists():
        raise ToolError(f"file not found: {_relative(path, root)}")
    if path.is_dir():
        raise ToolError(f"path is a directory, not a file: {_relative(path, root)}")

    spec = load_ignore_patterns(root)
    if _is_ignored(path, root, spec):
        raise ToolError(f"file is excluded by ignore rules: {_relative(path, root)}")

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"file is not valid UTF-8 text: {_relative(path, root)}") from None
    except OSError as exc:
        raise ToolError(f"could not read {_relative(path, root)}: {exc}") from None

    lines = text.splitlines()

    start = _coerce_int(arguments.get("start"), default=1, minimum=1)
    limit = _coerce_int(arguments.get("limit"), default=0, minimum=0)

    begin = start - 1
    selected = lines[begin:] if limit <= 0 else lines[begin:begin + limit]

    header = f"{_relative(path, root)} ({len(lines)} lines)"
    if not selected:
        return f"{header}\n(no lines in the requested range)"

    body: List[str] = []
    consumed = 0
    truncated = False
    for offset, line in enumerate(selected, start=start):
        rendered = f"{offset}: {_clip_line(line)}"
        consumed += len(rendered) + 1
        if consumed > MAX_READ_BYTES:
            truncated = True
            break
        body.append(rendered)

    if truncated:
        body.append(
            f"... truncated at {MAX_READ_BYTES} characters; "
            f"call read again with start={start + len(body)}"
        )

    return header + "\n" + "\n".join(body)


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------

def run_glob(arguments: Dict[str, Any], root: Path) -> str:
    """List project files matching a glob pattern.

    Args:
        arguments: ``pattern`` (required), e.g. ``src/**/*.py``.
        root: Project root.

    Returns:
        Newline-separated relative paths, or a not-found note.

    Raises:
        ToolError: If the pattern is missing or invalid.
    """
    pattern = str(arguments.get("pattern", "")).strip()
    if not pattern:
        raise ToolError("pattern is required")

    root = root.resolve()
    spec = load_ignore_patterns(root)

    try:
        candidates = sorted(root.glob(pattern))
    except (ValueError, IndexError) as exc:
        raise ToolError(f"invalid glob pattern {pattern!r}: {exc}") from None

    matches: List[str] = []
    for path in candidates:
        if not path.is_file() or _is_ignored(path, root, spec):
            continue
        matches.append(_relative(path, root))
        if len(matches) >= MAX_GLOB_RESULTS:
            break

    if not matches:
        return f"No files match {pattern!r}"

    header = f"{len(matches)} file{'' if len(matches) == 1 else 's'} matching {pattern!r}"
    if len(matches) >= MAX_GLOB_RESULTS:
        header += f" (capped at {MAX_GLOB_RESULTS})"
    return header + "\n" + "\n".join(matches)


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------

def run_grep(arguments: Dict[str, Any], root: Path) -> str:
    """Search file contents for a regular expression.

    Args:
        arguments: ``pattern`` (required regex), optional ``include`` glob to
            restrict which files are searched, optional ``ignore_case``.
        root: Project root.

    Returns:
        ``path:line: text`` matches, capped.

    Raises:
        ToolError: If the pattern is missing or not a valid regex.
    """
    pattern = str(arguments.get("pattern", "")).strip()
    if not pattern:
        raise ToolError("pattern is required")

    flags = re.IGNORECASE if _coerce_bool(arguments.get("ignore_case")) else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(f"invalid regular expression {pattern!r}: {exc}") from None

    include = str(arguments.get("include", "") or "").strip()

    root = root.resolve()
    spec = load_ignore_patterns(root)

    matches: List[str] = []
    files_seen = 0
    capped = False

    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_ignored(path, root, spec):
            continue

        rel = _relative(path, root)
        if include and not _matches_include(rel, include):
            continue

        try:
            if path.stat().st_size > MAX_GREP_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; skipping is the useful behavior

        files_seen += 1
        for number, line in enumerate(text.splitlines(), start=1):
            if not regex.search(line):
                continue
            matches.append(f"{rel}:{number}: {_clip_line(line).strip()}")
            if len(matches) >= MAX_GREP_MATCHES:
                capped = True
                break
        if capped:
            break

    if not matches:
        scope = f" in {include!r}" if include else ""
        return f"No matches for {pattern!r}{scope} ({files_seen} files searched)"

    header = f"{len(matches)} match{'' if len(matches) == 1 else 'es'} for {pattern!r}"
    if capped:
        header += f" (capped at {MAX_GREP_MATCHES})"
    return header + "\n" + "\n".join(matches)


def _matches_include(rel_path: str, include: str) -> bool:
    """Return True if *rel_path* satisfies an ``include`` glob.

    Bare patterns such as ``*.py`` are matched against the file name so users
    do not have to write ``**/*.py`` to search recursively.
    """
    if fnmatch.fnmatch(rel_path, include):
        return True
    if "/" not in include:
        return fnmatch.fnmatch(Path(rel_path).name, include)
    return False


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def run_write(arguments: Dict[str, Any], root: Path) -> str:
    """Write full file content, snapshotting the previous state first.

    The write is delegated to ``snapshot.apply_updates()`` so it is recorded in
    the same history as ordinary edits and can be reverted with ``restore``.

    Args:
        arguments: ``path`` and ``content`` (both required). ``content`` is the
            complete new file body; partial writes and diffs are not supported.
        root: Project root.

    Returns:
        A confirmation naming the file and its new size.

    Raises:
        ToolError: If arguments are missing, the path escapes the root, the
            content exceeds ``MAX_WRITE_BYTES``, the target is a directory, or
            strict mode blocks the write.
    """
    # Imported lazily: aye.model.snapshot pulls in backend modules, and this
    # keeps `import aye.model.tools` cheap for the read-only paths.
    from aye.model.snapshot import apply_updates
    from aye.model.write_validator import (
        check_files_against_ignore_patterns,
        is_strict_mode_enabled,
    )

    path = _resolve_in_root(arguments.get("path", ""), root)

    content = arguments.get("content")
    if content is None:
        raise ToolError("content is required (the full new file body)")
    if not isinstance(content, str):
        raise ToolError("content must be a string")

    size = len(content.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        raise ToolError(
            f"content is {size} bytes, over the {MAX_WRITE_BYTES} byte limit"
        )

    if path.is_dir():
        raise ToolError(f"path is a directory: {_relative(path, root)}")

    rel = _relative(path, root)

    # Strict mode blocks writes to files excluded from context, matching the
    # behavior of the normal source_files write path.
    if is_strict_mode_enabled():
        _, ignored = check_files_against_ignore_patterns(
            [{"file_name": rel, "file_content": content}], Path(root)
        )
        if ignored:
            raise ToolError(
                f"{rel} matches .gitignore or .ayeignore and strict mode is on"
            )

    existed = path.is_file()

    try:
        apply_updates(
            [{"file_name": rel, "file_content": content}],
            prompt=f"tool write {rel}",
            root=Path(root).resolve(),
        )
    except Exception as exc:  # noqa: BLE001 - surface as a tool error, not a crash
        raise ToolError(f"could not write {rel}: {exc}") from None

    verb = "Updated" if existed else "Created"
    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    return (
        f"{verb} {rel} ({lines} lines, {size} bytes). "
        "Previous state was snapshotted; `restore` reverts it."
    )


# ---------------------------------------------------------------------------
# bash / cmd
# ---------------------------------------------------------------------------
# Shell execution is fundamentally unsandboxable: a command can do anything the
# user can do. The safety here is procedural rather than technical -- user
# confirmation in default mode, a timeout, and an output cap -- not a claim
# that the command itself is constrained.

def _run_shell(command: str, root: Path, executable: Optional[List[str]]) -> str:
    """Execute *command* and return a combined status/stdout/stderr report.

    Args:
        command: The command line to run.
        root: Working directory for the child process.
        executable: Argv prefix that invokes the interpreter, e.g.
            ``["bash", "-c"]``. ``None`` uses the platform default shell.

    Returns:
        A text report with exit code and captured output, truncated to
        ``MAX_SHELL_OUTPUT_BYTES``.

    Raises:
        ToolError: If the command is empty, the interpreter is unavailable, or
            it exceeds the timeout.
    """
    command = str(command or "").strip()
    if not command:
        raise ToolError("command is required")

    try:
        if executable is None:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=SHELL_TIMEOUT_SECONDS,
                errors="replace",
            )
        else:
            completed = subprocess.run(
                executable + [command],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=SHELL_TIMEOUT_SECONDS,
                errors="replace",
            )
    except subprocess.TimeoutExpired:
        raise ToolError(
            f"command exceeded the {SHELL_TIMEOUT_SECONDS}s timeout and was killed"
        ) from None
    except FileNotFoundError:
        interpreter = executable[0] if executable else "shell"
        raise ToolError(f"{interpreter} is not available on this system") from None
    except OSError as exc:
        raise ToolError(f"could not run command: {exc}") from None

    return _format_shell_result(command, completed.returncode,
                               completed.stdout, completed.stderr)


def _format_shell_result(command: str, code: int, stdout: str, stderr: str) -> str:
    """Render a completed process as a compact text report for the model."""
    parts = [f"$ {command}", f"exit code: {code}"]

    for label, stream in (("stdout", stdout), ("stderr", stderr)):
        text = (stream or "").strip()
        if not text:
            continue
        if len(text) > MAX_SHELL_OUTPUT_BYTES:
            text = (
                text[:MAX_SHELL_OUTPUT_BYTES]
                + f"\n... truncated at {MAX_SHELL_OUTPUT_BYTES} characters"
            )
        parts.append(f"--- {label} ---")
        parts.append(text)

    if len(parts) == 2:
        parts.append("(no output)")

    return "\n".join(parts)


def run_bash(arguments: Dict[str, Any], root: Path) -> str:
    """Run a command through bash.

    Args:
        arguments: ``command`` (required).
        root: Working directory for the command.

    Returns:
        Exit code plus captured output.

    Raises:
        ToolError: If the command is empty, bash is missing, or it times out.
    """
    # Validate the argument before probing for the interpreter, so a blank
    # command reports the real problem rather than a missing-bash message.
    if not str(arguments.get("command", "") or "").strip():
        raise ToolError("command is required")

    bash = shutil.which("bash")
    if not bash:
        raise ToolError(
            "bash is not available on this system; use the cmd tool instead"
        )
    return _run_shell(arguments.get("command", ""), root, [bash, "-c"])


def run_cmd(arguments: Dict[str, Any], root: Path) -> str:
    """Run a command through the Windows command interpreter.

    On non-Windows platforms this falls back to the default system shell so the
    tool stays usable rather than failing on a technicality.

    Args:
        arguments: ``command`` (required).
        root: Working directory for the command.

    Returns:
        Exit code plus captured output.

    Raises:
        ToolError: If the command is empty or times out.
    """
    if platform.system() == "Windows":
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return _run_shell(arguments.get("command", ""), root, [comspec, "/c"])
    return _run_shell(arguments.get("command", ""), root, None)


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

_DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
_DDG_TITLE_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL
)
_WEB_TIMEOUT = 20.0


def _strip_html(text: str) -> str:
    """Remove tags and unescape entities from a scraped fragment."""
    return unescape(" ".join(re.sub(r"<[^>]+>", "", text).split()))


def _ddg_redirect_url(href: str) -> str:
    """Resolve DuckDuckGo's ``/l/?uddg=...`` redirect link to the real URL."""
    match = re.search(r"[?&]uddg=([^&]+)", href)
    if match:
        try:
            return parse.unquote(match.group(1))
        except ValueError:
            pass
    return href


def _format_search_results(
    provider: str,
    query: str,
    results: List[Dict[str, str]],
) -> str:
    """Render search results as a numbered block for the next prompt."""
    lines = [f"{provider} results for {query!r}:", ""]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.get('title') or '(no title)'}")
        url = result.get("url", "")
        if url:
            lines.append(f"   {url}")
        snippet = result.get("snippet", "")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _search_duckduckgo(query: str, max_results: int) -> str:
    """Search through DuckDuckGo's HTML endpoint; no API key required.

    DuckDuckGo occasionally blocks scripted requests or returns nothing, so
    callers treat an error here as a normal outcome and tell the model plainly.

    Raises:
        ToolError: If the request fails, is blocked, or has no results.
    """
    try:
        response = httpx.get(
            _DDG_SEARCH_URL,
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=_WEB_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"DuckDuckGo request failed: {exc}") from exc

    if response.status_code != 200:
        raise ToolError(f"DuckDuckGo returned HTTP {response.status_code}")

    page = response.content.decode("utf-8", errors="replace")
    titles = _DDG_TITLE_RE.findall(page)
    snippets = _DDG_SNIPPET_RE.findall(page)

    results: List[Dict[str, str]] = []
    for index, (href, raw_title) in enumerate(titles):
        if index >= max_results:
            break
        snippet = _strip_html(snippets[index]) if index < len(snippets) else ""
        results.append(
            {
                "title": _strip_html(raw_title) or "(no title)",
                "url": _ddg_redirect_url(href),
                "snippet": snippet,
            }
        )

    if not results:
        raise ToolError(
            "DuckDuckGo returned no results (blocked or nothing found)"
        )
    return _format_search_results("DuckDuckGo", query, results)


def _search_tavily(query: str, max_results: int) -> str:
    """Search through the Tavily API, which needs ``tavily_api_key``."""
    api_key = get_user_config("tavily_api_key")
    if not api_key:
        raise ToolError("tavily selected but tavily_api_key is not set in ~/.ayecfg")

    try:
        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=_WEB_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"Tavily request failed: {exc}") from exc

    if response.status_code != 200:
        raise ToolError(f"Tavily returned HTTP {response.status_code}")

    results: List[Dict[str, str]] = []
    for item in response.json().get("results", [])[:max_results]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            }
        )

    if not results:
        raise ToolError("Tavily returned no results")
    return _format_search_results("Tavily", query, results)


def _search_brave(query: str, max_results: int) -> str:
    """Search through the Brave API, which needs ``brave_api_key``."""
    api_key = get_user_config("brave_api_key")
    if not api_key:
        raise ToolError("brave selected but brave_api_key is not set in ~/.ayecfg")

    try:
        response = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
            timeout=_WEB_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"Brave request failed: {exc}") from exc

    if response.status_code != 200:
        raise ToolError(f"Brave returned HTTP {response.status_code}")

    results: List[Dict[str, str]] = []
    for item in response.json().get("web", {}).get("results", [])[:max_results]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            }
        )

    if not results:
        raise ToolError("Brave returned no results")
    return _format_search_results("Brave", query, results)


def run_web_search(arguments: Dict[str, Any], root: Path) -> str:
    """Search the web for live information outside the project tree.

    The provider is pinned with ``search_provider`` in ``~/.ayecfg``
    (or ``AYE_SEARCH_PROVIDER``): ``duckduckgo`` (default, no key),
    ``tavily``, or ``brave``.

    Args:
        arguments: ``query`` (required), optional ``max_results``.
        root: Unused; kept for the common runner signature.

    Returns:
        A numbered list of titles, URLs, and snippets.

    Raises:
        ToolError: On missing query, unknown provider, or a failed search.
    """
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise ToolError("query is required")

    max_results = _coerce_int(arguments.get("max_results"), default=5, minimum=1)
    provider = str(get_user_config("search_provider", "duckduckgo")).strip().lower()

    if provider in {"", "duckduckgo", "ddg"}:
        return _search_duckduckgo(query, max_results)
    if provider == "tavily":
        return _search_tavily(query, max_results)
    if provider == "brave":
        return _search_brave(query, max_results)
    raise ToolError(
        f"unknown search_provider {provider!r} "
        "(use duckduckgo, tavily, or brave)"
    )


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def _coerce_int(value: Any, *, default: int, minimum: int) -> int:
    """Best-effort int conversion; models sometimes send numbers as strings."""
    if value is None or value == "":
        return default
    try:
        return max(minimum, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any) -> bool:
    """Best-effort bool conversion accepting JSON and string spellings."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FILE_TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="read",
        description="Read one file from the project, with line numbers.",
        parameters={
            "path": "string - path relative to the project root",
            "start": "integer, optional - first line to return (1-based)",
            "limit": "integer, optional - how many lines to return",
        },
        required=("path",),
        runner=run_read,
    ),
    ToolSpec(
        name="glob",
        description="Find project files by glob pattern, e.g. 'src/**/*.py'.",
        parameters={"pattern": "string - glob pattern relative to the project root"},
        required=("pattern",),
        runner=run_glob,
    ),
    ToolSpec(
        name="grep",
        description="Search file contents with a regular expression.",
        parameters={
            "pattern": "string - regular expression to search for",
            "include": "string, optional - only search files matching this glob",
            "ignore_case": "boolean, optional - case-insensitive search",
        },
        required=("pattern",),
        runner=run_grep,
    ),
]

# Not offered to the model yet, deliberately.
#
# For ordinary edits `write` is redundant: files returned in the final
# response's ``source_files`` already go through apply_updates(), which reports
# the changed files, shows diffs when autodiff is on, and snapshots for
# `restore`. A second write path bypasses all of that reporting.
#
# Its real use is acting *within* one request -- write, run the tests, read the
# failure, write again -- which is the planned sandboxed test-generation flow.
# The implementation and its tests are kept intact and ready; re-enable this by
# adding WRITE_TOOL to default_specs() once the sandbox exists.
WRITE_TOOL: List[ToolSpec] = [
    ToolSpec(
        name="write",
        description=(
            "Write the full contents of one file. Read it first unless you are "
            "creating it. The previous state is snapshotted automatically."
        ),
        parameters={
            "path": "string - path relative to the project root",
            "content": "string - the complete new file body, not a diff",
        },
        required=("path", "content"),
        runner=run_write,
        mutating=True,
    ),
]

# Web search is read-only and never prompts, but it is network-bound and can
# legitimately fail (DuckDuckGo blocks scripts from time to time), so the model
# is told to report failures plainly instead of guessing.
WEB_TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="web_search",
        description=(
            "Search the web for current information. Use when an answer "
            "depends on live or external data not present in the project. "
            "Default provider is DuckDuckGo (no API key) and can fail or "
            "return no results; Tavily and Brave need keys configured by the "
            "user."
        ),
        parameters={
            "query": "string - the search query",
            "max_results": "integer, optional - how many results to return",
        },
        required=("query",),
        runner=run_web_search,
    ),
]

# Shell tools are separate only so read-only callers can exclude them by
# category. They are part of the offered tool set in both permission modes.
SHELL_TOOLS: List[ToolSpec] = [
    ToolSpec(
        name="bash",
        description=(
            "Run a shell command with bash, from the project root. Use for "
            "builds, tests, git, and other commands."
        ),
        parameters={"command": "string - the command line to run"},
        required=("command",),
        runner=run_bash,
        mutating=True,
        prompts_by_default=True,
    ),
    ToolSpec(
        name="cmd",
        description=(
            "Run a shell command with the Windows command interpreter, from "
            "the project root."
        ),
        parameters={"command": "string - the command line to run"},
        required=("command",),
        runner=run_cmd,
        mutating=True,
        prompts_by_default=True,
    ),
]

# Every tool that exists, including ones not currently offered to the model.
# Tests and read_only_registry() use this; default_specs() is what the model
# actually sees.
ALL_TOOLS: List[ToolSpec] = FILE_TOOLS + WRITE_TOOL + WEB_TOOLS + SHELL_TOOLS


def _platform_shell_tools() -> List[ToolSpec]:
    """Return the shell tool for the current platform.

    Offering both ``bash`` and ``cmd`` at once is ambiguous: a model on
    Windows does not know which one to pick and often asks for both, doubling
    confirmations. Only the interpreter that actually exists on this machine
    is offered.
    """
    if platform.system() == "Windows":
        return [spec for spec in SHELL_TOOLS if spec.name == "cmd"]
    return [spec for spec in SHELL_TOOLS if spec.name == "bash"]


def default_specs() -> List[ToolSpec]:
    """Return the tool specs offered to the model.

    Both permission modes offer the same set; they differ only in whether shell
    calls are confirmed with the user.

    ``WRITE_TOOL`` is intentionally absent: edits arrive through the final
    response's ``source_files`` instead. Add it here to re-enable the write
    tool once the sandboxed test flow needs mid-request writes.
    """
    return FILE_TOOLS + WEB_TOOLS + _platform_shell_tools()


def build_registry(specs: Optional[List[ToolSpec]] = None) -> Dict[str, ToolSpec]:
    """Return a name-keyed registry, defaulting to the full tool set."""
    return {
        spec.name: spec
        for spec in (specs if specs is not None else default_specs())
    }


def read_only_registry() -> Dict[str, ToolSpec]:
    """Return a registry containing only non-mutating tools.

    Excludes ``write`` and both shell tools. Intended for contexts that must
    not change the working tree at all, independent of permission mode.
    """
    return {spec.name: spec for spec in ALL_TOOLS if not spec.mutating}


def needs_confirmation(
    name: str,
    registry: Optional[Dict[str, ToolSpec]] = None,
    mode: Optional[str] = None,
) -> bool:
    """Return True if the caller must confirm *name* with the user first.

    Args:
        name: Tool name requested by the model.
        registry: Optional registry override.
        mode: Optional permission mode override. Defaults to the configured
            mode.

    Returns:
        True in ``default`` mode for tools flagged ``prompts_by_default``
        (i.e. ``bash`` and ``cmd``). Always False in ``full`` mode, and always
        False for unknown tools, which are rejected at dispatch instead.
    """
    effective = mode if mode in VALID_PERMISSIONS else permission_mode()
    if effective == PERMISSION_FULL:
        return False

    table = registry if registry is not None else build_registry()
    spec = table.get(str(name).strip())
    return bool(spec and spec.prompts_by_default)


def execute_tool(
    name: str,
    arguments: Dict[str, Any],
    root: Path,
    registry: Optional[Dict[str, ToolSpec]] = None,
) -> str:
    """Run a tool by name and return its textual result.

    Errors are returned as text rather than raised, because the result is fed
    back to the model: telling it *why* a call failed lets it correct itself,
    whereas an exception would abort the turn.

    Args:
        name: Tool name requested by the model.
        arguments: Arguments supplied by the model.
        root: Project root used as the sandbox boundary.
        registry: Optional registry override. Pass ``read_only_registry()`` to
            refuse mutating tools.

    Returns:
        The tool's output, or an ``Error: ...`` line.
    """
    table = registry if registry is not None else build_registry()
    spec = table.get(str(name).strip())

    if spec is None:
        available = ", ".join(sorted(table)) or "none"
        return f"Error: unknown tool {name!r}. Available tools: {available}"

    args = arguments if isinstance(arguments, dict) else {}

    # Presence only, not truthiness: an empty string is a legitimate value for
    # `write` content (an empty file). Runners reject blank paths and patterns
    # themselves, so nothing is lost by the looser check here.
    missing = [p for p in spec.required if args.get(p) is None]
    if missing:
        return f"Error: {spec.name} requires: {', '.join(missing)}"

    try:
        return spec.runner(args, Path(root))
    except ToolError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001 - never abort the turn on a tool bug
        return f"Error: {spec.name} failed unexpectedly: {exc}"

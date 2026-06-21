"""Persistent REPL command history for Aye Chat.

This module provides a small prompt_toolkit history backend that persists the
main chat REPL input across restarts.

Only the exact user-submitted REPL text is stored. Internal prompt expansions
such as source file contents, URL/plugin context, shell capture output, image
attachment data, or hidden prompt wrappers must never be written here.
"""

import json
import os
from pathlib import Path
from typing import Iterable, List

from prompt_toolkit.history import History

from aye.model.auth import get_user_config

DEFAULT_HISTORY_MAX_ENTRIES = 500
_EXIT_COMMANDS = {"exit", "quit", ":q"}


def get_repl_history_path() -> Path:
    """Return the persistent REPL history file path.

    The path can be overridden with the ``history_file`` config key, including
    via ``AYE_HISTORY_FILE`` through the existing config environment override
    mechanism. The default is ``~/.aye/history``.

    Returns:
        Path to the history file. The parent directory is created if needed.
    """
    configured = get_user_config("history_file")
    if configured:
        path = Path(str(configured)).expanduser()
    else:
        path = Path.home() / ".aye" / "history"

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def is_history_enabled() -> bool:
    """Return True unless persistent REPL history is explicitly disabled.

    Uses the ``history`` config key. Default is ``on``.
    """
    raw = get_user_config("history", "on")
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


def get_history_max_entries() -> int:
    """Return configured max history entries, defaulting to 500.

    Invalid, zero, or negative values fall back to ``DEFAULT_HISTORY_MAX_ENTRIES``.
    """
    raw = get_user_config("history_max_entries", str(DEFAULT_HISTORY_MAX_ENTRIES))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_MAX_ENTRIES

    if value <= 0:
        return DEFAULT_HISTORY_MAX_ENTRIES
    return value


class AyePersistentHistory(History):
    """File-backed prompt_toolkit history for the main Aye Chat REPL.

    History is stored as JSON-lines with one object per user-submitted input:

        {"text": "git status"}

    No metadata is stored. Entries are global across projects and chat sessions.
    Exact duplicates are moved to the most-recent position, and the file is
    pruned to ``max_entries`` unique entries.
    """

    def __init__(self, path: Path, max_entries: int = DEFAULT_HISTORY_MAX_ENTRIES):
        """Initialize persistent REPL history.

        Args:
            path: Path to the JSON-lines history file.
            max_entries: Maximum number of unique global entries to keep.
        """
        super().__init__()
        self.path = Path(path).expanduser()
        self.max_entries = max_entries if max_entries > 0 else DEFAULT_HISTORY_MAX_ENTRIES
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_history_strings(self) -> Iterable[str]:
        """Load stored history strings in oldest-to-newest order.

        Invalid/corrupt lines are ignored so a bad history file never prevents
        the REPL from starting.
        """
        return self._load_entries()

    def store_string(self, string: str) -> None:
        """Store one user-submitted REPL input if it passes filtering rules.

        Args:
            string: Exact string returned by prompt_toolkit for the submitted
                REPL input.
        """
        if not self._should_store(string):
            return

        entries = self._load_entries()

        # Exact duplicate handling: remove older occurrences and append the
        # re-entered input so recency is preserved.
        entries = [entry for entry in entries if entry != string]
        entries.append(string)

        if len(entries) > self.max_entries:
            entries = entries[-self.max_entries:]

        self._write_entries(entries)

    def _load_entries(self) -> List[str]:
        """Return valid history entries from disk.

        The file format is JSON-lines with only a ``text`` field. For resilience,
        malformed lines and records without a string ``text`` are skipped.
        """
        if not self.path.is_file():
            return []

        entries: List[str] = []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if not isinstance(record, dict):
                        continue

                    text = record.get("text")
                    if isinstance(text, str) and self._should_store(text):
                        entries.append(text)
        except OSError:
            return []

        # Normalize existing history on load: keep unique entries and preserve
        # the most recent occurrence of any duplicate.
        unique_entries: List[str] = []
        for entry in entries:
            unique_entries = [existing for existing in unique_entries if existing != entry]
            unique_entries.append(entry)

        if len(unique_entries) > self.max_entries:
            unique_entries = unique_entries[-self.max_entries:]

        return unique_entries

    def _write_entries(self, entries: List[str]) -> None:
        """Atomically write entries to disk where practical."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f".{self.path.name}.tmp.{os.getpid()}")

        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                for entry in entries:
                    # Only persist the text field; no metadata.
                    f.write(json.dumps({"text": entry}, ensure_ascii=False))
                    f.write("\n")

            self._chmod_0600(tmp_path)
            os.replace(tmp_path, self.path)
            self._chmod_0600(self.path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _should_store(string: str) -> bool:
        """Return True if a submitted input should be persisted."""
        if not isinstance(string, str):
            return False

        stripped = string.strip()
        if not stripped:
            return False

        if stripped.lower() in _EXIT_COMMANDS:
            return False

        return True

    @staticmethod
    def _chmod_0600(path: Path) -> None:
        """Best-effort chmod 0600 for history files."""
        try:
            path.chmod(0o600)
        except OSError:
            pass

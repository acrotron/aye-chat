import json
from pathlib import Path

import pytest

from aye.model import repl_history
from aye.model.repl_history import (
    DEFAULT_HISTORY_MAX_ENTRIES,
    AyePersistentHistory,
    get_history_max_entries,
    get_repl_history_path,
    is_history_enabled,
)


def _fake_config(values: dict):
    """Return a get_user_config replacement backed by a dict."""

    def _get_user_config(key: str, default=None):
        return values.get(key, default)

    return _get_user_config


def _read_records(path: Path) -> list[dict]:
    """Read JSON-lines records from a history file."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _read_texts(path: Path) -> list[str]:
    """Read text values from a JSON-lines history file."""
    return [record["text"] for record in _read_records(path)]


def test_default_path_resolves_to_home_aye_history(tmp_path, monkeypatch):
    """Default history path should be ~/.aye/history and create parent dir."""
    monkeypatch.setattr(repl_history, "get_user_config", _fake_config({}))
    monkeypatch.setattr(repl_history.Path, "home", lambda: tmp_path)

    path = get_repl_history_path()

    assert path == tmp_path / ".aye" / "history"
    assert path.parent.is_dir()


def test_history_file_config_override_works(tmp_path, monkeypatch):
    """history_file config should override the default path."""
    configured = tmp_path / "custom" / "aye-history.jsonl"
    monkeypatch.setattr(
        repl_history,
        "get_user_config",
        _fake_config({"history_file": str(configured)}),
    )

    path = get_repl_history_path()

    assert path == configured
    assert path.parent.is_dir()


def test_history_file_env_override_works(tmp_path, monkeypatch):
    """AYE_HISTORY_FILE should override config through get_user_config."""
    configured = tmp_path / "env" / "history"
    monkeypatch.setenv("AYE_HISTORY_FILE", str(configured))

    path = get_repl_history_path()

    assert path == configured
    assert path.parent.is_dir()


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, True),
        ("on", True),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("off", False),
        ("false", False),
        ("0", False),
        ("no", False),
    ],
)
def test_is_history_enabled_respects_history_config(raw_value, expected, monkeypatch):
    """history=off should disable persistent history helper behavior."""
    values = {} if raw_value is None else {"history": raw_value}
    monkeypatch.setattr(repl_history, "get_user_config", _fake_config(values))

    assert is_history_enabled() is expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, DEFAULT_HISTORY_MAX_ENTRIES),
        ("", DEFAULT_HISTORY_MAX_ENTRIES),
        ("not-a-number", DEFAULT_HISTORY_MAX_ENTRIES),
        ("0", DEFAULT_HISTORY_MAX_ENTRIES),
        ("-10", DEFAULT_HISTORY_MAX_ENTRIES),
        ("1", 1),
        ("250", 250),
        ("500", 500),
        ("1000", 1000),
    ],
)
def test_get_history_max_entries_defaults_and_invalid_values(raw_value, expected, monkeypatch):
    """history_max_entries should default to 500 and ignore invalid values."""
    values = {} if raw_value is None else {"history_max_entries": raw_value}
    monkeypatch.setattr(repl_history, "get_user_config", _fake_config(values))

    assert get_history_max_entries() == expected


@pytest.mark.parametrize("value", ["", "   ", "\n\t  "])
def test_empty_and_whitespace_only_strings_are_not_stored(tmp_path, value):
    """Empty and whitespace-only inputs should not create history entries."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    history.store_string(value)

    assert not path.exists()
    assert list(history.load_history_strings()) == []


@pytest.mark.parametrize("value", ["exit", "quit", ":q", " EXIT ", " Quit ", " :Q "])
def test_exit_commands_are_not_stored(tmp_path, value):
    """Exact single-token exit commands should not be persisted."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    history.store_string(value)

    assert not path.exists()
    assert list(history.load_history_strings()) == []


def test_shell_commands_are_stored(tmp_path):
    """Shell commands should be persisted exactly as submitted."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    history.store_string("git status")
    history.store_string("pytest tests/ -v")

    assert list(history.load_history_strings()) == ["git status", "pytest tests/ -v"]


def test_ai_prompts_are_stored(tmp_path):
    """AI prompts should be persisted exactly as submitted."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    prompt = "explain this project and suggest improvements"
    history.store_string(prompt)

    assert list(history.load_history_strings()) == [prompt]


def test_builtin_commands_other_than_exit_commands_are_stored(tmp_path):
    """Built-ins such as model/help/history should be stored unless excluded."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    history.store_string("help")
    history.store_string("model")
    history.store_string("history")
    history.store_string("diff src/main.py")

    assert list(history.load_history_strings()) == [
        "help",
        "model",
        "history",
        "diff src/main.py",
    ]


def test_exact_duplicates_are_not_duplicated(tmp_path):
    """Entering the same text repeatedly should keep one copy."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    history.store_string("git status")
    history.store_string("git status")
    history.store_string("git status")

    assert list(history.load_history_strings()) == ["git status"]
    assert _read_texts(path) == ["git status"]


def test_reentered_duplicate_moves_to_most_recent_position(tmp_path):
    """A duplicate should be moved to the newest position instead of copied."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    history.store_string("git status")
    history.store_string("pytest")
    history.store_string("explain this project")
    history.store_string("git status")

    assert list(history.load_history_strings()) == [
        "pytest",
        "explain this project",
        "git status",
    ]


def test_more_than_500_entries_are_pruned_to_500(tmp_path):
    """History should keep only the latest 500 unique global entries by default."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path, max_entries=500)

    for i in range(501):
        history.store_string(f"command {i}")

    entries = list(history.load_history_strings())

    assert len(entries) == 500
    assert entries[0] == "command 1"
    assert entries[-1] == "command 500"
    assert "command 0" not in entries


def test_custom_max_entries_prunes_to_configured_limit(tmp_path):
    """The backend should honor custom max_entries values."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path, max_entries=3)

    for i in range(5):
        history.store_string(f"cmd {i}")

    assert list(history.load_history_strings()) == ["cmd 2", "cmd 3", "cmd 4"]


def test_corrupt_history_file_does_not_crash_loading(tmp_path):
    """Malformed JSON-lines should be ignored without crashing."""
    path = tmp_path / "history"
    path.write_text(
        "not json\n"
        "[]\n"
        "{\"text\": 123}\n"
        "{\"wrong\": \"field\"}\n"
        "{\"text\": \"valid command\"}\n",
        encoding="utf-8",
    )
    history = AyePersistentHistory(path=path)

    assert list(history.load_history_strings()) == ["valid command"]


def test_missing_history_file_loads_as_empty(tmp_path):
    """Missing history files should simply load as empty history."""
    path = tmp_path / "missing-history"
    history = AyePersistentHistory(path=path)

    assert list(history.load_history_strings()) == []


def test_multiline_entries_round_trip_correctly(tmp_path):
    """JSON-lines storage should preserve multiline submitted input."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)
    multiline = "explain this\nwith multiple lines\nplease"

    history.store_string(multiline)

    assert list(history.load_history_strings()) == [multiline]
    assert _read_records(path) == [{"text": multiline}]


def test_json_lines_file_contains_only_text_records_and_no_metadata(tmp_path):
    """Stored JSON-lines records should contain only the text field."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)

    history.store_string("git status")
    history.store_string("review @src/main.py")

    records = _read_records(path)

    assert records == [
        {"text": "git status"},
        {"text": "review @src/main.py"},
    ]
    assert all(set(record.keys()) == {"text"} for record in records)


def test_only_user_submitted_text_is_stored_not_augmented_context(tmp_path):
    """The history backend stores only the exact string it is given."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)
    user_input = "review @src/main.py and https://example.com"

    history.store_string(user_input)

    text = path.read_text(encoding="utf-8")
    assert user_input in text
    assert "Attached URL context" not in text
    assert "def main" not in text
    assert "shellcap" not in text
    assert "data_b64" not in text


def test_existing_duplicate_entries_are_normalized_on_load(tmp_path):
    """Loading an existing file should keep the newest duplicate occurrence."""
    path = tmp_path / "history"
    path.write_text(
        json.dumps({"text": "git status"}) + "\n"
        + json.dumps({"text": "pytest"}) + "\n"
        + json.dumps({"text": "git status"}) + "\n",
        encoding="utf-8",
    )
    history = AyePersistentHistory(path=path)

    assert list(history.load_history_strings()) == ["pytest", "git status"]

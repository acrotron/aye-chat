import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.history import InMemoryHistory

from aye.controller import command_handlers, repl
from aye.controller.shell_capture import maybe_attach_shell_result
from aye.model.repl_history import AyePersistentHistory


class FakePromptSession:
    """Small PromptSession test double that exposes constructor kwargs."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.history = kwargs.get("history")
        self.completer = kwargs.get("completer")
        self.key_bindings = kwargs.get("key_bindings")
        self.complete_style = kwargs.get("complete_style")
        self.complete_while_typing = kwargs.get("complete_while_typing")


def _read_history_records(path: Path) -> list[dict]:
    """Read JSON-lines history records from disk."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _read_history_text(path: Path) -> str:
    """Read raw history file text from disk."""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _binding_handler_names(key_bindings) -> set[str]:
    """Return handler function names registered on a KeyBindings object."""
    return {binding.handler.__name__ for binding in key_bindings.bindings}


def test_create_prompt_session_uses_persistent_history_when_enabled(tmp_path, monkeypatch):
    """The main REPL should use AyePersistentHistory when history=on."""
    history_path = tmp_path / "history"

    monkeypatch.setattr(repl, "PromptSession", FakePromptSession)
    monkeypatch.setattr(repl, "is_history_enabled", lambda: True)
    monkeypatch.setattr(repl, "get_repl_history_path", lambda: history_path)
    monkeypatch.setattr(repl, "get_history_max_entries", lambda: 500)

    session = repl.create_prompt_session(completer="fake-completer", completion_style="readline")

    assert isinstance(session.history, AyePersistentHistory)
    assert session.history.path == history_path
    assert session.history.max_entries == 500
    assert session.completer == "fake-completer"
    assert session.complete_while_typing is True


def test_create_prompt_session_uses_in_memory_history_when_disabled(monkeypatch):
    """The main REPL should fall back to InMemoryHistory when history=off."""
    monkeypatch.setattr(repl, "PromptSession", FakePromptSession)
    monkeypatch.setattr(repl, "is_history_enabled", lambda: False)

    session = repl.create_prompt_session(completer=None, completion_style="readline")

    assert isinstance(session.history, InMemoryHistory)


def test_feedback_prompt_session_remains_in_memory():
    """Feedback prompt history must not be persisted."""
    source = inspect.getsource(repl.collect_and_send_feedback)

    assert "PromptSession(history=InMemoryHistory())" in source
    assert "AyePersistentHistory" not in source


def test_completion_key_bindings_still_exist_after_history_integration(monkeypatch):
    """Completion Enter bindings should remain registered."""
    monkeypatch.setattr(repl, "PromptSession", FakePromptSession)
    monkeypatch.setattr(repl, "is_history_enabled", lambda: False)

    session = repl.create_prompt_session(completer=None, completion_style="readline")
    handler_names = _binding_handler_names(session.key_bindings)

    assert "accept_selected_completion" in handler_names
    assert "accept_first_completion" in handler_names


def test_ctrl_v_key_binding_still_exists_when_enabled(monkeypatch):
    """Ctrl+V image paste binding should survive history integration."""
    monkeypatch.setattr(repl, "PromptSession", FakePromptSession)
    monkeypatch.setattr(repl, "is_history_enabled", lambda: False)
    monkeypatch.setattr(repl, "_is_clipboard_paste_enabled", lambda conf=None: True)

    session = repl.create_prompt_session(
        completer=None,
        completion_style="readline",
        conf=SimpleNamespace(),
    )
    handler_names = _binding_handler_names(session.key_bindings)

    assert "_handle_ctrl_v_clipboard_paste" in handler_names
    assert "accept_selected_completion" in handler_names
    assert "accept_first_completion" in handler_names


def test_existing_history_command_is_still_snapshot_history():
    """The existing `history` command must not be repurposed for REPL history."""
    source = inspect.getsource(repl.chat_repl)

    assert 'lowered_first == "history"' in source
    assert "commands.get_snapshot_history()" in source
    assert "cli_ui.print_snapshot_history(history_list)" in source


def test_blog_history_stores_user_facing_command_not_blog_preamble(tmp_path):
    """Prompt history should contain the typed blog command, not the hidden preamble."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)
    user_input = "blog write a post about persistent history"

    history.store_string(user_input)

    raw_history = _read_history_text(path)
    records = _read_history_records(path)

    assert records == [{"text": user_input}]
    assert command_handlers._BLOG_PROMPT_PREAMBLE not in raw_history
    assert "You are going to write a technical blog post" not in raw_history
    assert "User intent:" not in raw_history


def test_at_and_url_expanded_context_are_not_written_to_history(tmp_path):
    """History should store @/URL prompts exactly as typed, not expanded context."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)
    original_prompt = "review @src/main.py and https://example.com/security-report"

    # Store the prompt as prompt_toolkit returns it, before later REPL mutation.
    history.store_string(original_prompt)

    # Simulate downstream internal data that must never be written to history.
    expanded_source_content = "def main():\n    return 'secret source content'\n"
    fetched_url_context = "Attached URL context: {'url_0.txt': 'external scan JSON'}"
    cleaned_prompt = (
        "review and security-report\n\n---\n"
        f"{fetched_url_context}\n"
        f"{expanded_source_content}\n"
        "---\n"
    )

    raw_history = _read_history_text(path)
    records = _read_history_records(path)

    assert records == [{"text": original_prompt}]
    assert original_prompt in raw_history
    assert cleaned_prompt not in raw_history
    assert expanded_source_content not in raw_history
    assert fetched_url_context not in raw_history
    assert "secret source content" not in raw_history
    assert "external scan JSON" not in raw_history


def test_shellcap_augmented_output_is_not_written_to_history(tmp_path):
    """History should store the typed AI prompt, not attached shell output."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)
    original_prompt = "why did the previous command fail?"
    conf = SimpleNamespace(
        _pending_shell_attach=True,
        _last_shell_result={
            "cmd": "pytest tests/ -v",
            "cwd": "/tmp/project",
            "returncode": 1,
            "stdout": "FAILED tests/test_example.py::test_example",
            "stderr": "AssertionError: expected 1 got 2",
            "failed": True,
        },
    )

    history.store_string(original_prompt)
    augmented_prompt = maybe_attach_shell_result(conf, original_prompt)

    raw_history = _read_history_text(path)
    records = _read_history_records(path)

    assert records == [{"text": original_prompt}]
    assert augmented_prompt != original_prompt
    assert "Captured output from last failing command" in augmented_prompt
    assert "FAILED tests/test_example.py::test_example" in augmented_prompt
    assert "AssertionError: expected 1 got 2" in augmented_prompt
    assert "Captured output from last failing command" not in raw_history
    assert "FAILED tests/test_example.py::test_example" not in raw_history
    assert "AssertionError: expected 1 got 2" not in raw_history


def test_with_command_history_stores_user_facing_command_not_file_contents(tmp_path):
    """with prompts should be recalled as typed, without expanded source contents."""
    path = tmp_path / "history"
    history = AyePersistentHistory(path=path)
    user_input = "with src/main.py: explain this function"

    history.store_string(user_input)

    simulated_expanded_file_contents = "def greet(name: str) -> str:\n    return f'Hello, {name}'"
    raw_history = _read_history_text(path)
    records = _read_history_records(path)

    assert records == [{"text": user_input}]
    assert simulated_expanded_file_contents not in raw_history
    assert "def greet" not in raw_history


def test_history_stores_original_user_input_before_internal_prompt_mutation(tmp_path, monkeypatch):
    """Persistent history should capture original prompt text before REPL mutation."""
    history_path = tmp_path / "history"
    monkeypatch.setattr(repl, "PromptSession", FakePromptSession)
    monkeypatch.setattr(repl, "is_history_enabled", lambda: True)
    monkeypatch.setattr(repl, "get_repl_history_path", lambda: history_path)
    monkeypatch.setattr(repl, "get_history_max_entries", lambda: 500)

    session = repl.create_prompt_session(completer=None, completion_style="readline")
    original_user_input = "describe this [clipboard:image-001] and @src/main.py"

    # This is what prompt_toolkit history receives: the exact submitted input.
    session.history.store_string(original_user_input)

    # Simulate later internal mutations in controller/repl.py that should not
    # be written to command history.
    cleaned_prompt = "describe this and"
    internal_payload_bits = [
        cleaned_prompt,
        "data_b64",
        "image/png",
        "bytes_size",
        "source_files",
        "def implementation_detail(): pass",
    ]

    raw_history = _read_history_text(history_path)
    records = _read_history_records(history_path)

    assert records == [{"text": original_user_input}]
    for internal_value in internal_payload_bits:
        assert internal_value not in raw_history


def test_history_json_lines_records_have_no_metadata_integration(tmp_path, monkeypatch):
    """create_prompt_session-integrated history should still write text-only records."""
    history_path = tmp_path / "history"
    monkeypatch.setattr(repl, "PromptSession", FakePromptSession)
    monkeypatch.setattr(repl, "is_history_enabled", lambda: True)
    monkeypatch.setattr(repl, "get_repl_history_path", lambda: history_path)
    monkeypatch.setattr(repl, "get_history_max_entries", lambda: 500)

    session = repl.create_prompt_session(completer=None, completion_style="readline")
    session.history.store_string("git status")
    session.history.store_string("explain @src/main.py")

    records = _read_history_records(history_path)

    assert records == [
        {"text": "git status"},
        {"text": "explain @src/main.py"},
    ]
    assert all(set(record.keys()) == {"text"} for record in records)

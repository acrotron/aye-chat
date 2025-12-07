import builtins
import json
import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aye.plugins.offline_llm import OfflineLLMPlugin


def test_build_user_message_with_sources():
    plugin = OfflineLLMPlugin()
    prompt = "Summarize the changes"
    sources = OrderedDict(
        [
            ("main.py", "print('hello world')"),
            ("utils/helpers.py", "def helper():\n    return True"),
        ]
    )

    message = plugin._build_user_message(prompt, sources)

    assert message.startswith(prompt)
    assert "--- Source files are below. ---" in message
    for name, content in sources.items():
        assert f"** {name} **" in message
        assert f"```\n{content}\n```" in message


def test_build_user_message_without_sources():
    plugin = OfflineLLMPlugin()
    prompt = "Explain this"

    assert plugin._build_user_message(prompt, {}) == prompt


def test_parse_llm_response_structured_payload():
    plugin = OfflineLLMPlugin()
    payload = json.dumps(
        {
            "properties": {
                "answer_summary": "Done",
                "source_files": [
                    {
                        "file_name": "file.txt",
                        "file_content": "content",
                    }
                ],
            }
        }
    )

    parsed = plugin._parse_llm_response(payload)

    assert parsed["summary"] == "Done"
    assert parsed["updated_files"] == [
        {"file_name": "file.txt", "file_content": "content"}
    ]


def test_parse_llm_response_invalid_json_returns_fallback():
    plugin = OfflineLLMPlugin()

    parsed = plugin._parse_llm_response("not json")

    assert parsed["summary"] == "not json"
    assert parsed["updated_files"] == []


def test_parse_llm_response_without_properties():
    plugin = OfflineLLMPlugin()
    payload = json.dumps({"answer_summary": "Simple", "source_files": []})

    parsed = plugin._parse_llm_response(payload)

    assert parsed["summary"] == "Simple"
    assert parsed["updated_files"] == []


def test_create_error_response_verbose_logs(monkeypatch):
    plugin = OfflineLLMPlugin()
    plugin.verbose = True
    calls = []

    def fake_rprint(message):
        calls.append(message)

    monkeypatch.setattr("aye.plugins.offline_llm.rprint", fake_rprint)

    error_msg = "Something went wrong"
    result = plugin._create_error_response(error_msg)

    assert result == {"summary": error_msg, "updated_files": []}
    assert calls == [f"[red]{error_msg}[/]"]


def test_save_and_load_history_round_trip(tmp_path):
    plugin = OfflineLLMPlugin()
    history_path = tmp_path / ".aye" / "offline_chat_history.json"
    plugin.history_file = history_path
    sample_history = {"default": [{"role": "user", "content": "hi"}]}
    plugin.chat_history = sample_history.copy()

    plugin._save_history()
    assert history_path.exists()

    plugin.chat_history = {}
    plugin._load_history()

    assert plugin.chat_history == sample_history


def test_get_conversation_id_variants():
    plugin = OfflineLLMPlugin()

    assert plugin._get_conversation_id(5) == "5"
    assert plugin._get_conversation_id(0) == "default"
    assert plugin._get_conversation_id(None) == "default"


def test_check_dependencies_success(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace())

    assert plugin._check_dependencies() is True


def test_check_dependencies_failure_logs(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.delitem(sys.modules, "llama_cpp", raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "llama_cpp":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    calls = []
    monkeypatch.setattr("aye.plugins.offline_llm.rprint", lambda message: calls.append(message))

    assert plugin._check_dependencies() is False
    assert "llama-cpp-python not available" in calls[0]
    assert "Install it with" in calls[1]


def test_load_model_returns_true_when_already_loaded():
    plugin = OfflineLLMPlugin()
    plugin._current_model_id = "test-model"
    plugin._llm_instance = object()

    assert plugin._load_model("test-model") is True


def test_load_model_success_path(monkeypatch, tmp_path):
    plugin = OfflineLLMPlugin()
    plugin.verbose = False

    model_file = tmp_path / "model.bin"
    model_file.write_text("data")

    class DummyLlama:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=DummyLlama))
    monkeypatch.setattr(plugin, "_check_dependencies", lambda: True)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_path", lambda _: model_file)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_config", lambda _: {"context_length": 2048})

    assert plugin._load_model("foo") is True
    assert isinstance(plugin._llm_instance, DummyLlama)
    assert plugin._current_model_id == "foo"
    assert plugin._llm_instance.kwargs["model_path"] == str(model_file)


def test_load_model_missing_model_path_returns_false(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.setattr(plugin, "_check_dependencies", lambda: True)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_path", lambda _: None)

    assert plugin._load_model("missing") is False


def test_load_model_handles_exceptions_from_llama(monkeypatch, tmp_path):
    plugin = OfflineLLMPlugin()

    class FailingLlama:
        def __init__(self, **kwargs):
            raise RuntimeError("boom")

    model_file = tmp_path / "model.bin"
    model_file.write_text("data")

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=FailingLlama))
    monkeypatch.setattr(plugin, "_check_dependencies", lambda: True)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_path", lambda _: model_file)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_config", lambda _: {})

    assert plugin._load_model("broken") is False
    assert plugin._llm_instance is None


def test_load_history_handles_missing_file(tmp_path):
    plugin = OfflineLLMPlugin()
    plugin.history_file = tmp_path / ".aye" / "offline_chat_history.json"
    plugin.chat_history = {"default": []}

    plugin._load_history()

    assert plugin.chat_history == {}


def test_load_history_invalid_json_logs_warning(monkeypatch, tmp_path):
    plugin = OfflineLLMPlugin()
    plugin.verbose = True
    history_file = tmp_path / ".aye" / "offline_chat_history.json"
    history_file.parent.mkdir(parents=True)
    history_file.write_text("{invalid json")
    plugin.history_file = history_file
    calls = []
    monkeypatch.setattr("aye.plugins.offline_llm.rprint", lambda message: calls.append(message))

    plugin._load_history()

    assert plugin.chat_history == {}
    assert "Could not load offline model chat history" in calls[0]


def test_save_history_handles_write_errors(monkeypatch, tmp_path):
    plugin = OfflineLLMPlugin()
    plugin.verbose = True
    history_file = tmp_path / ".aye" / "offline_chat_history.json"
    plugin.history_file = history_file
    plugin.chat_history = {"default": []}
    calls = []
    monkeypatch.setattr("aye.plugins.offline_llm.rprint", lambda message: calls.append(message))
    original_write = Path.write_text

    def fake_write_text(self, *args, **kwargs):
        if self == history_file:
            raise OSError("disk full")
        return original_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fake_write_text, raising=False)

    plugin._save_history()

    assert calls == ["[yellow]Could not save offline model chat history: disk full[/]"]


def test_generate_response_returns_error_when_load_fails():
    plugin = OfflineLLMPlugin()
    plugin._load_model = lambda _: False

    result = plugin._generate_response("model", "prompt", {}, chat_id=None)

    assert "Failed to load offline model" in result["summary"]


def test_generate_response_returns_error_when_instance_missing():
    plugin = OfflineLLMPlugin()
    plugin._load_model = lambda _: True
    plugin._llm_instance = None

    result = plugin._generate_response("model", "prompt", {}, chat_id=None)

    assert "Model instance" in result["summary"]


def test_generate_response_success_updates_history_and_saves(monkeypatch):
    plugin = OfflineLLMPlugin()
    plugin.chat_history = {}
    plugin._load_model = lambda _: True
    dummy_response_text = json.dumps(
        {"properties": {"answer_summary": "Summary", "source_files": []}}
    )

    class DummyLLM:
        def __init__(self):
            self.calls = []

        def create_chat_completion(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "choices": [
                    {
                        "message": {
                            "content": dummy_response_text
                        }
                    }
                ]
            }

    llm = DummyLLM()
    plugin._llm_instance = llm
    saved = {"called": False}
    monkeypatch.setattr(plugin, "_save_history", lambda: saved.__setitem__("called", True))

    result = plugin._generate_response(
        "model",
        "Do work",
        {"main.py": "print(1)"},
        chat_id=7,
    )

    assert result["summary"] == "Summary"
    assert plugin.chat_history["7"][0]["role"] == "user"
    assert len(plugin.chat_history["7"]) == 2
    assert saved["called"] is True
    assert len(llm.calls) == 1
    assert llm.calls[0]["messages"][0]["role"] == "system"


def test_generate_response_returns_error_when_no_choices():
    plugin = OfflineLLMPlugin()
    plugin._load_model = lambda _: True

    class DummyLLM:
        def create_chat_completion(self, **kwargs):
            return {"choices": []}

    plugin._llm_instance = DummyLLM()

    result = plugin._generate_response("model", "prompt", {}, chat_id=None)

    assert result["summary"] == "No response generated from offline model"


def test_generate_response_returns_error_on_exception():
    plugin = OfflineLLMPlugin()
    plugin._load_model = lambda _: True

    class DummyLLM:
        def create_chat_completion(self, **kwargs):
            raise RuntimeError("boom")

    plugin._llm_instance = DummyLLM()

    result = plugin._generate_response("model", "prompt", {}, chat_id=None)

    assert "Error generating response" in result["summary"]


def test_on_command_download_model_non_offline(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.setattr("aye.plugins.offline_llm.is_offline_model", lambda _: False)

    result = plugin.on_command("download_offline_model", {"model_id": "m"})

    assert result == {"success": False, "error": "Not an offline model"}


def test_on_command_download_model_ready(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.setattr("aye.plugins.offline_llm.is_offline_model", lambda _: True)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_status", lambda _: "READY")
    calls = []
    monkeypatch.setattr("aye.plugins.offline_llm.rprint", lambda message: calls.append(message))

    result = plugin.on_command(
        "download_offline_model", {"model_id": "m", "model_name": "Model"}
    )

    assert result == {"success": True}
    assert "already downloaded" in calls[0]


def test_on_command_download_model_triggers_download(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.setattr("aye.plugins.offline_llm.is_offline_model", lambda _: True)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_status", lambda _: "PENDING")
    monkeypatch.setattr("aye.plugins.offline_llm.download_model_sync", lambda _: True)

    result = plugin.on_command("download_offline_model", {"model_id": "m"})

    assert result == {"success": True}


def test_on_command_new_chat_clears_history(monkeypatch, tmp_path):
    plugin = OfflineLLMPlugin()
    plugin.verbose = True
    history_file = tmp_path / ".aye" / "offline_chat_history.json"
    history_file.parent.mkdir(parents=True)
    history_file.write_text("{}")
    plugin.chat_history = {"default": ["old"]}
    calls = []
    monkeypatch.setattr("aye.plugins.offline_llm.rprint", lambda message: calls.append(message))

    result = plugin.on_command("new_chat", {"root": tmp_path})

    assert result == {"status": "offline_history_cleared"}
    assert not history_file.exists()
    assert plugin.chat_history == {}
    assert "history cleared" in calls[0]


def test_on_command_local_model_invoke_non_offline(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.setattr("aye.plugins.offline_llm.is_offline_model", lambda _: False)

    result = plugin.on_command("local_model_invoke", {"model_id": "m"})

    assert result is None


def test_on_command_local_model_invoke_model_not_ready(monkeypatch):
    plugin = OfflineLLMPlugin()
    monkeypatch.setattr("aye.plugins.offline_llm.is_offline_model", lambda _: True)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_status", lambda _: "DOWNLOADING")

    result = plugin.on_command("local_model_invoke", {"model_id": "m"})

    assert "Offline model" in result["summary"]


def test_on_command_local_model_invoke_success(monkeypatch, tmp_path):
    plugin = OfflineLLMPlugin()
    monkeypatch.setattr("aye.plugins.offline_llm.is_offline_model", lambda _: True)
    monkeypatch.setattr("aye.plugins.offline_llm.get_model_status", lambda _: "READY")

    loaded = {"called": False}

    def fake_load_history():
        loaded["called"] = True

    monkeypatch.setattr(plugin, "_load_history", fake_load_history)

    captured = {}

    def fake_generate(model_id, prompt, source_files, chat_id, system_prompt, max_output_tokens):
        captured["args"] = (model_id, prompt, source_files, chat_id, system_prompt, max_output_tokens)
        return {"summary": "ok", "updated_files": []}

    monkeypatch.setattr(plugin, "_generate_response", fake_generate)

    params = {
        "model_id": "m",
        "prompt": "Test",
        "source_files": {"a": "b"},
        "chat_id": 3,
        "root": tmp_path,
        "system_prompt": "custom prompt"
    }

    result = plugin.on_command("local_model_invoke", params)

    assert result == {"summary": "ok", "updated_files": []}
    assert captured["args"] == ("m", "Test", {"a": "b"}, 3, "custom prompt", 4096)
    assert loaded["called"] is True
    assert plugin.history_file == tmp_path / ".aye" / "offline_chat_history.json"

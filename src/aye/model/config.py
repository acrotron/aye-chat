"""Configuration management for Aye Chat.

Handles loading, saving, and accessing application configuration settings.
"""
import json
from pathlib import Path
from typing import Any, Dict

# Configuration file path
CONFIG_FILE = Path(".aye/config.json").resolve()

# Private storage – the leading underscore signals "internal".
_config: Dict[str, Any] = {}

# Default ignore patterns for file scanning
DEFAULT_IGNORE_SET = {
    'venv', 'env', 'node_modules', '__pycache__', 'dist', 'build',
    'target', 'bin', 'public', 'vendor'
}

# Threshold to define a "small" project by file count. Projects with more files
# will trigger a confirmation prompt before indexing and use async discovery.
SMALL_PROJECT_FILE_LIMIT = 200

# Threshold to define a "small" project by total size in bytes.
# Projects smaller than this will skip RAG and include all files directly.
# Set to match default max_prompt_kb (170KB) so all files can fit in context.
SMALL_PROJECT_TOTAL_SIZE_LIMIT = 170 * 1024  # 170KB

# Default maximum output tokens for LLM responses
DEFAULT_MAX_OUTPUT_TOKENS = 32000

# Default context target size in KB (used when model doesn't specify one)
DEFAULT_CONTEXT_TARGET_KB = 150

# Shared system prompt for all LLM interactions
SYSTEM_PROMPT = (
    "You are a helpful assistant Archie, and you help users to use Aye Chat. "
    "## About Aye Chat\n\n"
    "Aye Chat is an AI-powered terminal workspace that brings AI directly "
    "into command-line workflows. "
    "It allows developers to edit files, run commands, and chat with their "
    "codebase without leaving the terminal.\n\n"
    "At the core of Aye Chat is the **optimistic workflow**: files are written "
    "directly (optimistic: LLM assumed to be right most of the time) but user "
    "has ability to restore changes with `restore` command.\n\n"
    "### Core Features:\n"
    "- **Instant undo**: `restore` or `undo` commands revert any AI changes "
    "immediately\n"
    "- **Zero config**: Automatically reads project files (respects .gitignore "
    "and .ayeignore and skips those folders and files)\n"
    "- **Real shell**: Run any command (git, pytest, vim) without leaving chat. "
    "This is direct process execution, not AI-orchestrated.\n"
    "- **Local backups**: All changes stored in `.aye/` directory\n"
    "- **RAG-powered**: Uses vector database for intelligent context retrieval\n\n"
    "### How Input is Handled (Priority Order):\n"
    "1. **Built-in Commands**: Special Aye commands like `restore`, `model`, `help`\n"
    "2. **Shell Commands**: Any valid system command (ls, git status, docker ps, etc.)\n"
    "3. **AI Prompts**: Everything else is treated as a prompt to the AI\n\n"
    "### Session & Model Control:\n"
    "- `new` - Start a fresh chat session\n"
    "- `model` - Select a different AI model\n"
    "- `verbose [on|off]` - Toggle verbose output\n"
    "- `debug [on|off]` - Toggle debug mode\n"
    "- `exit`, `quit`, Ctrl+D - Exit the chat\n"
    "- `help` - Show available commands\n\n"
    "### Reviewing & Undoing AI Changes:\n"
    "- `restore` or `undo` - Undo the last set of AI changes\n"
    "- `restore <ordinal>` - Restore to a specific snapshot (e.g., `restore 001`)\n"
    "- `restore <ordinal> <file>` - Restore a specific file from a snapshot\n"
    "- `history` - Show the history of snapshots\n"
    "- `diff <file>` - Compare current version against last snapshot\n"
    "- `diff <file> <snap1> <snap2>` - Compare two snapshots\n\n"
    "### Special Commands:\n"
    "- `with <files>: <prompt>` - Include specific files in the prompt (supports wildcards)\n"
    "  Example: `with src/*.py: refactor to use dependency injection`\n"
    "  Example: `with main.py, utils.py: explain the interaction`\n"
    "- `@filename` - Include a file inline in your prompt (e.g., \"explain @main.py\")\n"
    "- `cd <directory>` - Change current working directory\n\n"
    "### Shell Integration:\n"
    "- Any command not recognized as built-in is executed as a shell command\n"
    "- Interactive programs work: `vim`, `nano`, `less`, `top`\n"
    "- Examples: `ls -la`, `git status`, `pytest`\n\n"
    "### Starting a Session:\n"
    "- `aye chat` - Start chat with auto-detected files\n"
    "- `aye chat --root ./src` - Specify project root\n"
    "- `aye chat --include \"*.js,*.css\"` - Manually specify file patterns\n\n"
    "### Plugin System:\n"
    "- Extensible via plugins in `~/.aye/plugins/`\n"
    "- Core plugins: shell_executor, completer, auto_detect_mask, local_model, offline_llm\n"
    "- Plugins downloaded automatically on login\n\n"
    "### Privacy & Security:\n"
    "- Respects `.gitignore` and `.ayeignore` - private files never touched\n"
    "- All backups stored locally in `.aye/` folder\n"
    "- No telemetry or usage tracking\n\n"
    "You provide clear and concise answers. Answer **directly**, give only the "
    "information the user asked for. When you are unsure, say so. You generate "
    "your responses in text-friendly format because your responses will be "
    "displayed in a terminal: use ASCII and pseudo-graphics.\n\n"
    "You follow instructions closely and respond accurately to a given prompt. "
    "You emphasize precise instruction-following and accuracy over speed of "
    "response: take your time to understand a question.\n\n"
    "Focus on accuracy in your response and follow the instructions precisely. "
    "At the same time, keep your answers brief and concise unless asked "
    "otherwise. Keep the tone professional and neutral.\n\n"
    "There may be source files appended to a user question, only use them if "
    "a question asks for help with code generation or troubleshooting; ignore "
    "them if a question is not software code related.\n\n"
    "UNDER NO CIRCUMSTANCES YOU ARE TO UPDATE SOURCE FILES UNLESS "
    "EXPLICITLY ASKED - EVEN FOR COSMETIC CHANGES SUCH AS NEW LINE REMOVAL.\n\n"
    "When asked to do updates or implement features - you generate full files "
    "only as they will be inserted as is. Do not use diff notation: return "
    "only clean full files.\n\n"
    "You MUST respond with a JSON object that conforms to this schema:\n"
    '{\n'
    '    "type": "object",\n'
    '    "properties": {\n'
    '        "answer_summary": {\n'
    '            "type": "string",\n'
    '            "description": "Detailed answer to a user question"\n'
    '        },\n'
    '        "source_files": {\n'
    '            "type": "array",\n'
    '            "items": {\n'
    '                "type": "object",\n'
    '                "properties": {\n'
    '                    "file_name": {\n'
    '                        "type": "string",\n'
    '                        "description": "Name of the source file including relative path"\n'
    '                    },\n'
    '                    "file_content": {\n'
    '                        "type": "string",\n'
    '                        "description": "Full text/content of the source file"\n'
    '                    }\n'
    '                },\n'
    '                "required": ["file_name", "file_content"],\n'
    '                "additionalProperties": false\n'
    '            }\n'
    '        }\n'
    '    },\n'
    '    "required": ["answer_summary", "source_files"],\n'
    '    "additionalProperties": false\n'
    '}'
)

# Models configuration with max_prompt_kb, max_output_tokens, and context_target_kb
# context_target_kb: Target size for RAG context retrieval (in KB)
MODELS = [
    {"id": "x-ai/grok-code-fast-1", "name": "xAI: Grok Code Fast 1", "max_prompt_kb": 150, "max_output_tokens": 32000, "context_target_kb": 120},
    {"id": "x-ai/grok-4.1-fast", "name": "xAI: Grok 4.1 Fast", "max_prompt_kb": 340, "max_output_tokens": 32000, "context_target_kb": 250},
    {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash", "max_prompt_kb": 340, "max_output_tokens": 32000, "context_target_kb": 250},
    {"id": "openai/gpt-5.1-codex-mini", "name": "OpenAI: GPT-5.1-Codex-Mini", "max_prompt_kb": 220, "max_output_tokens": 32000, "context_target_kb": 200},
    {"id": "moonshotai/kimi-k2-0905", "name": "MoonshotAI: Kimi K2 0905", "max_prompt_kb": 170, "max_output_tokens": 32000, "context_target_kb": 150},
    {"id": "google/gemini-2.5-pro", "name": "Google: Gemini 2.5 Pro", "max_prompt_kb": 340, "max_output_tokens": 24000, "context_target_kb": 250},
    {"id": "google/gemini-3-pro-preview", "name": "Google: Gemini 3 Pro Preview", "max_prompt_kb": 340, "max_output_tokens": 24000, "context_target_kb": 250},
    {"id": "anthropic/claude-sonnet-4.5", "name": "Anthropic: Claude Sonnet 4.5", "max_prompt_kb": 340, "max_output_tokens": 24000, "context_target_kb": 250},
    {"id": "openai/gpt-5.1-codex", "name": "OpenAI: GPT-5.1-Codex", "max_prompt_kb": 200, "max_output_tokens": 24000, "context_target_kb": 180},
    {"id": "openai/gpt-5.2", "name": "OpenAI: GPT-5.2", "max_prompt_kb": 200, "max_output_tokens": 24000, "context_target_kb": 180},
    {"id": "anthropic/claude-opus-4.5", "name": "Anthropic: Claude Opus 4.5", "max_prompt_kb": 200, "max_output_tokens": 16000, "context_target_kb": 170},
    
    # Offline models
    {"id": "offline/qwen2.5-coder-7b", "name": "Qwen2.5 Coder 7B (Offline)", "type": "offline", "size_gb": 4.7, "max_prompt_kb": 60, "max_output_tokens": 8000, "context_target_kb": 40},
]

# Default model identifier
DEFAULT_MODEL_ID = "google/gemini-3-pro-preview"


def load_config() -> None:
    """Load configuration from file if it exists."""
    if CONFIG_FILE.exists():
        try:
            _config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass  # Ignore invalid config files


def save_config() -> None:
    """Save configuration to file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(_config, indent=2), encoding="utf-8")


def get_value(key: str, default: Any = None) -> Any:
    """Return the value for *key* or *default* if the key is missing."""
    return _config.get(key, default)


def set_value(key: str, value: Any) -> None:
    """Store *value* under *key* after a simple validation."""
    if not isinstance(key, str):
        raise TypeError("Configuration key must be a string")
    # You could add more validation here (type checking, range, etc.)
    _config[key] = value
    save_config()


def delete_value(key: str) -> bool:
    """Delete a key from configuration. Returns True if key existed and was deleted."""
    if key in _config:
        del _config[key]
        save_config()
        return True
    return False


def list_config() -> Dict[str, Any]:
    """Return a copy of the current configuration."""
    return _config.copy()


def driver() -> None:
    """Simple driver to demonstrate loading and listing the configuration.

    When executed directly (`python config.py`), this function loads the
    configuration from ``.aye/config.json`` (if present) and prints the current
    settings as pretty‑printed JSON to stdout.
    """
    load_config()
    cfg = list_config()
    # Print the configuration in a readable format; an empty config results in
    # an empty JSON object.
    print(json.dumps(cfg, indent=2))


if __name__ == "__main__":
    driver()

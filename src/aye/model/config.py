# config.py
import json
from pathlib import Path
from typing import Any, Dict

# Configuration file path
CONFIG_FILE = Path(".aye/config.json").resolve()

# Private storage – the leading underscore signals "internal".
_config: Dict[str, Any] = {}

# Models configuration (order unchanged)
MODELS = [
    #{"id": "openai/gpt-oss-120b", "name": "OpenAI: GPT OSS 120b"},
    {"id": "x-ai/grok-code-fast-1", "name": "xAI: Grok Code Fast 1"},
    {"id": "x-ai/grok-4-fast", "name": "xAI: Grok 4 Fast"},
    #{"id": "qwen/qwen3-coder", "name": "Qwen: Qwen3 Coder"},
    #{"id": "deepseek/deepseek-chat-v3-0324", "name": "DeepSeek: DeepSeek V3 0324"},
    {"id": "google/gemini-2.0-flash-001", "name": "Google: Gemini 2.0 Flash"},
    {"id": "moonshotai/kimi-k2-0905", "name": "MoonshotAI: Kimi K2 0905"},
    {"id": "google/gemini-2.5-pro", "name": "Google: Gemini 2.5 Pro"},
    {"id": "anthropic/claude-sonnet-4.5", "name": "Anthropic: Claude Sonnet 4.5"},
    #{"id": "anthropic/claude-opus-4.1", "name": "Anthropic: Claude Opus 4.1"}
]

# Default model identifier – kept separate so the order of MODELS stays unchanged.
DEFAULT_MODEL_ID = "google/gemini-2.5-pro"


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

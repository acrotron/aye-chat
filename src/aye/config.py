# config.py
import json
from pathlib import Path
from typing import Any, Dict

# Configuration file path
CONFIG_FILE = Path(".aye/config.json").resolve()

# Private storage – the leading underscore signals "internal".
_config: Dict[str, Any] = {}


def load_config() -> None:
    """Load configuration from file if it exists."""
    if CONFIG_FILE.exists():
        try:
            _config.update(json.loads(CONFIG_FILE.read_text()))
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

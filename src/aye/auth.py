# auth.py
import os
import typer
from typing import Any, Optional
from pathlib import Path
from rich import print as rprint
import hashlib
import time

SERVICE_NAME = "aye-cli"
TOKEN_ENV_VAR = "AYE_TOKEN"
TOKEN_FILE = Path.home() / ".ayecfg"


def _parse_user_config() -> dict[str, str]:
    """Parse ~/.ayecfg into a dict for the [default] section."""
    config: dict[str, str] = {}
    if not TOKEN_FILE.is_file():
        return config
    try:
        content = TOKEN_FILE.read_text(encoding="utf-8")
        current_section = None
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                continue
            if current_section == "default" and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    except Exception:
        pass
    return config


def get_user_config(key: str, default: Any = None) -> Any:
    """Get a user config value, with environment variable override."""
    env_key = f"AYE_{key.upper().replace('-', '_')}"
    env_value = os.getenv(env_key)
    if env_value is not None:
        return env_value
    config = _parse_user_config()
    return config.get(key, default)


def set_user_config(key: str, value: Any) -> None:
    """Set a user config value in the [default] section."""
    config = _parse_user_config()
    config[key] = str(value)
    new_content = "[default]\n"
    for k, v in config.items():
        new_content += f"{k}={v}\n"
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(new_content, encoding="utf-8")
    TOKEN_FILE.chmod(0o600)


def store_token(token: str) -> None:
    """Persist the token in ~/.ayecfg (unless AYE_TOKEN is set)."""
    token = token.strip()
    set_user_config("token", token)


def get_token() -> Optional[str]:
    """Return the stored token (env → file). If None, generate a demo token."""
    token = get_user_config("token")
    if token is None:
        demo_hash = hashlib.md5(str(time.time()).encode()).hexdigest()[:10]
        demo_token = "aye_demo_" + demo_hash
        set_user_config("token", demo_token)
        return demo_token
    return token


def delete_token() -> None:
    """Delete the token from file (but not environment), preserving other settings."""
    config = _parse_user_config()
    config.pop("token", None)
    if not config:
        TOKEN_FILE.unlink(missing_ok=True)
    else:
        new_content = "[default]\n"
        for k, v in config.items():
            new_content += f"{k}={v}\n"
        TOKEN_FILE.write_text(new_content, encoding="utf-8")
        TOKEN_FILE.chmod(0o600)


def login_flow() -> None:
    """
    Small login flow:
    1. Prompt user to obtain token at https://ayechat.ai
    2. User enters/pastes the token in terminal (hidden input)
    3. Save the token to ~/.ayecfg (if AYE_TOKEN not set)
    """
    rprint("[yellow]Obtain your personal access token at https://ayechat.ai[/]")
    token = typer.prompt("Paste your token", hide_input=True)
    store_token(token.strip())
    typer.secho("✅ Token saved.", fg=typer.colors.GREEN)


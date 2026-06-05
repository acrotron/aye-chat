# auth.py
import hashlib
import inspect
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import typer
from rich import print as rprint

SERVICE_NAME = "aye-cli"
TOKEN_ENV_VAR = "AYE_TOKEN"
TOKEN_FILE = Path(os.getenv("AYE_TOKEN_FILE")) if os.getenv("AYE_TOKEN_FILE") else Path.home() / ".ayecfg"


def _parse_user_config() -> dict[str, str]:
    """Parse ~/.ayecfg or value from AYE_TOKEN_FILE environment variable into a dict for the [default] section."""
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


def delete_user_config(key: str) -> None:
    """Delete a user config key from the [default] section.

    If the key doesn't exist, this is a no-op.
    Preserves other settings and maintains file permissions.
    """
    config = _parse_user_config()
    if key not in config:
        return
    config.pop(key, None)
    if not config:
        # If no config left, remove the file entirely
        TOKEN_FILE.unlink(missing_ok=True)
    else:
        new_content = "[default]\n"
        for k, v in config.items():
            new_content += f"{k}={v}\n"
        TOKEN_FILE.write_text(new_content, encoding="utf-8")
        TOKEN_FILE.chmod(0o600)


def store_token(token: str) -> None:
    """Persist the token in ~/.ayecfg or value from AYE_TOKEN_FILE environment variable."""
    token = token.strip()
    set_user_config("token", token)


# Token validation pattern: alphanumeric, underscores, hyphens only
_TOKEN_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_MIN_TOKEN_LENGTH = 8


def _is_valid_token(token: str) -> bool:
    """Check if a token has a valid format.

    Valid tokens must:
    - Be at least 8 characters long
    - Contain only alphanumeric characters, underscores, and hyphens
    """
    if not token or len(token) < _MIN_TOKEN_LENGTH:
        return False
    return bool(_TOKEN_PATTERN.match(token))


def _generate_demo_token() -> str:
    """Generate a new demo token."""
    demo_hash = hashlib.md5(str(time.time()).encode()).hexdigest()[:10]
    return "aye_demo_" + demo_hash


def get_token() -> Optional[str]:
    """Return the stored token (env/file). If None or invalid, generate a demo token."""
    token = get_user_config("token")
    if token is None or not _is_valid_token(token):
        demo_token = _generate_demo_token()
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


def _supports_kwarg(callable_obj: Any, name: str) -> bool:
    """Return True if callable_obj accepts the named keyword argument."""
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False

    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return name in signature.parameters


def _verify_login_token_if_supported(token: str, previous_token: Optional[str]) -> None:
    """Verify a login token with the backend when this branch's API supports it.

    Older branches expose fetch_plugin_manifest() without token_override and
    previous_token. In that case, skip backend verification and preserve the
    historical login behavior expected by existing tests.
    """
    from aye.model.api import fetch_plugin_manifest

    if not _supports_kwarg(fetch_plugin_manifest, "token_override"):
        return

    kwargs: dict[str, Any] = {"token_override": token}
    if _supports_kwarg(fetch_plugin_manifest, "dry_run"):
        kwargs["dry_run"] = False
    if _supports_kwarg(fetch_plugin_manifest, "previous_token"):
        kwargs["previous_token"] = previous_token

    fetch_plugin_manifest(**kwargs)


def login_flow() -> None:
    """Prompt for a token, optionally verify it with the backend, then store it.

    If the current branch's API supports login verification, the new token is
    verified before it is stored and the previous token is passed for backend
    user association. If the API does not support that newer verification
    signature, login falls back to the existing local save behavior.
    """
    # Read old token without generating a demo token.
    # Do NOT use get_token() here as it auto-generates demo tokens.
    old_token = get_user_config("token")
    old_token = str(old_token).strip() if old_token else None

    # Warn if AYE_TOKEN env var is set (it will override the saved token)
    if os.getenv(TOKEN_ENV_VAR):
        rprint(
            "[yellow]Note: AYE_TOKEN environment variable is set. "
            "The saved token will not be used until that variable is removed.[/]"
        )

    # Prompt for new token
    rprint("[yellow]Obtain your personal access token at https://ayechat.ai[/]")
    token = typer.prompt("Paste your token", hide_input=True).strip()

    # Validate format locally
    if not _is_valid_token(token):
        typer.secho("Invalid token format.", fg=typer.colors.RED)
        raise typer.Exit(1)

    # Verify with backend only when the API layer supports the newer login flow.
    try:
        _verify_login_token_if_supported(token, old_token)
    except Exception as e:
        typer.secho(
            f"Login failed: could not verify token with the backend. "
            f"Existing token was not changed. ({e})",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    store_token(token)
    typer.secho("\u2705 Token saved.", fg=typer.colors.GREEN)

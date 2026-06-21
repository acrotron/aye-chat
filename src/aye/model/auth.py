# auth.py
import hashlib
import inspect
import os
import platform
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from rich import print as rprint

SERVICE_NAME = "aye-cli"
TOKEN_ENV_VAR = "AYE_TOKEN"
TOKEN_FILE = Path(os.getenv("AYE_TOKEN_FILE")) if os.getenv("AYE_TOKEN_FILE") else Path.home() / ".ayecfg"

# API configuration (duplicated here to avoid circular import with api.py)
_API_BASE_URL = os.environ.get("AYE_CHAT_API_URL", "https://api.ayechat.ai")
_API_TIMEOUT = 30.0


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


def _ssl_verify() -> bool:
    """Control TLS certificate verification for API calls."""
    raw = get_user_config("sslverify", "on")
    val = str(raw).strip().lower()
    if val in ("0", "false", "off", "no"):
        return False
    return True


def _get_client_version() -> str:
    """Get the current client version."""
    try:
        from aye.model.version_checker import get_current_version
        return get_current_version()
    except Exception:
        return "unknown"


def _get_or_create_install_id() -> str:
    """Get or create a persistent install ID.

    The install_id is a random UUID generated once per installation.
    It is NOT a secret - it's used only to help the backend correlate
    demo token requests from the same installation.

    Stored in ~/.ayecfg as 'install_id'.
    """
    install_id = get_user_config("install_id")
    if install_id and isinstance(install_id, str) and len(install_id) >= 32:
        return install_id

    # Generate a new install ID
    new_install_id = str(uuid.uuid4())
    set_user_config("install_id", new_install_id)
    return new_install_id


class DemoTokenError(Exception):
    """Raised when demo token acquisition fails."""
    pass


def _request_demo_token() -> Optional[str]:
    """Call POST /demo/start and return the issued demo token.

    Returns:
        The demo token string if successful, None if the request fails.

    Raises:
        DemoTokenError: If the request fails with a clear error message.
    """
    install_id = _get_or_create_install_id()
    payload = {
        "install_id": install_id,
        "client": "cli",
        "version": _get_client_version(),
        "platform": platform.system(),
    }

    url = f"{_API_BASE_URL}/demo/start"
    verify = _ssl_verify()

    try:
        with httpx.Client(timeout=_API_TIMEOUT, verify=verify) as client:
            resp = client.post(url, json=payload)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    token = data.get("token")
                    if token and _is_valid_token(token):
                        return token
                    else:
                        raise DemoTokenError(
                            "Server returned invalid token format. "
                            "Please try again or run 'aye auth login'."
                        )
                except (ValueError, KeyError) as e:
                    raise DemoTokenError(
                        f"Invalid response from server: {e}. "
                        "Please try again or run 'aye auth login'."
                    )

            # Handle specific error codes
            if resp.status_code == 429:
                raise DemoTokenError(
                    "Too many demo requests. Please wait a moment and try again, "
                    "or run 'aye auth login' to use your personal token."
                )
            elif resp.status_code == 503:
                raise DemoTokenError(
                    "Service temporarily unavailable. Please try again later "
                    "or run 'aye auth login'."
                )
            else:
                # Try to extract error message from response
                try:
                    err_data = resp.json()
                    err_msg = err_data.get("error", resp.text)
                except Exception:
                    err_msg = resp.text

                raise DemoTokenError(
                    f"Failed to start demo session (HTTP {resp.status_code}): {err_msg}. "
                    "Please run 'aye auth login' to authenticate."
                )

    except httpx.ConnectError:
        raise DemoTokenError(
            "Could not connect to Aye Chat servers. "
            "Please check your internet connection and try again."
        )
    except httpx.TimeoutException:
        raise DemoTokenError(
            "Connection timed out. Please check your internet connection and try again."
        )
    except DemoTokenError:
        raise
    except Exception as e:
        raise DemoTokenError(
            f"Unexpected error starting demo session: {e}. "
            "Please run 'aye auth login' to authenticate."
        )


def refresh_demo_token() -> Optional[str]:
    """Request a new demo token from the server and persist it.

    Called when an INVALID_DEMO_TOKEN error is received from the API.
    This allows the client to recover from expired or revoked demo tokens.

    Returns:
        The new demo token if successful, None otherwise.
    """
    try:
        new_token = _request_demo_token()
        if new_token:
            set_user_config("token", new_token)
            return new_token
    except DemoTokenError:
        pass
    return None


def get_token() -> Optional[str]:
    """Return the stored token (env/file). If None or invalid, request from server.

    For signed-in users: Returns their stored token.
    For demo users: Requests a token from /demo/start if none exists.

    Raises:
        DemoTokenError: If no valid token exists and demo token acquisition fails.
    """
    token = get_user_config("token")
    if token and _is_valid_token(token):
        return token

    # No valid token: request one from server
    demo_token = _request_demo_token()
    if demo_token:
        set_user_config("token", demo_token)
        return demo_token

    # This should not be reached as _request_demo_token raises on failure
    raise DemoTokenError(
        "Failed to obtain demo token. Please run 'aye auth login' to authenticate."
    )


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

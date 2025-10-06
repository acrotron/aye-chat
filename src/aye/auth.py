# auth.py
import os
import typer
from pathlib import Path
from rich import print as rprint

SERVICE_NAME = "aye-cli"
TOKEN_ENV_VAR = "AYE_TOKEN"
TOKEN_FILE = Path.home() / ".ayecfg"


def store_token(token: str) -> None:
    """Persist the token in ~/.ayecfg (unless AYE_TOKEN is set)."""
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    TOKEN_FILE.chmod(0o600)  # POSIX only


def get_token() -> str | None:
    """Return the stored token (env → file)."""
    # 1. Try environment variable first
    env_token = os.getenv(TOKEN_ENV_VAR)
    if env_token:
        return env_token.strip()

    # 2. Try config file
    if TOKEN_FILE.is_file():
        try:
            return TOKEN_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass  # Continue if file read fails

    return None


def delete_token() -> None:
    """Delete the token from file (but not environment)."""
    # Delete the file-based token
    TOKEN_FILE.unlink(missing_ok=True)


def login_flow() -> None:
    """
    Small login flow:
    1. Prompt user to obtain token at https://ayechat.ai
    2. User enters/pastes the token in terminal (hidden input)
    3. Save the token to ~/.ayecfg (if AYE_TOKEN not set)
    """
    #typer.echo(
    #    "Obtain your personal access token at https://ayechat.ai
    #)
    rprint("[yellow]Obtain your personal access token at https://ayechat.ai[/]")
    token = typer.prompt("Paste your token", hide_input=True)
    store_token(token.strip())
    typer.secho("✅ Token saved.", fg=typer.colors.GREEN)

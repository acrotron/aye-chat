# auth.py
import os
import typer
from pathlib import Path
from rich import print as rprint

SERVICE_NAME = "aye-cli"
TOKEN_ENV_VAR = "AYE_TOKEN"
TOKEN_FILE = Path.home() / ".ayecfg"


def store_token(token: str) -> None:
    """Persist the token in ~/.ayecfg (unless AYE_TOKEN is set).
    
    The token file now supports multiple profiles. The default profile is
    written as:
        [default]
        token=<token>
    Future profiles can be added as additional sections.
    """
    token = token.strip()
    # Write the new format with a default profile header.
    content = f"[default]\ntoken={token}\n"
    TOKEN_FILE.write_text(content, encoding="utf-8")
    TOKEN_FILE.chmod(0o600)  # POSIX only


def get_token() -> str | None:
    """Return the stored token (env → file).
    
    The function now parses the token file which may contain a profile
    header (e.g., ``[default]``) followed by ``token=<value>``.
    """
    # 1. Try environment variable first
    env_token = os.getenv(TOKEN_ENV_VAR)
    if env_token:
        return env_token.strip()

    # 2. Try config file with profile parsing
    if TOKEN_FILE.is_file():
        try:
            for line in TOKEN_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("["):
                    # Skip empty lines and section headers like [default]
                    continue
                if line.startswith("token="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            # If parsing fails, fall back to returning the raw stripped content
            try:
                return TOKEN_FILE.read_text(encoding="utf-8").strip()
            except Exception:
                pass
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

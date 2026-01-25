"""
Entry point wrapper for Windows installer.
Launches directly into 'aye chat' mode if no arguments provided.

- No args: aye.exe -> aye chat
- With args: aye.exe chat -r <path> -> aye chat -r <path> (passed through)
"""
import sys

# Fix Windows console encoding for PyInstaller frozen apps
# Must be done before importing anything that uses rich/typer
if sys.platform == 'win32':
    import io
    # Reconfigure stdout/stderr to use UTF-8 with error replacement
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    else:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    else:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# If no arguments provided, default to 'chat' command
if len(sys.argv) == 1:
    sys.argv.append('chat')

from aye.__main__ import app

if __name__ == '__main__':
    app()

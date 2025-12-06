"""
Entry point wrapper for Windows installer.
Launches directly into 'aye chat' mode if no arguments provided.

- No args: aye.exe -> aye chat
- With args: aye.exe chat -r <path> -> aye chat -r <path> (passed through)
"""
import sys

# If no arguments provided, default to 'chat' command
if len(sys.argv) == 1:
    sys.argv.append('chat')

from aye.__main__ import app

if __name__ == '__main__':
    app()

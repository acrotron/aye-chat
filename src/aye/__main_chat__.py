"""
Entry point wrapper for Windows installer.
Launches directly into 'aye chat' mode.

Transforms: aye.exe -r <path>  ->  aye chat -r <path>
"""
import sys

# Insert 'chat' as the first argument, keeping all other args after it
# sys.argv[0] is the exe name, sys.argv[1:] are the user args
# Result: [exe, 'chat', ...user_args]
sys.argv = [sys.argv[0], 'chat'] + sys.argv[1:]

from aye.__main__ import app

if __name__ == '__main__':
    app()

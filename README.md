[![Pylint](https://github.com/acrotron/aye-chat/actions/workflows/pylint.yml/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/pylint.yml)
[![CodeQL](https://github.com/acrotron/aye-chat/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/github-code-scanning/codeql)
[![Dependabot Updates](https://github.com/acrotron/aye-chat/actions/workflows/dependabot/dependabot-updates/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/dependabot/dependabot-updates)

# Aye Chat - AI-First development in your terminal

A terminal-native AI assistant that understands your project, edits your files safely, and helps you build faster — without ever leaving your flow.

![Aye Chat demo](https://welcome.ayechat.ai/images/main-flow.gif)

## Conceptual Summary

- 🧭 **Core Philosophy**: Flow first, trust by design, reversibility built-in.
- ⚙️ **Design Model**: Optimistic editing with automatic safety nets.
- 🔒 **Privacy Model**: Developer-defined boundaries (.gitignore/.ayeignore).
- 🧩 **Architecture**: Modular open-source core with plugin-driven extensibility.
- 🖥️ **Interface Model**: Unified CLI + Web experience with conversation continuity.
- 💡 **Goal**: Natural-language-driven coding in the developer’s native environment (the terminal).

## Key Features

- **Terminal-native experience** — built for developers who live in the CLI.  
- **Full-file generation and modification** — no copy-pasting between tools.  
- **Automatic snapshots, diff, and restore** — iterate safely, without Git overhead.  
- **Smart file awareness** — Aye Chat includes relevant project files automatically.  
- **Plugin architecture** — extend Aye Chat with your own commands.  


## Quick Start

1. **Install the tool**:
   ```bash
   pip install ayechat
   ```

2. **Authenticate**:
   ```bash
   aye auth login
   ```
   Visit https://ayechat.ai to obtain your personal access token.

3. **Start Interactive Chat**:
   ```bash
   aye chat
   ```

## Core Commands
### Authentication
```bash
aye auth login    # Configure your token
aye auth logout   # Remove stored credentials
```

### Interactive Chat
```bash
aye chat                          # Start chat with auto-detected files
aye chat --root ./src             # Specify project root directory          **<<<<<<<<<<<<<<<<<<<<<<<< broke here**
aye chat --inclue "*.js"          # Work with JavaScript files
aye chat --include "*.py,*.js"    # Work with multiple file types
```

In chat mode, you can use these built-in commands:
- `help` - Show available commands
- `exit`/`quit` - End chat session
- `new` - Start a new chat
- `history` - Show snapshot history
- `restore` - Restore files from snapshot
- `diff [file] [snapshot]` - Show differences in files
- `keep [N]` - Keep only N most recent snapshots

Any other command is treated as a shell command or AI prompt depending on context. Note that for the shell commands, you do not need to add '/' or any other special indicators: just type your command (e.g., "ls -la"). Some shell commands cannot be executed and will return an error or fail silently: these include those that alter terminal view (e.g., text editors) or attempt to switch shell context (e.g., "sudo su - ").

Except for Aye Chat own commands, which are matched and executed first, for each prompt, the tool attempts to find a shell command for the first token, and if successfull - execute it, if not - the prompt is treated as a message to AI.

### Snapshot Management
```bash
aye snap history              # List all snapshots
aye snap history src/main.py  # List snapshots for specific file
aye snap restore              # Restore latest snapshot
aye snap restore 001          # Restore specific snapshot
aye snap restore 001 file.py  # Restore specific file from snapshot
aye snap keep -n 5            # Keep only 5 most recent snapshots
aye snap cleanup -d 7         # Delete snapshots older than 7 days
```

### Configuration
```bash
aye config list                  # Show all settings
aye config get file_mask         # Get current file mask
aye config set file_mask "*.py"  # Set file mask
aye config delete file_mask      # Remove file mask setting
```
### Running using Visual Code

Example of launch.json you can use. Store this file under .vscode/

**Note:** Python 3.14.0, Visual Code and debugpy currently don't work.

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: Module",
            "type": "debugpy",
            "request": "launch",
            "module": "aye",
            "console": "integratedTerminal",
            "cwd": "${workspaceFolder}/src/",
            "justMyCode": true,
            "args": [  "--help" ],
         }
    ]
}
```

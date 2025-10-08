[![Pylint](https://github.com/acrotron/aye-chat/actions/workflows/pylint.yml/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/pylint.yml)
[![CodeQL](https://github.com/acrotron/aye-chat/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/github-code-scanning/codeql)
[![Dependabot Updates](https://github.com/acrotron/aye-chat/actions/workflows/dependabot/dependabot-updates/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/dependabot/dependabot-updates)

# Aye Chat - AI-Powered Terminal Code Generator

Aye Chat is a terminal-first AI coding assistant that helps you generate, modify, and manage code through natural language prompts. It provides both one-shot generation and interactive chat capabilities with built-in snapshot management for safe code modifications.

## Key Features

### Terminal-First Design
Unlike GUI-based tools, Aye Chat is built for developers who live in their terminals. All interactions happen through command-line interface with intuitive commands.

### Intelligent File Detection
- Automatically detects relevant source file types in your project
- Respects `.gitignore` and `.ayeignore` patterns to exclude irrelevant files
- Supports multiple comma-separated file masks (e.g., `"*.py,*.js"`)
- Skips binary and hidden files for efficient processing

### Safe Code Modification with Snapshots
- Creates timestamped snapshots of your files before any AI-generated changes
- "Latest" snapshot tracking for quick restores
- Interactive diff viewing to compare changes
- History management with pruning capabilities

### Interactive Chat Experience
- REPL-based chat interface with command completion
- Supports both AI prompts and shell commands in the same session
- Persistent chat sessions that remember context
- Built-in command help system

### Plugin Architecture
- Extensible plugin system for additional functionality
- Automatic plugin downloading based on user's license tier
- Modular design allowing easy addition of new features

### Configuration Management
- Persistent configuration storage
- Support for multiple settings including file masks

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
   (Auto-detect to be implemented soon. Currently, using '*.py' mask by default.) This will automatically detect your project's file types and include relevant source files.

4. **One-Shot Generation**:
   ```bash
   aye generate "Create a function that reverses a string"
   ```

## Usage Examples
### Authentication
```bash
aye auth login    # Configure your token
aye auth logout   # Remove stored credentials
```

### Interactive Chat
```bash
aye chat                          # Start chat with auto-detected files
aye chat --root ./src             # Specify project root directory
aye chat --file-mask "*.js"       # Work with JavaScript files
aye chat --file-mask "*.py,*.js"  # Work with multiple file types
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

Note that Python 3.14.0, Visual Code and debugpy currently don't work. So we are using Python 3.13.x

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: Module",
            "type": "debugpy", // currently python 3.14.0 and visual code don't work well together. You have to use python 3.13.x.
            "request": "launch",
            "module": "aye",
            "console": "integratedTerminal",
            "cwd": "${workspaceFolder}/src/",
            "justMyCode": true,
            "args": [  "--help" ], // adjust/extend for the argument(s) you want to use.
         }
    ]
}
```

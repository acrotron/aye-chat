[![Pylint](https://github.com/acrotron/aye-chat/actions/workflows/pylint.yml/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/pylint.yml)
[![CodeQL](https://github.com/acrotron/aye-chat/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/github-code-scanning/codeql)
[![Dependabot Updates](https://github.com/acrotron/aye-chat/actions/workflows/dependabot/dependabot-updates/badge.svg)](https://github.com/acrotron/aye-chat/actions/workflows/dependabot/dependabot-updates)

# Aye Chat: AI-First development in your terminal

If you ever try it - you are never going to go back to other tools. (That is, if your native environment is a terminal 🙂) 

It's not just another AI Slop, it's an ecosystem. Give it a try.

## Quick Start

1. **Install the tool**:
   ```bash
   pip install ayechat
   ```

2. **Start Interactive Chat**:
   ```bash
   aye chat
   ```

That's it!


![Aye Chat demo](https://welcome.ayechat.ai/images/main-flow.gif)


## Key Features

- 🖥️ **Terminal-native experience** — built for developers who live in the CLI.  
- 📁 **Full-file generation and modification** — no copy-pasting between tools.  
- 🔁 **Automatic snapshots, diff, and restore** — iterate safely, without Git overhead.  
- 🔒 **Privacy-aware design**: developer-defined boundaries with .gitignore and .ayeignore.
- 💡 **Smart file awareness** — Aye Chat includes relevant project files automatically.  
- 🧩 **Plugin architecture** — extend Aye Chat with your own commands.  


## Core Commands
### Authentication
```bash
aye auth login    # Configure your token
aye auth logout   # Remove stored credentials
```

### Interactive Chat
```bash
aye chat                          # Start chat with auto-detected files
aye chat --root ./src             # Specify project root directory
aye chat --inclue "*.js"          # Work with JavaScript files
aye chat --include "*.py,*.js"    # Work with multiple file types
```

In chat mode, you can use these built-in commands:
- `new` - Start a new chat.
  - **Tip**: Start a new chat when you move on to a new feature. Also, start a new chat if LLM starts going in circles.
- `history` - Show snapshot history
- `diff <file> [snapshot]` - Show diff of file with the latest snapshot, or a specified snapshot
- `restore [snapshot_id] [file]` - Restore all files from the latest snapshot or a specified snapshot; optionally for a specific file
- `keep [N]` - Keep only N most recent snapshots (10 by default)
- `model` - Select a different model. Selection will persist between sessions.
- `verbose [on|off]` - Toggle verbose mode to print out list of files included with user prompt (on/off, persists between sessions)
- `exit`, `quit`, `Ctrl+D` - Exit the chat session
- `help` - Show available commands

Any other command is treated as a shell command or AI prompt depending on context. Note that for the shell commands, you do not need to add '/' or any other special indicators: just type your command (e.g., "ls -la"). Some shell commands cannot be executed and will return an error or fail silently: these include those that alter terminal view (e.g., text editors) or attempt to switch shell context (e.g., "sudo su - ").

Except for Aye Chat own commands, which are matched and executed first, for each prompt, the tool attempts to find a shell command for the first token, and if successful - execute it, if not - the prompt is treated as a message to AI.

## Philosophy

**Aye Chat** reimagines coding as a conversation, not a sequence of commands.

Built for the terminal, it trusts AI to act — not wait for approval, while every change remains safe, transparent, and reversible.

By removing friction from creation, Aye Chat turns natural language into direct action, enabling developers to build software at the speed of thought.

## Configuration & Privacy

- Aye Chat respects `.gitignore` and `.ayeignore` — no unwanted file access.  
- Snapshots are stored locally in `.aye/` folder where `aye chat` command is executed.

## 🤝 Contributing

Aye Chat is open-source — we welcome contributions!
- Fork the repo and submit PRs.
- Open issues for bugs or ideas.
- Join our discussions on Discord [AyeChat](https://discord.gg/ZexraQYH77) server.




### 🔥 Ready to code with AI — without leaving your terminal?
👉 [Get started at ayechat.ai](https://ayechat.ai)






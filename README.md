# Aye Chat: AI-powered shell for Linux

**An AI assistant integrated into your shell: execute commands, edit files, and prompt AI, all in one seamless session.**

## Key Features

-   🚀 **Seamless Shell Integration** — Your shell, super-powered. Run `ls`, `git`, and even `vim` in the same session you chat with AI. No prefixes, no context switching.
-   🧠 **Zero-Config Context** — Aye Chat automatically detects your project's files, respecting your `.gitignore`, so you can start coding immediately.
-   ✍️ **Direct File Editing** — The AI directly edits and creates files in your project. No more copy-pasting code from a chat window.
-   ⏪ **Instant Undo** — AI made a mistake? A single `restore` command instantly reverts the last set of changes. Your work is always safe.
-   🖥️ **Terminal-Native Experience** — A rich, responsive UI built for developers who live in the command line.
-   🧩 **Extensible via Plugins** — The core experience is enhanced by plugins for shell execution, autocompletion, and more.

## Quick Start

1.  **Install the tool**:
    ```bash
    pip install ayechat
    ```

2.  **Start interactive chat in your source code folder**:
    ```bash
    aye chat
    ```

3.  **Start talking to your shell. That's it!**

![Aye Chat: The AI-powered shell for Linux](https://raw.githubusercontent.com/acrotron/aye-media/refs/heads/main/files/ai-shell.gif)

## Core Commands

### Authentication

```bash
aye auth login    # Configure your access token
aye auth logout   # Remove stored credentials
```

### Starting a Session

```bash
aye chat                          # Start chat with auto-detected files
aye chat --root ./src             # Specify a different project root
aye chat --include "*.js,*.css"   # Manually specify which files to include
```

### In-Chat Commands

In chat mode, your input is handled in a specific order:
1.  **Built-in Commands** (like `restore` or `model`).
2.  **Shell Commands** (like `ls -la` or `git status`).
3.  **AI Prompt** (everything else is sent to the LLM).

**Session & Model Control**
-   `new` - Start a fresh chat session.
-   `model` - Select a different AI model.
-   `verbose [on|off]` - Toggle printing the list of files sent to the AI.
-   `exit`, `quit`, `Ctrl+D` - Exit the chat.
-   `help` - Show available commands.

**Reviewing & Undoing AI Changes**
-   `restore` - Instantly undo the last set of changes made by the AI.
-   `history` - Show the history of changes made by the AI.
-   `diff <file>` - Compare the current version of a file against the last change.

**Shell Commands**
Any command that is not a built-in is treated as a shell command.
-   You can run standard commands like `ls -la`, `git status`, or `docker ps`.
-   **Interactive commands like `vim`, `nano`, and `less` work seamlessly**, handing control over to the editor and returning you to the chat when you're done.

## Philosophy

**Aye Chat** reimagines coding as a fluid conversation with an AI-powered shell.

Built for the terminal, it trusts the AI to act directly on your files—no approval diffs, no friction. This high-velocity workflow is made safe by a simple, instant `undo` command that keeps you in complete control.

By removing the barriers between thought, command, and code, Aye Chat lets you build software at the speed of your ideas.

## Configuration & Privacy

-   Aye Chat respects `.gitignore` and `.ayeignore`—your private files are never touched.
-   Change history and backups are stored locally in the `.aye/` folder within your project.

## 🤝 Contributing

Aye Chat is open-source — we welcome contributions!
-   Fork the repo and submit PRs.
-   Open issues for bugs or ideas.
-   Join our discussions on our [Discord Server](https://discord.gg/ZexraQYH77).

### 🔥 Ready to code with AI — without leaving your terminal?
👉 [Get started at ayechat.ai](https://ayechat.ai)
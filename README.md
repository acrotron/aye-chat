# Aye Chat: AI-powered terminal workspace <img src="https://flagcdn.com/16x12/us.png" width="20" align="top"/> <img src="https://flagcdn.com/16x12/ua.png" width="20" align="top" /> <img src="https://flagcdn.com/16x12/nl.png" width="20" align="top" /> <img src="https://flagcdn.com/16x12/eu.png" width="20" align="top"/>

**Your terminal, but with AI. Edit files, run commands, chat with AI - all in one session.**

## Install in 30 seconds

```bash
$ pip install ayechat
$ aye chat          # Start in any project
```

**macOS (Homebrew):**
```bash
brew tap acrotron/aye-chat
brew install aye-chat
```

**Windows (Installer):**

Download and run [aye-chat-setup.exe](https://github.com/acrotron/aye-chat/releases/latest/download/aye-chat-setup.exe)

![Aye Chat: The AI-powered terminal workspace](https://raw.githubusercontent.com/acrotron/aye-media/refs/heads/main/files/ai-shell.gif)

## What it does

```bash
$ aye chat
> fix the bug in server.py
✓ Fixed undefined variable on line 42

> vim server.py
[opens real vim, returns to chat after]

> refactor: make it async
✓ Updated server.py with async/await

> pytest
✗ Tests fail

> restore
✓ Reverted last changes

```

**No copy-pasting. No context switching. AI edits your files directly.**

## Why developers love it

- **Zero config** - Automatically reads your project files (respects .gitignore)
- **Instant undo** - `restore` command reverts any AI changes immediately  
- **Real shell** - Run `git`, `pytest`, even `vim` without leaving the chat
- **100% local backups** - Your code is safe, changes stored in `.aye/`
- **No prefixes** - Just type. Commands run, everything else goes to AI

#### Instant undo with Aye Chat's `Restore`
Aye Chat's `restore` command provides an instant and reliable safety net for any changes made by the AI. Developers can forge ahead and experiment knowing that application restore is just one simple command away.  

**Restore offers fine-grained control:**
- `restore <ordinal>`:  Lets users revert to a specific historical snapshot (e.g., `001`). This is useful for stepping back through multiple AI interactions.
- `restore <ordinal> <file>`:  Allows restoring a *specific file* from a particular snapshot. This is incredibly powerful for selectively reverting changes without affecting other files that might have been correctly updated.

**Restore works best when used alongside other commands:**
- `history`: to view available snapshots
- `diff`: to compare current files with previous versions

These commands provide a comprehensive system for reviewing, managing, and reverting code changes, keeping you in control. 

## Quick examples

```bash
# In your project directory:
aye chat

> refactor this to use dependency injection
> pytest
> fix what broke  
> git commit -m "refactored DI"
```

## Get started

1. **Install**: `pip install ayechat`
2. **Start chatting**: `aye chat` in any project folder

## Get started - Windows Installer                                                                                                            
For Windows users, the recommended way to install Aye Chat is with the official installer. It provides a standalone
application that requires no manual setup.

#### Installation

1.  Download the latest [aye-chat-setup.exe](https://github.com/acrotron/aye-chat/releases/latest/download/aye-chat-setup.exe) from the GitHub Releases page.
2.  Run the downloaded installer.
3.  During setup, it is highly recommended to keep the following options enabled:
    - `Add the application directory to your PATH`
    - `Add 'Open Aye Chat here' to folder context menu`

#### Usage

After installation, you can launch Aye Chat by:

- Typing `aye` in any terminal.
- Right-clicking a project folder and selecting **Open Aye Chat here**.                                                                                    

---

<details>
<summary>📚 Full command reference</summary>

## Core Commands

### Authentication

**Does not require authentication**

### Starting a Session

```bash
aye chat                          # Start chat with auto-detected files
aye chat --root ./src             # Specify a different project root
aye chat --include "*.js,*.css"   # Manually specify which files to include
```

### In-Chat Commands

Your input is handled in this order:
1. **Built-in Commands** (like `restore` or `model`)
2. **Shell Commands** (like `ls -la` or `git status`)
3. **AI Prompt** (everything else)

**Session & Model Control**
- `new` - Start a fresh chat session
- `model` - Select a different AI model
- `verbose [on|off]` - Toggle verbose output on or off
- `exit`, `quit`, `Ctrl+D` - Exit the chat
- `help` - Show available commands

**Reviewing & Undoing AI Changes**
- `restore`, `undo` - Instantly undo the last set of changes made by AI
- `history` - Show the history of changes made by AI
- `diff <file>` - Compare current version against last change

**Shell Commands**
- Run any command: `ls -la`, `git status`, `docker ps`
- Interactive programs work: `vim`, `nano`, `less`, `top`

</details>

<details>
<summary>⚙️ Configuration & Privacy</summary>

## Configuration

- Aye Chat respects `.gitignore` and `.ayeignore` - private files are never touched
- Change history and backups stored locally in `.aye/` folder
- Configure default model and preferences in `~/.aye/config.yaml`

## Privacy & Security

- All file backups are local only
- API calls only include files you explicitly work with
- No telemetry or usage tracking
- Open source - audit the code yourself

</details>

<details>
<summary>🧩 Plugins & Extensions</summary>

## Extensible via Plugins

The core experience is enhanced by plugins:
- Shell execution plugin
- Autocompletion plugin  
- Custom command plugins
- Model provider plugins

</details>
                                                                                                                                                             
<details>
<summary>🐧 NixOS/Nix Installation</summary>

```bash
# Run directly without installing
nix run github:acrotron/aye-chat

# Or install to your profile
nix profile install github:acrotron/aye-chat
```

</details>

## Contributing

Aye Chat is open source! We welcome contributions.

- **Report bugs**: [GitHub Issues](https://github.com/acrotron/aye-chat/issues)
- **Submit PRs**: Fork and contribute
- **Get help**: [Discord Community](https://discord.gg/ZexraQYH77)

## License

MIT License - see [LICENSE](LICENSE) file

## Disclaimer

review the [DISCLAIMER](DISCLAIMER) before using this software.

---

**Ready to code with AI without leaving your terminal?**

```bash
pip install ayechat && aye chat
```

[Wiki](https://github.com/acrotron/aye-chat/wiki) • [Discord](https://discord.gg/ZexraQYH77) • [GitHub](https://github.com/acrotron/aye-chat)

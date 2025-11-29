# Aye Chat: AI-powered terminal workspace <img src="https://flagcdn.com/16x12/us.png" width="20" align="top"/> <img src="https://flagcdn.com/16x12/ua.png" width="20" align="top" /> <img src="https://flagcdn.com/16x12/nl.png" width="20" align="top" /> <img src="https://flagcdn.com/16x12/eu.png" width="20" align="top"/>

**Your terminal, but with AI. Edit files, run commands, chat with AI - all in one session.**

## Install in 30 seconds

```bash
$ pip install ayechat
$ aye chat          # Start in any project
```

![Aye Chat: The AI-powered shell for Linux](https://raw.githubusercontent.com/acrotron/aye-media/refs/heads/main/files/ai-shell.gif)

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
<summary>🪟 Windows install notes</summary>                                                                                                                                                                                                          

On some Windows setups, `pip install` puts console scripts (like `aye.exe`) in a directory that is not on your `PATH` by default. If, after installing, `aye` is not recognized, you have a few options:

1. **Run via Python directly** (works immediately):

   ```powershell
   python -m aye chat
   ```

2. **Add the Python Scripts folder to `PATH`** so `aye` is found directly. For a typical user install this is something like:

   ```
   C:\Users\<you>\AppData\Roaming\Python\PythonXX\Scripts
   ```

   (Replace `PythonXX` with your actual Python version directory.)

3. **Use `pipx` for a smoother global install** (recommended on Windows):

   ```powershell
   pip install pipx
   pipx ensurepath
   pipx install ayechat

   aye chat
   ```

`pipx` installs Python CLI tools into an isolated environment and puts the entry points on your `PATH` automatically.

</details>

<details>
<summary>🪟 Windows install notes</summary>

On some Windows setups, `pip install` puts console scripts (like `aye.exe`) in a directory that is not on your `PATH` by default. If, after installing, `aye` is not recognized, you have a few options:

1. **Run via Python directly** (works immediately):

   ```powershell
   python -m aye chat
   ```

2. **Add the Python Scripts folder to `PATH`** so `aye` is found directly. For a typical user install this is something like:

   ```
   C:\Users\<you>\AppData\Roaming\Python\PythonXX\Scripts
   ```

   (Replace `PythonXX` with your actual Python version directory.)

3. **Use `pipx` for a smoother global install** (recommended on Windows):

   ```powershell
   pip install pipx
   pipx ensurepath
   pipx install ayechat

   aye chat
   ```

`pipx` installs Python CLI tools into an isolated environment and puts the entry points on your `PATH` automatically.

</details>

## Contributing

Aye Chat is open source! We welcome contributions.

- **Report bugs**: [GitHub Issues](https://github.com/acrotron/aye-chat/issues)
- **Submit PRs**: Fork and contribute
- **Get help**: [Discord Community](https://discord.gg/ZexraQYH77)

## License

MIT License - see [LICENSE](LICENSE) file

---

**Ready to code with AI without leaving your terminal?**

```bash
pip install ayechat && aye chat
```

[Wiki](https://github.com/acrotron/aye-chat/wiki) • [Discord](https://discord.gg/ZexraQYH77) • [GitHub](https://github.com/acrotron/aye-chat)

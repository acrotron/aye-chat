# User Acceptance Tests for Plugin System in Aye

This document outlines user acceptance tests (UATs) for the plugin system in Aye, implemented in `aye/controller/plugin_manager.py`, `aye/plugins/plugin_base.py`, and plugins in `aye/plugins/` (e.g., completer.py, shell_executor.py, auto_detect_mask.py). The plugin manager loads plugins from `~/.aye/plugins/`, handles commands via `on_command`, and integrates into chat for completers, shell execution, and auto-detection. Tests cover plugin discovery, command handling, and specific plugin functionalities like tab completion, shell commands, and mask detection. Emphasize user interactions in chat where plugins are active.

## Test Environment Setup
- Downloaded plugins must be present in `~/.aye/plugins/` (via `aye auth login`).
- Start Aye chat session (`aye chat`) to test plugin integration.
- Ensure plugins are loaded (check "Plugins loaded" message).
- For shell executor, have valid system commands available.
- For completer, test in a terminal with tab completion support.
- Run tests in a clean project directory for auto-detect mask.

## Test Cases

### 1. Plugin Manager Discovery and Loading

#### UAT-1.1: Successful Plugin Discovery and Loading
- **Given**: Plugins exist in `~/.aye/plugins/` with valid Python files (e.g., completer.py, shell_executor.py).
- **When**: The user starts `aye chat`.
- **Then**: PluginManager discovers and loads plugins, printing "Plugins loaded: completer, shell_executor, ...".
- **Verification**: Confirm plugins are in registry; completer enables tab completion in chat prompt.

#### UAT-1.2: No Plugins Directory
- **Given**: `~/.aye/plugins/` does not exist or is empty.
- **When**: Starting chat.
- **Then**: PluginManager skips discovery gracefully, no plugins loaded.
- **Verification**: Chat starts normally without plugin features; no errors.

#### UAT-1.3: Invalid Plugin File (Syntax Error)
- **Given**: A plugin file with syntax error in `~/.aye/plugins/`.
- **When**: Starting chat.
- **Then**: PluginManager skips the invalid plugin, loads others if any.
- **Verification**: Check that valid plugins still load; invalid one is ignored without crashing.

### 2. Completer Plugin

#### UAT-2.1: Tab Completion for Commands
- **Given**: Completer plugin loaded in chat session.
- **When**: The user types partial command (e.g., "ls") and presses Tab.
- **Then**: Completes to full system command (if available) with space appended.
- **Verification**: Use a terminal that supports completion; verify suggested commands from PATH.

#### UAT-2.2: Tab Completion for File Paths
- **Given**: Completer plugin loaded, after typing a command and space.
- **When**: The user types partial path (e.g., "src/") and presses Tab.
- **Then**: Completes to full path, appending "/" for directories.
- **Verification**: Ensure file system paths are completed accurately.

#### UAT-2.3: No Completion Match
- **Given**: Completer loaded, typing unknown command/path.
- **When**: Pressing Tab on invalid partial.
- **Then**: No completion occurs; typing continues.
- **Verification**: Confirm no errors or unexpected behavior.

### 3. Shell Executor Plugin

#### UAT-3.1: Execute Valid Shell Command
- **Given**: Shell executor plugin loaded in chat.
- **When**: The user types a valid command like "ls".
- **Then**: Executes the command, displays stdout in chat.
- **Verification**: Check output matches system `ls`; stderr shown if any.

#### UAT-3.2: Execute Invalid Shell Command
- **Given**: Plugin loaded.
- **When**: Typing a non-existent command like "nonexistentcmd".
- **Then**: Returns None (no response), command not executed.
- **Verification**: Ensure chat treats it as LLM input instead.

#### UAT-3.3: Execute Command with Args and Stderr
- **Given**: Plugin loaded.
- **When**: Typing "ls /nonexistent".
- **Then**: Executes, shows stderr error message.
- **Verification**: Confirm error output is displayed in red if applicable.

### 4. Auto Detect Mask Plugin

#### UAT-4.1: Auto Detect Mask in Empty Project
- **Given**: Plugin loaded, project has no source files.
- **When**: Starting chat without `--include`, plugin runs auto_detect_mask.
- **Then**: Falls back to default "*.py".
- **Verification**: Check session context message shows "*.py".

#### UAT-4.2: Auto Detect Mask with Source Files
- **Given**: Project has .py, .js, .ts files, no .gitignore ignoring them.
- **When**: Starting chat.
- **Then**: Detects and sets mask to "*.py,*.js,*.ts" (or similar).
- **Verification**: Confirm mask includes top extensions, ignoring hidden/binary files.

#### UAT-4.3: Auto Detect with Ignored Files
- **Given**: .gitignore excludes certain files.
- **When**: Running auto_detect_mask.
- **Then**: Ignores excluded files in detection.
- **Verification**: Only count non-ignored source files for extensions.

### 5. Generic Plugin Command Handling

#### UAT-5.1: Plugin Handles Known Command
- **Given**: A loaded plugin handles a command (e.g., completer for "get_completer").
- **When**: PluginManager.handle_command is called.
- **Then**: Returns the plugin's response dict.
- **Verification**: In chat, ensure response affects behavior (e.g., completer sets up completion).

#### UAT-5.2: No Plugin Handles Command
- **Given**: Loaded plugins, unknown command.
- **When**: handle_command called.
- **Then**: Returns None.
- **Verification**: Chat falls back to LLM processing.

#### UAT-5.3: Multiple Plugins Handle Command
- **Given**: Hypothetical multiple plugins for same command.
- **When**: Calling handle_command.
- **Then**: Returns first non-None response.
- **Verification**: Confirm order (discovery order) determines priority.

## Notes
- Plugin loading happens at chat start; restart chat to test changes.
- Premium plugins: Code checks tier, but tests assume free tier or mocked.
- Auto-detect ignores binary/hidden files and respects .gitignore.
- Shell commands are validated before execution to avoid security issues.
- Tests should verify no crashes on malformed plugins.

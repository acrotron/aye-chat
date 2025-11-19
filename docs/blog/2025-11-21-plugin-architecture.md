---
title: "Building a Modular CLI: The Plugin Architecture of Aye Chat"
date: 2025-11-21
draft: false
summary: "An exploration of Aye Chat's plugin-based architecture, detailing how the modular design enables flexibility, extensibility, and community contributions."
tags: ["plugins", "architecture", "design"]
---

## Overview

When designing a command-line tool, one of the most critical architectural decisions is how to manage complexity and enable future growth. A monolithic design, where all features are tightly integrated, can be fast to develop initially but quickly becomes difficult to maintain, test, and extend. For Aye Chat, we knew from the start that we wanted a system that was both powerful and flexible, capable of evolving with new technologies and community contributions. The answer was a **plugin-based architecture**.

By building Aye Chat on a lightweight core and implementing key features as discrete plugins, we've created a system that is modular, extensible, and easy to understand. This architecture allows us to add everything from shell command execution to UI enhancements without altering the central application logic. It's the foundation that enables rapid development and encourages community involvement.

This blog post explores the design and implementation of Aye Chat's plugin system, from the central `PluginManager` to the simple contract that all plugins follow, and showcases how it brings powerful features to life.

## Part 1: The Core - The Plugin Manager

The brain of the system is the `PluginManager`, defined in `aye/controller/plugin_manager.py`. Its role is simple but vital: discover, load, and communicate with all available plugins.

### Discovery and Loading

When Aye Chat starts, the `PluginManager`'s `discover()` method is called. It scans the `aye/plugins/` directory for Python files. It ignores special files like `__init__.py` and `plugin_base.py`, and then attempts to load each remaining file as a module.

```python
# aye/controller/plugin_manager.py

class PluginManager:
    # ...
    def discover(self) -> None:
        plugin_dir = Path(__file__).parent.parent / "plugins"
        # ...
        for f in plugin_dir.glob("*.py"):
            if f.name.startswith("__") or f.name == "plugin_base.py":
                continue
            self._load(f)
```

Inside the `_load` method, it dynamically imports the module and inspects its contents. It looks for any class that inherits from our `Plugin` base class. For each one it finds, it creates an instance, initializes it, and adds it to a central registry.

```python
# aye/controller/plugin_manager.py

def _load(self, file: Path):
    # ...
    for n, m in vars(mod).items():
        if isinstance(m, type) and issubclass(m, Plugin) and m is not Plugin:
            plug = m()
            # ...
            plug.init({"verbose": self.verbose, "debug": DEBUG})
            self.registry[plug.name] = plug
```

### The Command Bus

Once loaded, plugins are not called directly. Instead, the application communicates with them through a simple command bus pattern implemented in `handle_command`. When a part of the application needs a service that might be provided by a plugin, it calls `plugin_manager.handle_command("command_name", {params})`.

The manager then iterates through all registered plugins and calls their `on_command` method with the same arguments. The first plugin to return a non-`None` response wins, and its response is immediately returned to the caller. This creates a chain-of-responsibility pattern where plugins can claim ownership of a command.

```python
# aye/controller/plugin_manager.py

def handle_command(self, command_name: str, params: Dict[str, Any] = {}) -> Optional[Dict[str, Any]]:
    """Let plugins handle a command, return the first non-None response."""
    for plugin in self.all():
        response = plugin.on_command(command_name, params)
        if response is not None:
            return response
    return None
```

## Part 2: The Contract - The Plugin Base Class

For this system to work, all plugins must adhere to a common interface. This is defined by the abstract base class `Plugin` in `aye/plugins/plugin_base.py`. The contract is intentionally minimal:

```python
# aye/plugins/plugin_base.py

class Plugin(ABC):
    name: str
    version: str = "1.0.0"
    # ...

    def init(self, cfg: Dict[str, Any]) -> None:
        # ...

    def on_command(self, command_name: str, params: Dict[str, Any] = {}) -> Optional[Dict[str, Any]]:
        return None
```

A plugin needs to:
1.  Define a unique `name`.
2.  Optionally implement `init` to receive configuration.
3.  Implement `on_command` to listen for and respond to commands from the manager.

This simple, consistent interface is the key to the system's modularity.

## Part 3: Plugins in Action

Let's see how this architecture enables three very different features.

### Example 1: Adding a Core Feature with `shell_executor`

The ability to run shell commands is a cornerstone of Aye Chat. This is handled entirely by the `ShellExecutorPlugin` (`plugins/shell_executor.py`). In the main REPL, when a user's input is not a built-in command, it tries to execute it as a shell command:

```python
# aye/controller/repl.py

# ... inside the main loop
else:
    shell_response = conf.plugin_manager.handle_command("execute_shell_command", {"command": original_first, "args": tokens[1:]})
    if shell_response is not None:
        # ... process stdout/stderr
    else:
        # Not a shell command, so treat as an LLM prompt
        llm_response = invoke_llm(...)
```

The `ShellExecutorPlugin` listens for the `execute_shell_command` command. If it receives it, it validates the command and executes it, returning the result. If the input is not a valid shell command, it returns `None`, allowing the `PluginManager` to continue, and eventually letting the REPL fall back to treating the input as an AI prompt.

### Example 2: Enhancing the UI with `completer`

Plugins can also provide objects and services to the application. The REPL's tab-completion functionality is provided by the `CompleterPlugin` (`plugins/completer.py`). When the `PromptSession` is created, it asks the `PluginManager` for a completer object:

```python
# aye/controller/repl.py

BUILTIN_COMMANDS = [...]
completer_response = conf.plugin_manager.handle_command("get_completer", {"commands": BUILTIN_COMMANDS})
completer = completer_response["completer"] if completer_response else None

session = PromptSession(..., completer=completer, ...)
```

The `CompleterPlugin` is the only plugin that responds to the `get_completer` command. It constructs a `CmdPathCompleter` instance, which knows how to complete both built-in commands and file paths, and returns it in the response dictionary. This cleanly decouples the main application from the implementation details of autocompletion.

### Example 3: Abstracting Services with `local_model`

The plugin system is also perfect for abstracting external services. The `LocalModelPlugin` (`plugins/local_model.py`) acts as a gateway to various LLM providers. The `llm_invoker` first attempts to use a local model before falling back to the main API.

```python
# aye/controller/llm_invoker.py

def invoke_llm(...):
    # ...
    # 1. Try local model first
    local_response = plugin_manager.handle_command("local_model_invoke", {
        "prompt": prompt,
        # ...
    })
    
    if local_response is not None:
        return LLMResponse(...)
    
    # 2. Fall back to API
    # ...
```

The `LocalModelPlugin` listens for `local_model_invoke`. It checks environment variables for API keys and endpoints for various services (OpenAI-compatible, Databricks, Gemini) and routes the request accordingly. This allows users to easily switch between different LLM backends without any changes to the core application logic.

## Part 4: Known Issues and What's Next

The current plugin architecture provides a solid, modular foundation. However, its simplicity comes with limitations that we plan to address in future iterations.

### Known Issues

1.  **Static Plugin Discovery:** Plugins must reside within the `aye/plugins/` directory. This makes it difficult for users to install third-party plugins or for developers to manage their own private plugins without modifying the core application source.

2.  **Simple Command Handling:** The "first-to-respond-wins" command bus is straightforward but inflexible. It doesn't allow for multiple plugins to collaborate on a single command or for a plugin to modify the result of another.

3.  **Lack of Isolation:** All plugins run within the same process as the main application. A bug or crash in a single plugin can bring down the entire Aye Chat session, impacting stability.

4.  **No Dependency Management:** If two different plugins require conflicting versions of the same dependency, it can lead to runtime errors that are difficult to diagnose.

### What's Next: The Path to a True Ecosystem

Our roadmap for the plugin system is focused on transforming it from an internal modularization tool into a robust framework for a community-driven ecosystem.

1.  **Entry Point Discovery:** We plan to move away from directory scanning and adopt Python's standard `importlib.metadata` entry points. This will allow anyone to package and distribute their own plugins via PyPI (`pip install aye-chat-plugin-xyz`), which Aye Chat will automatically discover and load.

2.  **A More Sophisticated Event Bus:** The command bus will evolve into a full-fledged event bus. This will allow plugins to subscribe to a variety of events (e.g., `before_llm_invoke`, `after_file_update`, `on_shell_command_complete`) and chain their logic together, enabling much more complex and collaborative features.

3.  **Asynchronous Support:** We will introduce support for `async` plugin methods. This will allow plugins to perform non-blocking I/O operations efficiently without resorting to manual thread management, making the entire application more responsive.

4.  **Structured Configuration and Sandboxing:** Looking further ahead, we aim to provide a structured way for plugins to declare their configuration options and for users to manage them. We are also exploring lightweight sandboxing techniques to improve application stability by isolating plugin processes.

## Conclusion

Aye Chat's plugin architecture is the silent engine that drives its power and flexibility. By defining a simple contract and using a command bus pattern, we've created a system where features can be developed, tested, and added in isolation. This modularity not only makes the codebase cleaner and more maintainable but also opens the door for community contributions.

Whether it's adding support for a new LLM, creating a new UI enhancement, or integrating another developer tool, the plugin system provides a clear and easy path to extend Aye Chat's capabilities. It's the foundation upon which we will continue to build a smarter, more helpful coding companion for the terminal.

---
## About Aye Chat

Aye Chat is an open-source, AI-powered terminal workspace that brings the power of AI directly into your command-line workflow. Edit files, run commands, and chat with your codebase without ever leaving the terminal.

Find the project on GitHub: [https://github.com/acrotron/aye-chat](https://github.com/acrotron/aye-chat)

# AGENTS.md - Aye Chat Project Guidelines

This file provides context for AI coding assistants working on the Aye Chat codebase.

## Project Overview

**Aye Chat** is an AI-powered terminal workspace that brings AI directly into command-line workflows. It allows developers to edit files, run commands, and chat with their codebase without leaving the terminal.

### Core Philosophy

- **Optimistic workflow**: Files are written directly (LLM assumed correct), with instant `restore` to undo
- **Zero config**: Auto-detects project files, respects `.gitignore` and `.ayeignore`
- **Real shell**: Execute any command without leaving chat
- **Local-first**: All backups stored locally in `.aye/` directory

## Architecture

```
src/aye/
├── __main__.py          # CLI entry point (Typer app)
├── controller/          # Business logic, command handling
│   ├── repl.py          # Main chat REPL loop
│   ├── commands.py      # Command implementations
│   ├── command_handlers.py  # Individual command handlers
│   ├── llm_invoker.py   # LLM API invocation
│   ├── llm_handler.py   # LLM response processing
│   └── plugin_manager.py    # Plugin discovery and management
├── model/               # Data models, business logic
│   ├── api.py           # HTTP API client
│   ├── auth.py          # Token management (~/.ayecfg)
│   ├── config.py        # Constants, system prompt, models
│   ├── snapshot/        # File versioning (backup/restore)
│   ├── source_collector.py  # File collection with ignore patterns
│   ├── file_processor.py    # Path normalization, filtering
│   └── index_manager.py     # RAG vector database
├── presenter/           # UI output (Rich-based)
│   ├── repl_ui.py       # Chat UI components
│   ├── cli_ui.py        # CLI output formatting
│   ├── streaming_ui.py  # Streaming response display
│   └── diff_presenter.py    # Diff visualization
└── plugins/             # Plugin implementations
    ├── plugin_base.py   # Abstract base class
    ├── at_file_completer.py  # @file reference completion
    ├── completer.py     # Command/path completion
    └── shell_executor.py    # Shell command execution
```

## Coding Conventions

### Python Style

- **Python 3.10+** - Use modern syntax (type hints, `|` union, walrus operator where clear)
- **Type hints** - Required for function signatures, optional for locals
- **Docstrings** - Google style, required for public functions
- **Line length** - 100 characters soft limit
- **Imports** - Standard library, third-party, then local; alphabetized within groups

```python
# Good
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx
from rich import print as rprint

from aye.model.auth import get_user_config
from aye.presenter.repl_ui import print_error


def process_files(
    files: List[Dict[str, str]],
    root: Path,
    *,
    verbose: bool = False,
) -> Optional[str]:
    """Process files and return batch ID.
    
    Args:
        files: List of file dicts with 'file_name' and 'file_content'
        root: Project root path
        verbose: Enable verbose output
        
    Returns:
        Batch ID if successful, None otherwise
    """
```

### Error Handling

- Use specific exceptions, not bare `except:`
- Log errors with context using Rich formatting
- Graceful degradation - don't crash on non-critical errors

```python
# Good
try:
    content = file_path.read_text(encoding='utf-8')
except UnicodeDecodeError:
    # Skip binary files gracefully
    if verbose:
        rprint(f"[yellow]Skipping binary file: {file_path}[/]")
    return None
except PermissionError as e:
    rprint(f"[red]Permission denied:[/] {file_path}")
    raise
```

### Path Handling

- Always use `pathlib.Path`, never string manipulation for paths
- Use `.as_posix()` for cross-platform path strings in output
- Resolve paths against project root, not CWD

```python
# Good
from pathlib import Path

def resolve_file(file_name: str, root: Path) -> Path:
    p = Path(file_name)
    if p.is_absolute():
        return p
    return (root / p).resolve()
```

### Configuration

- User config stored in `~/.ayecfg` (flat INI-style file)
- Use `get_user_config()` / `set_user_config()` from `aye.model.auth`
- Environment variables override file config (prefix: `AYE_`)

```python
from aye.model.auth import get_user_config, set_user_config

# Reading config with default
verbose = get_user_config("verbose", "off").lower() == "on"

# Writing config
set_user_config("selected_model", "gpt-4")
```

## Testing

### Test Organization

```
tests/
├── test_*.py            # Unit tests (pytest)
├── ua/                  # User acceptance test specs (markdown)
└── e2e/                 # End-to-end tests
```

### Test Patterns

- Use `pytest` with fixtures
- Mock external dependencies (API calls, file system where needed)
- Use `tmp_path` fixture for file system tests
- Test both success and error paths

```python
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from aye.model.file_processor import make_paths_relative


class TestMakePathsRelative:
    def test_absolute_path_under_root(self, tmp_path):
        """Test converting absolute paths under root to relative."""
        root = tmp_path
        files = [{"file_name": str(root / "src" / "main.py")}]
        
        result = make_paths_relative(files, root)
        
        assert result[0]["file_name"] == "src/main.py"
    
    def test_handles_missing_file_name_key(self):
        """Test graceful handling of malformed input."""
        files = [{"other_key": "value"}]
        
        result = make_paths_relative(files, Path.cwd())
        
        assert "file_name" not in result[0]
```

### Running Tests

```bash
# All tests
pytest tests/ -v

# Specific file
pytest tests/test_file_processor.py -v

# With coverage
pytest tests/ -v --cov=src/aye --cov-report=term-missing
```

## Key Abstractions

### Plugin System

Plugins extend functionality without modifying core code.

```python
from aye.plugins.plugin_base import Plugin

class MyPlugin(Plugin):
    name = "my_plugin"
    version = "1.0.0"
    premium = "free"  # or "premium"
    
    def init(self, cfg: Dict[str, Any]) -> None:
        """Called once at startup."""
        super().init(cfg)
        # Setup code here
    
    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle a command. Return None if not handled."""
        if command_name == "my_command":
            return {"result": "success"}
        return None
```

### Snapshot System

Provides undo/restore functionality for AI changes.

```python
from aye.model.snapshot import apply_updates, restore_snapshot, list_snapshots

# Apply file updates (creates snapshot first)
batch_id = apply_updates(
    updated_files=[{"file_name": "main.py", "file_content": "..."}],
    prompt="refactor to async",
    root=project_root,
)

# Restore from snapshot
restore_snapshot(ordinal="001", file_name=None)  # All files
restore_snapshot(ordinal="001", file_name="main.py")  # Single file
```

### LLM Response Processing

```python
from aye.model.models import LLMResponse
from aye.controller.llm_handler import process_llm_response

# LLMResponse contains:
# - summary: str (answer text)
# - updated_files: List[Dict] (files to write)
# - chat_id: Optional[int]

response = LLMResponse(
    summary="Updated main.py with async/await",
    updated_files=[{"file_name": "main.py", "file_content": "..."}],
    chat_id=12345,
)

new_chat_id = process_llm_response(
    response=response,
    conf=conf,
    console=console,
    prompt=user_prompt,
)
```

## Common Tasks

### Adding a New Command

1. Add handler function in `command_handlers.py`:

```python
def handle_mycommand(tokens: list, conf: Any) -> None:
    """Handle the 'mycommand' command."""
    if len(tokens) > 1:
        # Process arguments
        pass
    else:
        rprint("[yellow]Usage: mycommand <arg>[/]")
```

2. Add to REPL dispatch in `repl.py`:

```python
BUILTIN_COMMANDS = [..., "mycommand"]

# In the main loop:
elif lowered_first == "mycommand":
    telemetry.record_command("mycommand", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
    handle_mycommand(tokens, conf)
```

3. Add tests in `tests/test_command_handlers.py`

### Adding a New Plugin

1. Create plugin file in `src/aye/plugins/`:

```python
from typing import Dict, Any, Optional
from .plugin_base import Plugin

class MyFeaturePlugin(Plugin):
    name = "my_feature"
    version = "1.0.0"
    premium = "free"
    
    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "do_my_feature":
            result = self._process(params)
            return {"result": result}
        return None
    
    def _process(self, params: Dict[str, Any]) -> str:
        # Implementation
        pass
```

2. Plugin is auto-discovered from `~/.aye/plugins/` or bundled in package

### Modifying File Processing

Files flow through this pipeline:

1. `filter_unchanged_files()` - Skip files with no changes
2. `make_paths_relative()` - Normalize paths relative to project root  
3. `check_files_against_ignore_patterns()` - Respect .gitignore/.ayeignore
4. `apply_updates()` - Snapshot current state, write new files

## Do's and Don'ts

### Do

- ✅ Use `Path` objects for all file operations
- ✅ Handle encoding errors gracefully (skip binary files)
- ✅ Respect `.gitignore` and `.ayeignore` patterns
- ✅ Write full files when updating (no diffs/patches)
- ✅ Add tests for new functionality
- ✅ Use Rich for terminal output (`rprint`, `Console`)
- ✅ Follow existing patterns in nearby code

### Don't

- ❌ Use bare `except:` clauses
- ❌ Assume CWD equals project root
- ❌ Write partial files or use diff notation
- ❌ Import from `aye.controller` in `aye.model` (avoid circular deps)
- ❌ Modify files without creating a snapshot first
- ❌ Store sensitive data in plain text (use auth module)
- ❌ Block the main thread with long operations (use threading)

## Dependencies

Core dependencies (see `pyproject.toml`):

- `typer` - CLI framework
- `rich` - Terminal formatting
- `prompt_toolkit` - REPL/completion
- `httpx` - HTTP client
- `pathspec` - Gitignore pattern matching
- `chromadb` - Vector database for RAG (optional)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AYE_TOKEN` | API authentication token | None |
| `AYE_CHAT_API_URL` | Custom API endpoint | `https://api.ayechat.ai` |
| `AYE_SSLVERIFY` | SSL certificate verification | `on` |
| `AYE_STREAM_DEBUG` | Enable streaming debug output | `off` |
| `AYE_STREAM_VIEWPORT_HEIGHT` | Streaming viewport lines | `15` |

## AGENTS.md Discovery

Aye Chat automatically discovers and includes `AGENTS.md` files as additional context:

1. `./.aye/AGENTS.md` (highest priority)
2. `./AGENTS.md` in project root
3. Walk up parent directories for `.aye/AGENTS.md` or `AGENTS.md`

First match wins. Content is prepended to the system prompt.

---

*This file helps AI assistants understand the Aye Chat codebase and generate consistent, high-quality code.*

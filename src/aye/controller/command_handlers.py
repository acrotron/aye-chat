import os
import shlex
from pathlib import Path
from typing import Optional, Any, List

from prompt_toolkit import PromptSession
from rich import print as rprint
from rich.console import Console

from aye.model.auth import get_user_config, set_user_config
from aye.model.config import MODELS
from aye.presenter.repl_ui import print_error
from aye.controller.llm_invoker import invoke_llm
from aye.controller.llm_handler import process_llm_response, handle_llm_error


def handle_cd_command(tokens: list[str], conf: Any) -> bool:
    """Handle 'cd' command: change directory and update conf.root."""
    if len(tokens) < 2:
        target_dir = str(Path.home())
    else:
        target_dir = ' '.join(tokens[1:])
    try:
        os.chdir(target_dir)
        conf.root = Path.cwd()
        rprint(str(conf.root))
        return True
    except Exception as e:
        print_error(e)
        return False


def handle_model_command(session: Optional[PromptSession], models: list, conf: Any, tokens: list):
    """Handle the 'model' command for model selection."""
    if len(tokens) > 1:
        try:
            num = int(tokens[1])
            if 1 <= num <= len(models):
                selected_id = models[num - 1]["id"]
                
                # Check if this is an offline model and trigger download if needed
                selected_model = models[num - 1]
                if selected_model.get("type") == "offline":
                    download_response = conf.plugin_manager.handle_command("download_offline_model", {
                        "model_id": selected_id,
                        "model_name": selected_model["name"],
                        "size_gb": selected_model.get("size_gb", 0)
                    })
                    if download_response and not download_response.get("success", True):
                        rprint(f"[red]Failed to download model: {download_response.get('error', 'Unknown error')}[/]")
                        return
                
                conf.selected_model = selected_id
                set_user_config("selected_model", selected_id)
                rprint(f"[green]Selected model: {models[num - 1]['name']}[/]")
            else:
                rprint("[red]Invalid model number.[/]")
        except ValueError:
            rprint("[red]Invalid input. Use a number.[/]")
        return

    current_id = conf.selected_model
    current_name = next((m['name'] for m in models if m['id'] == current_id), "Unknown")

    rprint(f"[yellow]Currently selected:[/] {current_name}\n")
    rprint("[yellow]Available models:[/]")
    for i, m in enumerate(models, 1):
        model_info = f"  {i}. {m['name']}"
        if m.get("type") == "offline":
            size_gb = m.get("size_gb", 0)
            model_info += f" [{size_gb}GB download]"
        rprint(model_info)
    rprint("")

    if not session:
        return

    choice = session.prompt("Enter model number to select (or Enter to keep current): ").strip()
    if choice:
        try:
            num = int(choice)
            if 1 <= num <= len(models):
                selected_id = models[num - 1]["id"]
                
                # Check if this is an offline model and trigger download if needed
                selected_model = models[num - 1]
                if selected_model.get("type") == "offline":
                    download_response = conf.plugin_manager.handle_command("download_offline_model", {
                        "model_id": selected_id,
                        "model_name": selected_model["name"],
                        "size_gb": selected_model.get("size_gb", 0)
                    })
                    if download_response and not download_response.get("success", True):
                        rprint(f"[red]Failed to download model: {download_response.get('error', 'Unknown error')}[/]")
                        return
                
                conf.selected_model = selected_id
                set_user_config("selected_model", selected_id)
                rprint(f"[green]Selected: {models[num - 1]['name']}[/]")
            else:
                rprint("[red]Invalid number.[/]")
        except ValueError:
            rprint("[red]Invalid input.[/]")


def handle_verbose_command(tokens: list):
    """Handle the 'verbose' command."""
    if len(tokens) > 1:
        val = tokens[1].lower()
        if val in ("on", "off"):
            set_user_config("verbose", val)
            rprint(f"[green]Verbose mode set to {val.title()}[/]")
        else:
            rprint("[red]Usage: verbose on|off[/]")
    else:
        current = get_user_config("verbose", "off")
        rprint(f"[yellow]Verbose mode is {current.title()}[/]")


def handle_debug_command(tokens: list):
    """Handle the 'debug' command."""
    if len(tokens) > 1:
        val = tokens[1].lower()
        if val in ("on", "off"):
            set_user_config("debug", val)
            rprint(f"[green]Debug mode set to {val.title()}[/]")
        else:
            rprint("[red]Usage: debug on|off[/]")
    else:
        current = get_user_config("debug", "off")
        rprint(f"[yellow]Debug mode is {current.title()}[/]")


def handle_completion_command(tokens: list) -> Optional[str]:
    """Handle the 'completion' command for switching completion styles.
    
    Returns:
        The new completion style if changed ('readline' or 'multi'), None otherwise.
    """
    if len(tokens) > 1:
        val = tokens[1].lower()
        if val in ("readline", "multi"):
            set_user_config("completion_style", val)
            rprint(f"[green]Completion style set to {val.title()}[/]")
            return val
        else:
            rprint("[red]Usage: completion readline|multi[/]")
            rprint("[yellow]  readline - Traditional readline-like completion (default)[/]")
            rprint("[yellow]  multi    - Multi-column completion with complete-while-typing[/]")
            return None
    else:
        current = get_user_config("completion_style", "readline")
        rprint(f"[yellow]Completion style is {current.title()}[/]")
        rprint("[yellow]Use 'completion readline' or 'completion multi' to change[/]")
        return None


def _should_skip_path_part(part: str, explicitly_allowed_parts: set) -> bool:
    """Check if a path part should be skipped (hidden and not explicitly allowed).
    
    Args:
        part: A single path component to check.
        explicitly_allowed_parts: Set of path parts that should NOT be skipped
                                  even if they start with '.'.
    
    Returns:
        True if the part should be skipped, False otherwise.
    """
    # Not hidden - don't skip
    if not part.startswith('.'):
        return False
    
    # Hidden but explicitly allowed - don't skip
    if part in explicitly_allowed_parts:
        return False
    
    # Hidden and not explicitly allowed - skip
    return True


def _get_explicitly_allowed_parts(pattern: str) -> set:
    """Extract all explicitly specified hidden directory/file names from a pattern.
    
    For example:
    - '.github' -> {'.github'}
    - '.github/' -> {'.github'}
    - '.github/workflows' -> {'.github'}
    - '.vscode/.hidden' -> {'.vscode', '.hidden'}
    
    Args:
        pattern: The original pattern string.
    
    Returns:
        Set of path parts that start with '.' and were explicitly specified.
    """
    # Normalize separators and remove trailing slashes
    normalized = pattern.replace('\\', '/').rstrip('/')
    
    # Split into parts and collect hidden ones
    parts = normalized.split('/')
    hidden_parts = {part for part in parts if part.startswith('.') and part not in ('.', '..')}
    
    return hidden_parts


def _expand_file_patterns(patterns: list[str], conf: Any) -> list[str]:
    """Expand wildcard patterns and return a list of existing file paths.
    
    Supports:
    - Direct file paths: src/main.py
    - Directory paths: src/ or src (includes all files recursively)
    - Hidden directories: .github, .vscode (when explicitly specified)
    - Single wildcards: *.py, src/*.py
    - Double wildcards: src/**/*.py, src/** (recursive)
    
    When expanding directories or using **, respects .gitignore and .ayeignore patterns.
    Hidden files/directories are skipped UNLESS the user explicitly specifies them.
    """
    # Lazy import to avoid circular dependencies
    from aye.model.ignore_patterns import load_ignore_patterns
    
    expanded_files = []
    ignore_spec = None  # Lazy load only if needed
    verbose = getattr(conf, 'verbose', False)
    
    # Resolve conf.root to handle path normalization issues
    root_resolved = conf.root.resolve()
    
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        
        # Get all explicitly specified hidden parts from the pattern
        explicitly_allowed = _get_explicitly_allowed_parts(pattern)
        
        if verbose and explicitly_allowed:
            rprint(f"[dim]Pattern '{pattern}' explicitly allows hidden: {explicitly_allowed}[/dim]")
        
        # Remove trailing slash for consistency
        pattern_clean = pattern.rstrip('/').rstrip('\\')
        
        # Build the target path and resolve it
        # Use forward slashes for consistency, then let Path handle it
        pattern_normalized = pattern_clean.replace('\\', '/')
        direct_path = (root_resolved / pattern_normalized)
        
        # Try to resolve - but don't fail if path doesn't exist yet
        try:
            direct_path_resolved = direct_path.resolve()
        except (OSError, ValueError):
            direct_path_resolved = direct_path
        
        if verbose:
            rprint(f"[dim]Checking path: {direct_path_resolved}[/dim]")
            rprint(f"[dim]  exists={direct_path_resolved.exists()}, is_dir={direct_path_resolved.is_dir()}, is_file={direct_path_resolved.is_file()}[/dim]")
        
        # Check if it's a direct file path
        if direct_path_resolved.is_file():
            expanded_files.append(pattern_normalized)
            if verbose:
                rprint(f"[dim]'{pattern}' is a file[/dim]")
            continue
        
        # Check if it's a directory - include all files recursively
        if direct_path_resolved.is_dir():
            if ignore_spec is None:
                ignore_spec = load_ignore_patterns(root_resolved)
            
            file_count = 0
            if verbose:
                rprint(f"[dim]'{pattern}' is a directory, scanning recursively...[/dim]")
            
            try:
                for file_path in direct_path_resolved.rglob('*'):
                    if not file_path.is_file():
                        continue
                    try:
                        # Get path relative to the project root (not the target directory)
                        relative_path = file_path.relative_to(root_resolved)
                        rel_path_str = relative_path.as_posix()
                        
                        # Skip ignored files
                        if ignore_spec.match_file(rel_path_str):
                            continue
                        
                        # Skip hidden files/directories, but allow explicitly specified ones
                        if any(_should_skip_path_part(part, explicitly_allowed) 
                               for part in relative_path.parts):
                            continue
                        
                        expanded_files.append(rel_path_str)
                        file_count += 1
                    except ValueError as e:
                        if verbose:
                            rprint(f"[dim]  Skipping {file_path}: {e}[/dim]")
            except Exception as e:
                if verbose:
                    rprint(f"[dim]  Error scanning directory: {e}[/dim]")
            
            if verbose:
                rprint(f"[dim]Expanded '{pattern}' to {file_count} file(s)[/dim]")
            continue
        
        # Handle ** patterns for recursive matching
        if '**' in pattern:
            if ignore_spec is None:
                ignore_spec = load_ignore_patterns(root_resolved)
            
            # If pattern ends with ** or **/, convert to **/* to match files
            glob_pattern = pattern_normalized
            if glob_pattern.endswith('**'):
                glob_pattern = glob_pattern + '/*'
            
            if verbose:
                rprint(f"[dim]Using glob pattern: {glob_pattern}[/dim]")
            
            file_count = 0
            try:
                matched_paths = list(root_resolved.glob(glob_pattern))
                for matched_path in matched_paths:
                    if not matched_path.is_file():
                        continue
                    try:
                        relative_path = matched_path.relative_to(root_resolved)
                        rel_path_str = relative_path.as_posix()
                        
                        # Skip ignored files for ** patterns
                        if ignore_spec.match_file(rel_path_str):
                            continue
                        
                        # Skip hidden files/directories, but allow explicitly specified ones
                        if any(_should_skip_path_part(part, explicitly_allowed)
                               for part in relative_path.parts):
                            continue
                        
                        expanded_files.append(rel_path_str)
                        file_count += 1
                    except ValueError:
                        pass
            except Exception as e:
                if verbose:
                    rprint(f"[dim]  Error with glob: {e}[/dim]")
            
            if verbose:
                rprint(f"[dim]Expanded '{pattern}' to {file_count} file(s)[/dim]")
            continue
        
        # Use glob to expand single wildcards (no filtering for explicit patterns)
        if verbose:
            rprint(f"[dim]Trying simple glob: {pattern_normalized}[/dim]")
        
        try:
            matched_paths = list(root_resolved.glob(pattern_normalized))
            
            for matched_path in matched_paths:
                if matched_path.is_file():
                    try:
                        relative_path = matched_path.relative_to(root_resolved)
                        expanded_files.append(relative_path.as_posix())
                    except ValueError:
                        expanded_files.append(pattern_normalized)
                elif matched_path.is_dir():
                    # Glob matched a directory - recursively include files
                    if ignore_spec is None:
                        ignore_spec = load_ignore_patterns(root_resolved)
                    
                    if verbose:
                        rprint(f"[dim]Glob matched directory '{matched_path.name}', scanning recursively...[/dim]")
                    
                    for file_path in matched_path.rglob('*'):
                        if not file_path.is_file():
                            continue
                        try:
                            relative_path = file_path.relative_to(root_resolved)
                            rel_path_str = relative_path.as_posix()
                            
                            if ignore_spec.match_file(rel_path_str):
                                continue
                            
                            if any(_should_skip_path_part(part, explicitly_allowed)
                                   for part in relative_path.parts):
                                continue
                            
                            expanded_files.append(rel_path_str)
                        except ValueError:
                            pass
        except Exception as e:
            if verbose:
                rprint(f"[dim]  Error with simple glob: {e}[/dim]")
        
        # If still no matches and pattern looks like a directory path, try one more time
        # This handles edge cases where is_dir() returns False but the directory exists
        if not expanded_files or expanded_files == []:
            if direct_path.exists() and not direct_path.is_file():
                if verbose:
                    rprint(f"[dim]Path exists but wasn't detected as dir, trying fallback scan...[/dim]")
                
                if ignore_spec is None:
                    ignore_spec = load_ignore_patterns(root_resolved)
                
                try:
                    for file_path in direct_path.rglob('*'):
                        if not file_path.is_file():
                            continue
                        try:
                            relative_path = file_path.relative_to(root_resolved)
                            rel_path_str = relative_path.as_posix()
                            
                            if ignore_spec.match_file(rel_path_str):
                                continue
                            
                            if any(_should_skip_path_part(part, explicitly_allowed)
                                   for part in relative_path.parts):
                                continue
                            
                            if rel_path_str not in expanded_files:
                                expanded_files.append(rel_path_str)
                        except ValueError:
                            pass
                except Exception as e:
                    if verbose:
                        rprint(f"[dim]  Fallback scan error: {e}[/dim]")
    
    return expanded_files


def handle_with_command(
    prompt: str, 
    conf: Any, 
    console: Console, 
    chat_id: int, 
    chat_id_file: Path
) -> Optional[int]:
    """Handle the 'with' command for file-specific prompts with wildcard support.
    
    Supports:
    - Single files: with main.py: prompt
    - Multiple files: with main.py, utils.py: prompt
    - Wildcards: with *.py: prompt
    - Directories: with src: prompt (includes all files recursively)
    - Hidden directories: with .github: prompt (explicitly allowed)
    - Double wildcards: with src/**: prompt, with **/*.py: prompt
    
    Args:
        prompt: The full prompt string starting with 'with'
        conf: Configuration object
        console: Rich console for output
        chat_id: Current chat ID
        chat_id_file: Path to chat ID file
        
    Returns:
        New chat_id if available, None otherwise
    """
    try:
        parts = prompt.split(":", 1)
        if len(parts) != 2:
            rprint("[red]Error: 'with' command requires format: with <files>: <prompt>[/red]")
            return None
            
        file_list_str, new_prompt_str = parts
        file_list_str = file_list_str.strip()[4:].strip()  # Remove 'with ' prefix

        if not file_list_str:
            rprint("[red]Error: File list cannot be empty for 'with' command.[/red]")
            return None
        if not new_prompt_str.strip():
            rprint("[red]Error: Prompt cannot be empty after the colon.[/red]")
            return None

        # Parse file patterns (can include wildcards, directories, **)
        file_patterns = [f.strip() for f in file_list_str.replace(",", " ").split() if f.strip()]
        
        if getattr(conf, 'verbose', False):
            rprint(f"[dim]File patterns: {file_patterns}[/dim]")
            rprint(f"[dim]Project root: {conf.root}[/dim]")
        
        # Expand wildcards and directories to get actual file paths
        expanded_files = _expand_file_patterns(file_patterns, conf)
        
        if not expanded_files:
            rprint("[red]Error: No files found matching the specified patterns.[/red]")
            rprint(f"[dim]Patterns tried: {file_patterns}[/dim]")
            rprint(f"[dim]Project root: {conf.root}[/dim]")
            return None
        
        explicit_source_files = {}
        read_errors = []
        
        for file_name in expanded_files:
            file_path = conf.root / file_name
            if not file_path.is_file():
                rprint(f"[yellow]File not found, skipping: {file_name}[/yellow]")
                continue
            try:
                explicit_source_files[file_name] = file_path.read_text(encoding="utf-8")
            except Exception as e:
                read_errors.append(f"{file_name}: {e}")
                continue
        
        if not explicit_source_files:
            rprint("[red]Error: No readable files found.[/red]")
            if read_errors:
                for err in read_errors[:5]:
                    rprint(f"[dim]  {err}[/dim]")
            return None
        
        # Show which files were included
        if getattr(conf, 'verbose', False) or len(explicit_source_files) != len(expanded_files):
            file_list = ', '.join(list(explicit_source_files.keys())[:10])
            suffix = f" (+{len(explicit_source_files) - 10} more)" if len(explicit_source_files) > 10 else ""
            rprint(f"[cyan]Including {len(explicit_source_files)} file(s): {file_list}{suffix}[/cyan]")

        llm_response = invoke_llm(
            prompt=new_prompt_str.strip(),
            conf=conf,
            console=console,
            plugin_manager=conf.plugin_manager,
            chat_id=chat_id,
            verbose=getattr(conf, 'verbose', False),
            explicit_source_files=explicit_source_files
        )
        
        if llm_response:
            new_chat_id = process_llm_response(
                response=llm_response, 
                conf=conf, 
                console=console, 
                prompt=new_prompt_str.strip(), 
                chat_id_file=chat_id_file if llm_response.chat_id else None
            )
            return new_chat_id
        else:
            rprint("[yellow]No response from LLM.[/]")
            return None
            
    except Exception as exc:
        handle_llm_error(exc)
        return None


_BLOG_PROMPT_PREAMBLE = (
    "You are going to write a technical blog post as a deep dive into what we implemented in this chat session.\n"
    "\n"
    "Requirements:\n"
    "- Derive the narrative and details primarily from this *current chat session* (the conversation so far).\n"
    "- The blog post must be written in Markdown.\n"
    "- Write the blog post to a file named `blog.md` (project root).\n"
    "- Return a JSON object that follows the required schema, and include exactly one updated file: `blog.md`.\n"
    "  (Unless the user explicitly asked for additional files.)\n"
    "\n"
)


def handle_blog_command(
    tokens: List[str],
    conf: Any,
    console: Console,
    chat_id: int,
    chat_id_file: Path,
) -> Optional[int]:
    """Handle the 'blog' command.

    Syntax:
        blog <intent>

    This wraps the user's intent with a pre-defined instruction block that:
    - forces output to blog.md
    - asks the model to derive content from the current chat session

    Returns:
        New chat_id if available, None otherwise
    """
    try:
        intent = " ".join(tokens[1:]).strip() if len(tokens) > 1 else ""
        if not intent:
            rprint("[red]Usage:[/] blog <text to describe blog post intent>")
            return None

        llm_prompt = (
            f"{_BLOG_PROMPT_PREAMBLE}\n"
            f"User intent: {intent}\n"
        )

        llm_response = invoke_llm(
            prompt=llm_prompt,
            conf=conf,
            console=console,
            plugin_manager=conf.plugin_manager,
            chat_id=chat_id,
            verbose=getattr(conf, 'verbose', False),
            explicit_source_files=None,
        )

        if llm_response:
            # Store a concise prompt label in snapshots/history.
            snapshot_prompt = f"blog {intent}".strip()
            new_chat_id = process_llm_response(
                response=llm_response,
                conf=conf,
                console=console,
                prompt=snapshot_prompt,
                chat_id_file=chat_id_file if llm_response.chat_id else None,
            )
            return new_chat_id

        rprint("[yellow]No response from LLM.[/]")
        return None

    except Exception as exc:
        handle_llm_error(exc)
        return None

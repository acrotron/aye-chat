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


def _expand_file_patterns(patterns: list[str], conf: Any) -> list[str]:
    """Expand wildcard patterns and return a list of existing file paths."""
    expanded_files = []
    
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
            
        # Check if it's a direct file path first
        direct_path = conf.root / pattern
        if direct_path.is_file():
            expanded_files.append(pattern)
            continue
            
        # Use glob to expand wildcards
        # Search relative to the project root
        matched_paths = list(conf.root.glob(pattern))
        
        # Add relative paths of matched files
        for matched_path in matched_paths:
            if matched_path.is_file():
                try:
                    relative_path = matched_path.relative_to(conf.root)
                    expanded_files.append(str(relative_path))
                except ValueError:
                    # If we can't make it relative, use the original pattern
                    expanded_files.append(pattern)
    
    return expanded_files


def handle_with_command(
    prompt: str, 
    conf: Any, 
    console: Console, 
    chat_id: int, 
    chat_id_file: Path
) -> Optional[int]:
    """Handle the 'with' command for file-specific prompts with wildcard support.
    
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
        file_list_str, new_prompt_str = parts
        file_list_str = file_list_str.strip()[4:].strip()  # Remove 'with ' prefix

        if not file_list_str:
            rprint("[red]Error: File list cannot be empty for 'with' command.[/red]")
            return None
        if not new_prompt_str.strip():
            rprint("[red]Error: Prompt cannot be empty after the colon.[/red]")
            return None

        # Parse file patterns (can include wildcards)
        file_patterns = [f.strip() for f in file_list_str.replace(",", " ").split() if f.strip()]
        
        # Expand wildcards to get actual file paths
        expanded_files = _expand_file_patterns(file_patterns, conf)
        
        if not expanded_files:
            rprint("[red]Error: No files found matching the specified patterns.[/red]")
            return None
        
        explicit_source_files = {}
        
        for file_name in expanded_files:
            file_path = conf.root / file_name
            if not file_path.is_file():
                rprint(f"[yellow]File not found, skipping: {file_name}[/yellow]")
                continue  # Continue with other files instead of breaking
            try:
                explicit_source_files[file_name] = file_path.read_text(encoding="utf-8")
            except Exception as e:
                rprint(f"[red]Could not read file '{file_name}': {e}[/red]")
                continue  # Continue with other files instead of breaking
        
        if not explicit_source_files:
            rprint("[red]Error: No readable files found.[/red]")
            return None
        
        # Show which files were included
        if conf.verbose or len(explicit_source_files) != len(expanded_files):
            rprint(f"[cyan]Including {len(explicit_source_files)} file(s): {', '.join(explicit_source_files.keys())}[/cyan]")

        llm_response = invoke_llm(
            prompt=new_prompt_str.strip(),
            conf=conf,
            console=console,
            plugin_manager=conf.plugin_manager,
            chat_id=chat_id,
            verbose=conf.verbose,
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

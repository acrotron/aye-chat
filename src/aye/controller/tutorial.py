import time
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.spinner import Spinner

from aye.presenter.diff_presenter import show_diff
from aye.model.snapshot import restore_snapshot, apply_updates, list_snapshots


def _print_step(title, text, simulated_command=None):
    """Prints a formatted tutorial step and waits for user input."""
    rprint(Panel(text, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan", expand=False))
    if simulated_command:
        rprint(f"\n(ツ» [bold magenta]{simulated_command}[/bold magenta]")
    input("\nPress Enter to continue...\n")


def run_tutorial():
    """
    Runs an interactive tutorial for first-time users.
    
    Guides the user through:
    1. Sending a prompt to modify a file.
    2. Running a shell command.
    3. Viewing the changes with `diff`.
    4. Reverting the changes with `restore`.
    """
    # Directory for the flag file that prevents the tutorial from running again.
    tutorial_flag_dir = Path.home() / ".aye"
    tutorial_flag_dir.mkdir(parents=True, exist_ok=True)
    tutorial_flag_file = tutorial_flag_dir / ".tutorial_ran"

    # Welcome message and confirmation
    rprint(Panel(
        "[bold green]Welcome to Aye Chat![/] This is a quick 4-step interactive tutorial to get you started.",
        title="[bold]First-Time User Tutorial[/bold]",
        border_style="green",
        expand=False
    ))
    
    if not Confirm.ask("\n[bold]Do you want to start the tutorial now?[/bold]", default=True):
        rprint("\nSkipping tutorial. You can run it again by deleting the `~/.aye/.tutorial_ran` file.")
        tutorial_flag_file.touch()
        return

    # Create a temporary file for the tutorial
    temp_file = Path("tutorial_example.py")
    original_content = 'def hello_world():\n    print("Hello, World!")\n'
    temp_file.write_text(original_content, encoding="utf-8")

    rprint(f"\nI've created a temporary file named `[bold]{temp_file}[/]` for this tutorial.")
    time.sleep(1)

    # Step 1: Sending a prompt
    prompt = "add a docstring to the hello_world function"
    _print_step(
        "Step 1: Sending a Prompt",
        "Aye Chat works by sending your prompts to a Large Language Model (LLM), along with the content of relevant files in your project.\n\n"
        "Let's ask the LLM to add a docstring to the function in `tutorial_example.py`.\n\n"
        "I will now simulate sending this prompt:",
        simulated_command=prompt
    )

    console = Console()
    spinner = Spinner("dots", text="[yellow]Thinking...[/yellow]")
    
    try:
        with console.status(spinner) as status:
            # Simulate a network call and a response from the LLM instead of making a real one.
            time.sleep(3)

            new_content = (
                'def hello_world():\n'
                '    """Prints \'Hello, World!\' to the console."""\n'
                '    print("Hello, World!")\n'
            )
            
            result = {
                "summary": "I have added a docstring to the `hello_world` function as you requested.",
                "updated_files": [
                    {
                        "file_name": str(temp_file),
                        "file_content": new_content
                    }
                ]
            }

        updated_files = result.get("updated_files", [])
        if not updated_files:
            raise RuntimeError("The model did not suggest any file changes.")
        
        # Apply the updates, which also creates the initial snapshot
        batch_ts = apply_updates(updated_files, prompt)
        
        summary = result.get("summary", "The model has responded.")
        bot_face = "-{•!•}-"
        color = "rgb(170,170,170)"
        rprint()
        rprint(f"[{color}]{bot_face} » {summary}[/]")
        rprint()

        rprint(f"[green]Success! The file `[bold]{temp_file}[/]` has been updated by the assistant.[/green]")

    except Exception as e:
        rprint(f"[red]An error occurred during the tutorial: {e}[/red]")
        rprint("Skipping the rest of the tutorial.")
        temp_file.unlink(missing_ok=True)
        tutorial_flag_file.touch()
        return
    
    # Step 2: Running a shell command
    ls_command = f"ls -l {temp_file}"
    _print_step(
        "Step 2: Running Shell Commands",
        "Aye Chat isn't just for AI prompts. Any command that isn't a built-in (like `diff` or `restore`) is executed directly in your shell.\n\n"
        "This means you can run `git`, `pytest`, or even `vim` without leaving the chat.\n\n"
        "Let's run `ls -l` to see the details of our updated file.",
        simulated_command=ls_command
    )

    # In a real scenario, the shell executor plugin would handle this.
    # Here, we'll simulate its output.
    try:
        from datetime import datetime

        stat = temp_file.stat()
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%b %d %H:%M')
        
        # A simplified, cross-platform 'ls -l' style output
        ls_output = f"-rw-r--r-- 1 user group {size:4d} {mtime} {temp_file}"
        rprint(f"\n[bright_black]{ls_output}[/bright_black]")

    except Exception as e:
        rprint(f"\n[yellow]Could not simulate 'ls -l': {e}[/yellow]")
        rprint(f"[yellow]Imagine seeing file details like '-rw-r--r-- 1 user group 123 Oct 26 10:30 {temp_file}' here.[/yellow]")

    # Step 3: Viewing the changes (diff)
    diff_command = f"diff {temp_file}"
    _print_step(
        "Step 3: Viewing Changes with `diff`",
        "Before applying changes, Aye Chat creates a snapshot of the original files. You can see the difference between the current version and the last snapshot using the `diff` command.\n\n"
        "Let's run `diff` to see what changed.",
        simulated_command=diff_command
    )
    
    try:
        # To call diff, we need the path to the snapshot file.
        # In a real scenario, we'd get this from the snapshot model.
        # Here, we can reconstruct it based on the `apply_updates` logic.
        snapshots = list_snapshots(temp_file)
        if snapshots:
            latest_snap_path = Path(snapshots[0][1])
            show_diff(temp_file, latest_snap_path)
        else:
             rprint("[yellow]Could not find snapshot to diff against.[/yellow]")
    except Exception as e:
        rprint(f"[red]Error showing diff: {e}[/red]")

    # Step 4: Reverting changes
    restore_command = f"restore {temp_file}"
    _print_step(
        "Step 4: Reverting Changes with `restore`",
        "If you don't like the changes, you can easily revert them using the `restore` (or `undo`) command. This restores the file from the last snapshot.\n\n"
        "Let's run `restore`.",
        simulated_command=restore_command
    )

    try:
        restore_snapshot(file_name=str(temp_file))
        rprint(f"\n[green]Success! `[bold]{temp_file}[/]` has been restored to its original state.[/green]")
        
        rprint("\nLet's check the content:")
        rprint(f"[cyan]{temp_file.read_text(encoding='utf-8')}[/cyan]")
    except Exception as e:
        rprint(f"[red]Error restoring file: {e}[/red]")

    # Conclusion
    _print_step(
        "Tutorial Complete!",
        "You've learned the basic workflow:\n"
        "  1. Send a prompt to the assistant.\n"
        "  2. Run any shell command, like `ls -l`.\n"
        "  3. Review the changes using `diff`.\n"
        "  4. Revert if needed using `restore` or `undo`.\n\n"
        "You can explore more commands like `history`, `model`, and `help` in the interactive chat.\n\n"
        "Enjoy using Aye Chat!"
    )

    # Cleanup and finalize
    temp_file.unlink()
    tutorial_flag_file.touch()
    rprint("\nTutorial finished. The interactive chat will now start.")
    time.sleep(2)


def run_first_time_tutorial_if_needed() -> bool:
    """
    Checks if the first-run tutorial should be executed and runs it.
    Returns True if this was the first run (and the tutorial was run or skipped),
    False otherwise.
    """
    tutorial_flag_file = Path.home() / ".aye" / ".tutorial_ran"
    if not tutorial_flag_file.exists():
        run_tutorial()
        return True
    return False

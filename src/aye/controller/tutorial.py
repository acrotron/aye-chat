"""Interactive tutorial for first-time Aye Chat users.

Guides users through the core workflow:
1. Letting the assistant edit files directly (optimistic workflow)
2. Undoing instantly with `restore`
3. Seeing what changed with `diff`
4. Running normal shell commands in the same session
"""

import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from aye.presenter.diff_presenter import show_diff
from aye.model.snapshot import apply_updates, list_snapshots, restore_snapshot
from aye.presenter.repl_ui import console, print_assistant_response, print_prompt


# Constants
STEP_DELAY = 0.5  # Delay after showing step info
SIMULATE_THINK_DELAY = 2.0  # Delay to simulate LLM "thinking"
TUTORIAL_FLAG_DIR = Path.home() / '.aye'
TUTORIAL_FLAG_FILE = TUTORIAL_FLAG_DIR / '.tutorial_ran'

# Tutorial content
ORIGINAL_FILE_CONTENT = 'def hello_world():\n    print("Hello, World!")\n'
MODIFIED_FILE_CONTENT = 'def hello_world():\n    print("HELLO WORLD!")\n'
SIMULATED_PROMPT = 'change the function to print HELLO WORLD in all caps'


def _print_step(
    title: str,
    text: str,
    simulated_command: Optional[str] = None,
    target_console: Optional[Console] = None,
) -> None:
    """Print a tutorial step with optional simulated command.
    
    Args:
        title: Step title shown in panel header
        text: Explanatory text for the step
        simulated_command: Optional command to display as if user typed it
        target_console: Console to print to (defaults to global console)
    """
    c = target_console or console
    c.print('\n')
    c.print(Panel(
        text,
        title=f'[ui.help.header]{title}[/]',
        border_style='ui.border',
        expand=False
    ))
    if simulated_command:
        prompt_symbol = print_prompt()
        c.print(f'\n{prompt_symbol}[ui.help.command]{simulated_command}[/]')
    input('\nPress Enter to continue...\n')


class TutorialRunner:
    """Runs the interactive tutorial for first-time users.
    
    Each public method handles one step of the tutorial, making the
    code easier to test and maintain.
    """

    def __init__(self, target_console: Optional[Console] = None):
        """Initialize the tutorial runner.
        
        Args:
            target_console: Console to use for output (defaults to global console)
        """
        self._console = target_console or console
        self._temp_file = Path('tutorial_example.py')
        self._setup_complete = False

    def run(self, is_first_run: bool = True) -> None:
        """Run the complete tutorial.
        
        Args:
            is_first_run: If True, runs automatically. If False, prompts user first.
        """
        if not self._should_run(is_first_run):
            return

        self._show_welcome()
        
        try:
            self._setup()
            self._demo_file_editing()
            self._demo_restore()
            self._demo_diff()
            self._demo_shell_commands()
            self._show_completion()
        except KeyboardInterrupt:
            self._console.print('\n[ui.warning]Tutorial interrupted.[/]')
        finally:
            self._cleanup()

    def _should_run(self, is_first_run: bool) -> bool:
        """Check if the tutorial should run based on user preference.
        
        Args:
            is_first_run: Whether this is the first run (auto-runs if True)
            
        Returns:
            True if tutorial should run, False otherwise
        """
        if is_first_run:
            return True

        if not Confirm.ask(
            '\n[bold]Do you want to run the tutorial?[/bold]',
            console=self._console,
            default=False
        ):
            self._console.print('\nSkipping tutorial.')
            self._mark_tutorial_complete()
            return False

        return True

    def _show_welcome(self) -> None:
        """Display the welcome panel."""
        self._console.print(Panel(
            '[ui.welcome]Welcome to Aye Chat![/] This is a quick 4-step tutorial.',
            title='[ui.help.header]First-Time User Tutorial[/]',
            border_style='ui.border',
            expand=False
        ))

    def _setup(self) -> None:
        """Set up the tutorial environment by creating the temp file.
        
        Raises:
            PermissionError: If unable to write to current directory
            OSError: If file creation fails for other reasons
        """
        # Ensure tutorial flag directory exists
        try:
            TUTORIAL_FLAG_DIR.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            self._console.print(
                f'[ui.error]Cannot create directory {TUTORIAL_FLAG_DIR}. '
                'Check permissions.[/]'
            )
            raise

        # Create the tutorial temp file
        try:
            self._temp_file.write_text(ORIGINAL_FILE_CONTENT, encoding='utf-8')
            self._setup_complete = True
            self._console.print(
                f'\nCreated a temporary file: `[ui.help.text]{self._temp_file}[/]`'
            )
            time.sleep(STEP_DELAY)
        except PermissionError:
            self._console.print(
                f'[ui.error]Cannot create {self._temp_file}. '
                'Check write permissions in current directory.[/]'
            )
            raise
        except OSError as e:
            self._console.print(
                f'[ui.error]Failed to create tutorial file: {e}[/]'
            )
            raise

    def _demo_file_editing(self) -> None:
        """Step 1: Demonstrate the optimistic file editing workflow.
        
        Simulates an LLM response that modifies the tutorial file.
        """
        _print_step(
            'Step 1: Letting the assistant edit files',
            'Aye Chat edits files directly (optimistic workflow). No approval prompts.\n\n'
            'In real chats, the assistant updates files immediately and snapshots every change.\n\n'
            'We will ask it to change `tutorial_example.py`.',
            simulated_command=SIMULATED_PROMPT,
            target_console=self._console,
        )

        # Simulate LLM "thinking"
        with self._console.status('[ui.help.text]Thinking...[/]'):
            time.sleep(SIMULATE_THINK_DELAY)

        # Simulate LLM response
        updated_files: List[Dict[str, str]] = [
            {
                'file_name': str(self._temp_file),
                'file_content': MODIFIED_FILE_CONTENT,
            }
        ]

        try:
            apply_updates(updated_files, SIMULATED_PROMPT)
            print_assistant_response('Updated `hello_world` to print in all caps.')
            self._console.print(
                f'[ui.success]Success! `[bold]{self._temp_file}[/]` was updated.[/]'
            )
        except PermissionError:
            self._console.print(
                f'[ui.error]Cannot write to {self._temp_file}. Check permissions.[/]'
            )
            raise
        except OSError as e:
            self._console.print(f'[ui.error]Failed to update file: {e}[/]')
            raise

    def _demo_restore(self) -> None:
        """Step 2: Demonstrate the restore/undo functionality."""
        restore_command = f'restore {self._temp_file}'
        _print_step(
            'Step 2: Instant undo with `restore`',
            'This is the core workflow: every edit batch is snapshotted automatically, '
            'so rollback is instant.\n\n'
            'Common options:\n'
            '  - `undo` or `restore <file>` rolls back the most recent change\n'
            '  - `history` lists snapshots, then `restore <ordinal>` '
            '(e.g. `restore 001`) jumps back',
            simulated_command=restore_command,
            target_console=self._console,
        )

        try:
            restore_snapshot(file_name=str(self._temp_file))
            self._console.print(
                f'\n[ui.success]Restored `[bold]{self._temp_file}[/]` to the previous state.[/]'
            )
            self._console.print('\nCurrent content:')
            content = self._temp_file.read_text(encoding='utf-8')
            self._console.print(f'[ui.help.text]{content}[/]')
        except FileNotFoundError:
            self._console.print(
                f'[ui.error]File {self._temp_file} not found. Cannot restore.[/]'
            )
        except (ValueError, RuntimeError) as e:
            # Snapshot system errors
            self._console.print(f'[ui.error]Error restoring file: {e}[/]')

    def _demo_diff(self) -> None:
        """Step 3: Demonstrate the diff functionality."""
        diff_command = f'diff {self._temp_file}'
        _print_step(
            'Step 3: See what changed with `diff`',
            'Let us apply the same change again, then inspect the diff.\n\n'
            'Tip: `diff <file>` compares against the last snapshot. '
            'For older snapshots, use `history` and then `diff <file> <ordinal>`.',
            simulated_command=diff_command,
            target_console=self._console,
        )

        # Re-apply the change so we have something to diff
        updated_files: List[Dict[str, str]] = [
            {
                'file_name': str(self._temp_file),
                'file_content': MODIFIED_FILE_CONTENT,
            }
        ]

        try:
            apply_updates(updated_files, SIMULATED_PROMPT)
            snapshots = list_snapshots(self._temp_file)
            
            if snapshots:
                latest_snap_path = Path(snapshots[0][1])
                show_diff(self._temp_file, latest_snap_path)
            else:
                self._console.print(
                    '[ui.warning]Could not find a snapshot to diff against.[/]'
                )
        except FileNotFoundError as e:
            self._console.print(f'[ui.error]File not found: {e}[/]')
        except (ValueError, RuntimeError) as e:
            self._console.print(f'[ui.error]Error showing diff: {e}[/]')

    def _demo_shell_commands(self) -> None:
        """Step 4: Demonstrate inline shell command execution."""
        ls_command = f'ls -l {self._temp_file}'
        _print_step(
            'Step 4: Run shell commands inline',
            'Anything that is not a chat command (like `diff`, `restore`, `history`) '
            'runs in your shell.\n\n'
            'You can run tests, git, or open an editor without leaving the session.',
            simulated_command=ls_command,
            target_console=self._console,
        )

        try:
            stat = self._temp_file.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%b %d %H:%M')
            ls_output = f'-rw-r--r-- 1 user group {size:4d} {mtime} {self._temp_file}'
            self._console.print(f'\n[dim]{ls_output}[/]')
        except FileNotFoundError:
            self._console.print(
                f'\n[ui.warning]File {self._temp_file} not found for `ls -l`.[/]'
            )
        except OSError as e:
            self._console.print(f'\n[ui.warning]Could not simulate `ls -l`: {e}[/]')

    def _show_completion(self) -> None:
        """Display the tutorial completion message."""
        _print_step(
            'Tutorial Complete!',
            'You saw the core flow:\n'
            '  1. Prompt the assistant (edits apply automatically).\n'
            '  2. Roll back instantly with `undo` / `restore`.\n'
            '  3. Inspect changes with `diff` and browse snapshots with `history`.\n'
            '  4. Run normal shell commands inline.\n\n'
            'Type `help` anytime to see commands.',
            target_console=self._console,
        )

    def _cleanup(self) -> None:
        """Clean up tutorial resources and mark completion."""
        # Remove temp file if it was created
        if self._setup_complete:
            try:
                self._temp_file.unlink(missing_ok=True)
            except OSError:
                # Best effort cleanup - don't fail if we can't delete
                pass

        self._mark_tutorial_complete()
        self._console.print('\nTutorial finished. The interactive chat will now start.')
        time.sleep(STEP_DELAY * 2)

    def _mark_tutorial_complete(self) -> None:
        """Mark the tutorial as completed by touching the flag file."""
        try:
            TUTORIAL_FLAG_FILE.touch()
        except OSError:
            # Non-critical - tutorial will just run again next time
            pass


def run_tutorial(is_first_run: bool = True) -> None:
    """Run the interactive tutorial.
    
    This is the main entry point for the tutorial, maintaining backward
    compatibility with the original function signature.

    Args:
        is_first_run: If True, runs automatically. If False, prompts user first.
    """
    runner = TutorialRunner()
    runner.run(is_first_run)


def run_first_time_tutorial_if_needed() -> bool:
    """Run the tutorial if this is the user's first time.
    
    Returns:
        True if the tutorial was run, False if skipped (already completed)
    """
    if TUTORIAL_FLAG_FILE.exists():
        return False

    run_tutorial(is_first_run=True)
    return True

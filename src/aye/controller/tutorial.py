import time
from pathlib import Path
from typing import List, Dict, cast

from rich.panel import Panel
from rich.prompt import Confirm
from rich.spinner import Spinner

from aye.presenter.diff_presenter import show_diff
from aye.model.snapshot import apply_updates, list_snapshots, restore_snapshot
from aye.presenter.repl_ui import console, print_assistant_response, print_prompt


def _print_step(title, text, simulated_command=None):
    console.print('\n')
    console.print(Panel(text, title=f'[ui.help.header]{title}[/]', border_style='ui.border', expand=False))
    if simulated_command:
        prompt_symbol = print_prompt()
        console.print(f'\n{prompt_symbol}[ui.help.command]{simulated_command}[/]')
    input('\nPress Enter to continue...\n')


def run_tutorial(is_first_run: bool = True):
    """
    Runs an interactive tutorial for users.

    Args:
        is_first_run: If True, runs automatically. If False, prompts user first.

    Guides the user through:
    1. Letting the assistant edit a file directly.
    2. Undoing instantly with `restore`.
    3. Seeing what changed with `diff`.
    4. Running normal shell commands in the same session.
    """
    tutorial_flag_dir = Path.home() / '.aye'
    tutorial_flag_dir.mkdir(parents=True, exist_ok=True)
    tutorial_flag_file = tutorial_flag_dir / '.tutorial_ran'

    if not is_first_run:
        if not Confirm.ask('\n[bold]Do you want to run the tutorial?[/bold]', console=console, default=False):
            console.print('\nSkipping tutorial.')
            tutorial_flag_file.touch()
            return

    console.print(Panel(
        '[ui.welcome]Welcome to Aye Chat![/] This is a quick 4-step tutorial.',
        title='[ui.help.header]First-Time User Tutorial[/]',
        border_style='ui.border',
        expand=False
    ))

    temp_file = Path('tutorial_example.py')
    original_content = 'def hello_world():\n    print("Hello, World!")\n'
    temp_file.write_text(original_content, encoding='utf-8')

    console.print(f'\nCreated a temporary file: `[ui.help.text]{temp_file}[/]`')
    time.sleep(0.5)

    spinner = Spinner('dots', text='[ui.help.text]Thinking...[/]')

    prompt = 'change the function to print HELLO WORLD in all caps'
    _print_step(
        'Step 1: Letting the assistant edit files',
        'Aye Chat edits files directly (optimistic workflow). No approval prompts.\n\n' 
        'In real chats, the assistant updates files immediately and snapshots every change..\n\n' 
        'We will ask it to change `tutorial_example.py`.',
        simulated_command=prompt
    )

    try:
        with console.status(spinner):
            time.sleep(2)

            new_content = (
                'def hello_world():\n'
                '    print("HELLO WORLD!")\n'
            )

            result = {
                'summary': 'Updated `hello_world` to print in all caps.',
                'updated_files': [
                    {
                        'file_name': str(temp_file),
                        'file_content': new_content,
                    }
                ],
            }

        updated_files = cast(List[Dict[str, str]], result.get('updated_files', []))
        if not updated_files:
            raise RuntimeError('The model did not suggest any file changes.')

        apply_updates(updated_files, prompt)

        summary = str(result.get('summary', 'Done.'))
        print_assistant_response(summary)

        console.print(f'[ui.success]Success! `[bold]{temp_file}[/]` was updated.[/]')

    except Exception as e:
        console.print(f'[ui.error]An error occurred during the tutorial: {e}[/]')
        console.print('Skipping the rest of the tutorial.')
        temp_file.unlink(missing_ok=True)
        tutorial_flag_file.touch()
        return

    restore_command = f'restore {temp_file}'
    _print_step(
        'Step 2: Instant undo with `restore`',
        'This is the core workflow: every edit batch is snapshotted automatically, so rollback is instant.\n\n' 
        'Common options:\n' 
        '  - `undo` or `restore <file>` rolls back the most recent change\n' 
        '  - `history` lists snapshots, then `restore <ordinal>` (e.g. `restore 001`) jumps back',
        simulated_command=restore_command
    )

    try:
        restore_snapshot(file_name=str(temp_file))
        console.print(f'\n[ui.success]Restored `[bold]{temp_file}[/]` to the previous state.[/]')
        console.print('\nCurrent content:')
        console.print(f'[ui.help.text]{temp_file.read_text(encoding="utf-8")}[/]')
    except Exception as e:
        console.print(f'[ui.error]Error restoring file: {e}[/]')

    diff_command = f'diff {temp_file}'
    _print_step(
        'Step 3: See what changed with `diff`',
        'Let us apply the same change again, then inspect the diff.\n\n' 
        'Tip: `diff <file>` compares against the last snapshot. For older snapshots, use `history` and then `diff <file> <ordinal>`.,',
        simulated_command=diff_command
    )

    try:
        updated_files = [
            {
                'file_name': str(temp_file),
                'file_content': (
                    'def hello_world():\n'
                    '    print("HELLO WORLD!")\n'
                ),
            }
        ]
        apply_updates(updated_files, prompt)

        snapshots = list_snapshots(temp_file)
        if snapshots:
            latest_snap_path = Path(snapshots[0][1])
            show_diff(temp_file, latest_snap_path)
        else:
            console.print('[ui.warning]Could not find a snapshot to diff against.[/]')
    except Exception as e:
        console.print(f'[ui.error]Error showing diff: {e}[/]')

    ls_command = f'ls -l {temp_file}'
    _print_step(
        'Step 4: Run shell commands inline',
        'Anything that is not a chat command (like `diff`, `restore`, `history`) runs in your shell.\n\n' 
        'You can run tests, git, or open an editor without leaving the session.',
        simulated_command=ls_command
    )

    try:
        from datetime import datetime

        stat = temp_file.stat()
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%b %d %H:%M')
        ls_output = f'-rw-r--r-- 1 user group {size:4d} {mtime} {temp_file}'
        console.print(f'\n[dim]{ls_output}[/]')

    except Exception as e:
        console.print(f'\n[ui.warning]Could not simulate `ls -l`: {e}[/]')

    _print_step(
        'Tutorial Complete!',
        'You saw the core flow:\n' 
        '  1. Prompt the assistant (edits apply automatically).\n' 
        '  2. Roll back instantly with `undo` / `restore`.\n' 
        '  3. Inspect changes with `diff` and browse snapshots with `history`.\n' 
        '  4. Run normal shell commands inline.\n\n' 
        'Type `help` anytime to see commands.'
    )

    temp_file.unlink()
    tutorial_flag_file.touch()
    console.print('\nTutorial finished. The interactive chat will now start.')
    time.sleep(1)


def run_first_time_tutorial_if_needed() -> bool:
    tutorial_flag_file = Path.home() / '.aye' / '.tutorial_ran'
    is_first_run = not tutorial_flag_file.exists()

    if is_first_run:
        run_tutorial(is_first_run=True)
        return True

    return False

# User Acceptance Tests for Snapshot Commands in Aye Chat

This document outlines user acceptance tests (UATs) for snapshot-related commands that are executed exclusively from the Aye chat interface (REPL). These commands are: `history`, `restore`, and `keep`. Tests are based on the functionality implemented in `aye/controller/repl.py`, focusing on user interactions within the chat session.

## Test Environment Setup
- Start Aye chat session using `aye chat`.
- Ensure snapshots exist (create test snapshots via code generation or manual setup if needed).
- Use a test project directory with files that have been modified and snapshotted.

## Test Cases

### 1. History Command

#### UAT-1.1: Display All Snapshots When Snapshots Exist
- **Given**: The user is in an Aye chat session, and snapshots exist in the project.
- **When**: The user types `history` and presses Enter.
- **Then**: The system displays a list of all snapshots in descending order (newest first), including ordinal, truncated prompt, and file names. Output example:
  ```
  001  (no prompt       )  file1.py, file2.py
  002  (some prompt...  )  file3.py
  ```

#### UAT-1.2: Display Message When No Snapshots Exist
- **Given**: The user is in an Aye chat session, and no snapshots exist.
- **When**: The user types `history` and presses Enter.
- **Then**: The system displays `[yellow]No snapshots found.[/]`, and no list is shown.

### 2. Restore Command

#### UAT-2.1: Restore All Files from Latest Snapshot (No Arguments)
- **Given**: The user is in an Aye chat session, and at least one snapshot exists.
- **When**: The user types `restore` and presses Enter.
- **Then**: The system restores all files to the latest snapshot and displays:
  ```
  [green]✅ All files restored to latest snapshot.[/]
  ```

#### UAT-2.2: Restore All Files from Specific Snapshot by Ordinal
- **Given**: The user is in an Aye chat session, and a snapshot with ordinal '001' exists.
- **When**: The user types `restore 001` and presses Enter.
- **Then**: The system restores all files to snapshot '001' and displays:
  ```
  [green]✅ All files restored to 001[/]
  ```

#### UAT-2.3: Restore Specific File from Latest Snapshot
- **Given**: The user is in an Aye chat session, and 'file1.py' has snapshots.
- **When**: The user types `restore file1.py` (assuming 'file1.py' exists and has been snapshotted).
- **Then**: The system restores 'file1.py' to the latest snapshot for that file and displays:
  ```
  [green]✅ File 'file1.py' restored to latest snapshot.[/]
  ```

#### UAT-2.4: Restore Specific File from Specific Snapshot
- **Given**: The user is in an Aye chat session, and 'file1.py' has a snapshot with ordinal '001'.
- **When**: The user types `restore 001 file1.py` and presses Enter.
- **Then**: The system restores 'file1.py' to snapshot '001' and displays:
  ```
  [green]✅ File 'file1.py' restored to 001[/]
  ```

#### UAT-2.5: Error When Snapshot Not Found
- **Given**: The user is in an Aye chat session, and no snapshot with ordinal '999' exists.
- **When**: The user types `restore 999` and presses Enter.
- **Then**: The system displays an error message (e.g., `[red]Error:[/] Snapshot with Id 999 not found`), and no restoration occurs.

#### UAT-2.6: Error When File Not in Snapshot
- **Given**: The user is in an Aye chat session, and snapshot '001' does not contain 'nonexistent.py'.
- **When**: The user types `restore 001 nonexistent.py` and presses Enter.
- **Then**: The system displays an error message (e.g., `[red]Error:[/] File 'nonexistent.py' not found in snapshot 001`), and no restoration occurs.

### 3. Keep Command

#### UAT-3.1: Prune Snapshots to Default Keep Count (10)
- **Given**: The user is in an Aye chat session, and more than 10 snapshots exist.
- **When**: The user types `keep` and presses Enter.
- **Then**: The system prunes snapshots to keep the 10 most recent and displays:
  ```
  ✅ X snapshots pruned. 10 most recent kept.
  ```
  (Where X is the number deleted.)

#### UAT-3.2: Prune Snapshots to Custom Keep Count
- **Given**: The user is in an Aye chat session, and more than 5 snapshots exist.
- **When**: The user types `keep 5` and presses Enter.
- **Then**: The system prunes snapshots to keep the 5 most recent and displays:
  ```
  ✅ Y snapshots pruned. 5 most recent kept.
  ```
  (Where Y is the number deleted.)

#### UAT-3.3: No Pruning When Fewer Snapshots Than Keep Count
- **Given**: The user is in an Aye chat session, and fewer than 10 snapshots exist.
- **When**: The user types `keep` and presses Enter.
- **Then**: The system displays:
  ```
  ✅ 0 snapshots pruned. 10 most recent kept.
  ```
  (Indicating no action taken.)

#### UAT-3.4: Error Handling for Invalid Keep Count
- **Given**: The user is in an Aye chat session.
- **When**: The user types `keep abc` (invalid number) and presses Enter.
- **Then**: The system displays an error message (e.g., `[red]Error:[/] invalid literal for int() with base 10: 'abc'`), and no pruning occurs.

## Notes
- Tests assume the underlying snapshot functions in `aye/model/snapshot.py` work correctly, as the REPL delegates to them.
- Error messages are based on code inspection; actual output may vary slightly.
- All commands should respond gracefully to exceptions, displaying error messages in red.
- Tests should be run in a controlled environment to avoid affecting real project snapshots.

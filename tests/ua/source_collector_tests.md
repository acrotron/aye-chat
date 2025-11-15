# User Acceptance Tests for Source Collector Functionality in Aye

This document outlines user acceptance tests (UATs) for the source collection feature in Aye, implemented in `aye/model/source_collector.py`. The `collect_sources` function collects source files based on file masks, automatically excluding hidden files/directories (those starting with '.') and files matching patterns in `.gitignore` and `.ayeignore` files (loaded from the root directory and all parent directories). Tests emphasize correct exclusion of hidden and ignored files, while verifying inclusion of valid source files. The functionality is used internally by Aye for collecting files to include in prompts, such as in chat sessions or generation commands.

## Test Environment Setup
- Create a temporary project directory structure with various files and subdirectories.
- Use Aye CLI or chat commands that trigger source collection (e.g., `aye chat`, `aye generate`) to observe collected files.
- For direct testing, invoke the `collect_sources` function programmatically with different masks and roots.
- Include `.gitignore` and/or `.ayeignore` files with various patterns (e.g., specific files, directories, wildcards).
- Ensure some hidden files/directories (e.g., `.hidden`, `.git`) and files matching ignore patterns are present in the test directory.
- Run tests in a clean environment without pre-existing `.aye` directories influencing results.

## Test Cases

### 1. Exclusion of Hidden Files and Directories

#### UAT-1.1: Exclude Files in Hidden Directories
- **Given**: A project directory with files like `src/main.py`, `.hidden/secret.py`, and `data/file.txt`.
- **When**: The user runs `aye chat --include "*.py"` in the project root.
- **Then**: Only `src/main.py` is collected; `.hidden/secret.py` is excluded despite matching the mask.
- **Verification**: Check the collected files output or log; ensure no files from hidden paths (any directory starting with '.') are included.

#### UAT-1.2: Exclude Hidden Files at Root Level
- **Given**: A project directory with files `.env`, `config.py`, and `app.js`.
- **When**: The user runs `aye generate "Create app" --include "*.py,*.js"`.
- **Then**: Only `config.py` and `app.js` are collected; `.env` is excluded.
- **Verification**: Confirm `.env` does not appear in the list of files included with the prompt.

#### UAT-1.3: Exclude Files in Nested Hidden Directories
- **Given**: A deep directory structure with `src/.cache/temp.py` and `src/utils/helper.py`.
- **When**: The user runs `aye chat --include "*.py"`.
- **Then**: Only `src/utils/helper.py` is collected; `src/.cache/temp.py` is excluded.
- **Verification**: Ensure recursion properly skips all hidden subdirectories.

### 2. Exclusion Based on .gitignore/.ayeignore Patterns

#### UAT-2.1: Exclude Files Matching .gitignore at Root
- **Given**: A `.gitignore` file in the project root containing `logs/*.log` and `temp/`, with files `logs/error.log`, `temp/cache.py`, and `src/app.py`.
- **When**: The user runs `aye chat --include "*.py,*.log"`.
- **Then**: Only `src/app.py` is collected; `logs/error.log` and `temp/cache.py` are excluded.
- **Verification**: Check that pathspec-based matching excludes files matching gitwildmatch patterns.

#### UAT-2.2: Exclude Files Matching .ayeignore (Override .gitignore)
- **Given**: Both `.gitignore` (with `build/`) and `.ayeignore` (with `build/debug.log`) in root, with files `build/app.exe`, `build/debug.log`, and `src/main.py`.
- **When**: The user runs `aye generate "Fix bug" --include "*.py,*.log"`.
- **Then**: `src/main.py` and `build/debug.log` are collected; `build/app.exe` is excluded (assuming .exe is not in mask).
- **Verification**: Confirm `.ayeignore` patterns are loaded and applied in addition to `.gitignore`.

#### UAT-2.3: Exclude Patterns from Parent Directories
- **Given**: Parent directory has `.gitignore` with `shared/`, project root has `shared/config.txt` and `local/file.py`.
- **When**: The user runs `aye chat --include "*.txt,*.py"` from project root.
- **Then**: Only `local/file.py` is collected; `shared/config.txt` is excluded.
- **Verification**: Ensure ignore patterns are loaded from all parent directories up to filesystem root.

#### UAT-2.4: Include Files That Partially Match But Are Not Fully Excluded
- **Given**: `.gitignore` with `node_modules/`, with files `node_modules/package.json` and `src/package.json`.
- **When**: The user runs `aye chat --include "*.json"`.
- **Then**: Only `src/package.json` is collected; `node_modules/package.json` is excluded.
- **Verification**: Verify exact path matching excludes only full matches.

### 3. Inclusion Based on File Masks and Recursion

#### UAT-3.1: Include Files Matching Simple Mask
- **Given**: Files `app.py`, `utils.js`, `data.csv`, and `readme.txt` in root.
- **When**: The user runs `aye chat --include "*.py,*.js"`.
- **Then**: Only `app.py` and `utils.js` are collected.
- **Verification**: Confirm comma-separated masks work and only specified extensions are included.

#### UAT-3.2: Include Files with Recursive Search
- **Given**: Files `src/main.py`, `src/utils/helper.py`, `tests/test.py`, and `root.py`.
- **When**: The user runs `aye chat --include "*.py"` (default recursive).
- **Then**: All four `.py` files are collected.
- **Verification**: Ensure rglob is used for recursive matching.

#### UAT-3.3: Non-Recursive Search (If Applicable)
- **Given**: Same structure as UAT-3.2.
- **When**: The user invokes `collect_sources` with `recursive=False`.
- **Then**: Only `root.py` is collected.
- **Verification**: Confirm glob is used for non-recursive, excluding subdirectories.

#### UAT-3.4: Handle Empty Masks or No Matches
- **Given**: An empty directory or files not matching mask.
- **When**: The user runs `aye chat --include "*.xyz"`.
- **Then**: No files are collected.
- **Verification**: Ensure empty dict is returned without errors.

### 4. Edge Cases and Error Handling

#### UAT-4.1: Skip Non-UTF8 Files
- **Given**: A binary file `image.png` and text file `script.py`.
- **When**: The user runs `aye chat --include "*"` (broad mask).
- **Then**: Only `script.py` is collected; `image.png` is skipped.
- **Verification**: Check for UnicodeDecodeError handling.

#### UAT-4.2: Handle Invalid Root Directory
- **Given**: A non-existent path.
- **When**: The user attempts to run `aye chat --root "/nonexistent"`.
- **Then**: An error is raised (NotADirectoryError).
- **Verification**: Ensure proper exception for invalid directories.

#### UAT-4.3: Ignore Comments and Empty Lines in Ignore Files
- **Given**: `.ayeignore` with `# comment`, empty lines, and `temp/*`.
- **When**: The user runs `aye chat --include "*"` with `temp/file.txt` present.
- **Then**: `temp/file.txt` is excluded.
- **Verification**: Confirm parsing ignores comments and blanks.

#### UAT-4.4: Case-Insensitive Extension Matching (if applicable)
- **Given**: Files `App.PY` and `utils.JS` (uppercase extensions).
- **When**: The user runs `aye chat --include "*.py,*.js"`.
- **Then**: Both files are collected.
- **Verification**: Ensure suffix matching is case-insensitive.

## Notes
- Tests assume the `collect_sources` function is called internally by Aye commands; direct API calls can be simulated for unit-like verification.
- Hidden file exclusion is based on any path part starting with '.', independent of ignore patterns.
- Ignore patterns use `pathspec` for gitwildmatch, supporting wildcards and directory matches.
- All tests should verify no crashes on malformed ignore files or missing files.
- For CLI tests, observe the "Included with prompt" message to confirm collected files.

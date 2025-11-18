# User Acceptance Tests for Service Handlers in Aye

This document outlines user acceptance tests (UATs) for service handlers in Aye, implemented in `aye/controller/commands.py` and CLI in `aye/__main__.py`. Covers handlers for generate, chat, diff, and other commands not fully tested elsewhere. Emphasize CLI usage and chat integration.

## Test Environment Setup
- Run CLI commands.
- For chat, start session.
- Have test files/snapshots.

## Test Cases

### 1. Generate Command

#### UAT-1.1: Generate Single Prompt
- **Given**: Valid token.
- **When**: `aye generate "prompt"`.
- **Then**: Calls API, prints code.
- **Verification**: Output shown.

### 2. Chat Command

#### UAT-2.1: Start Chat
- **Given**: Config set.
- **When**: `aye chat`.
- **Then**: Starts REPL.
- **Verification**: Welcome shown.

### 3. Diff Command

#### UAT-3.1: Diff File with Latest
- **Given**: File with snapshots.
- **When**: In chat, "diff file.py".
- **Then**: Shows diff.
- **Verification**: Output correct.

#### UAT-3.2: Diff Between Snapshots
- **Given**: Multiple snapshots.
- **When**: "diff file.py 001 002".
- **Then**: Shows diff between them.
- **Verification**: Correct files compared.

### 4. Other Handlers

#### UAT-4.1: History in Chat
- **Given**: Snapshots exist.
- **When**: "history".
- **Then**: Lists snapshots.
- **Verification**: Formatted list.

#### UAT-4.2: Prune/Cleanup
- **Given**: Many snapshots.
- **When**: Via CLI or chat.
- **Then**: Deletes old ones.
- **Verification**: Count correct.

## Notes
- Handlers call API/snapshot functions.
- Errors handled gracefully.

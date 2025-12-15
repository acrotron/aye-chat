'''Plugin for @file reference completion and expansion.

Allows users to reference files inline using @filename syntax with autocomplete.
Example: "I want to update @main.py with a driver function"

Usage:
    - Type @ followed by a filename to get autocomplete suggestions
    - Multiple @references can be used in a single prompt
    - Supports relative paths: @src/utils.py
    - Supports wildcards in file patterns: @src/*.py (when parsed)

Examples:
    "I want to update @main.py with a driver function"
    "Refactor @src/utils.py and @src/helpers.py to use async"
    "Explain what @config.py does"
'''

import os
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from prompt_toolkit.document import Document
from prompt_toolkit.completion import Completer, Completion
from rich import print as rprint

from .plugin_base import Plugin
from aye.model.ignore_patterns import load_ignore_patterns


class AtFileCompleter(Completer):
    """
    Completes file paths when user types '@' anywhere in the input.
    Supports relative paths and wildcards.
    Respects .gitignore and .ayeignore patterns.
    """

    def __init__(self, project_root: Optional[Path] = None, file_cache: Optional[List[str]] = None):
        self.project_root = project_root or Path.cwd()
        self._file_cache: Optional[List[str]] = file_cache
        self._cache_valid = file_cache is not None

    def _get_project_files(self) -> List[str]:
        """Get list of files in project, using cache if available.
        
        Respects .gitignore and .ayeignore patterns.
        """
        if self._cache_valid and self._file_cache is not None:
            return self._file_cache

        # Load ignore patterns using shared utility
        ignore_spec = load_ignore_patterns(self.project_root)

        # Build file list - respect ignore patterns
        files = []

        try:
            for root, dirs, filenames in os.walk(self.project_root):
                # Filter out ignored directories before descending
                rel_dir = Path(root).relative_to(self.project_root).as_posix()
                
                # Filter directories
                dirs[:] = [
                    d for d in dirs
                    if not ignore_spec.match_file(os.path.join(rel_dir, d + "/"))
                    and not d.startswith('.')  # Skip hidden dirs
                ]

                # Process files
                for filename in filenames:
                    if filename.startswith('.'):
                        continue
                    
                    rel_file = os.path.join(rel_dir, filename)
                    
                    # Check if file matches ignore patterns
                    if ignore_spec.match_file(rel_file):
                        continue
                    
                    filepath = Path(root) / filename
                    try:
                        rel_path = filepath.relative_to(self.project_root)
                        # Use POSIX paths for consistency across platforms (forward slashes)
                        files.append(rel_path.as_posix())
                    except ValueError:
                        continue
        except Exception:
            pass

        self._file_cache = sorted(files)
        self._cache_valid = True
        return self._file_cache

    def invalidate_cache(self):
        """Invalidate the file cache to force refresh."""
        self._cache_valid = False
        self._file_cache = None

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # Find the last '@' that starts a file reference
        at_pos = text.rfind('@')
        if at_pos == -1:
            return

        # Check if @ is preceded by a space or is at start (valid file reference)
        if at_pos > 0 and text[at_pos - 1] not in ' \t\n':
            # @ is part of another word (like an email), skip
            return

        # Get the partial path after '@'
        partial = text[at_pos + 1:]

        # Don't complete if there's a space after the partial (file reference ended)
        # But allow paths with slashes
        if ' ' in partial and not partial.endswith('/'):
            return

        # Get all project files
        all_files = self._get_project_files()

        partial_lower = partial.lower()

        if not partial:
            # Empty partial: show top 20 alphabetical
            all_files_sorted = sorted(all_files)
            for filepath in all_files_sorted[:20]:
                yield Completion(
                    filepath,
                    start_position=-len(partial),
                    display=filepath,
                    display_meta="file"
                )
            return

        # First: exact matches (prefix, filename prefix, substring)
        exact_matches = []
        for filepath in all_files:
            filepath_lower = filepath.lower()
            filename_lower = Path(filepath).name.lower()

            matches = (
                filepath_lower.startswith(partial_lower) or
                filename_lower.startswith(partial_lower) or
                partial_lower in filepath_lower
            )

            if matches:
                exact_matches.append(filepath)

        if exact_matches:
            for filepath in sorted(exact_matches):
                yield Completion(
                    filepath,
                    start_position=-len(partial),
                    display=filepath,
                    display_meta="file (exact)"
                )
            return

        # Fuzzy fallback on filenames (rapidfuzz)
        try:
            from rapidfuzz import process, fuzz
            file_names = [Path(fp).name for fp in all_files]
            matches = process.extract(
                partial, file_names,
                scorer=fuzz.partial_ratio,
                limit=8
            )
            for match_name, score, _ in matches:  # ✅ Fixed: unpack all 3 values
                if score >= 70:
                    full_paths = [fp for fp in all_files if Path(fp).name == match_name]
                    if full_paths:
                        full_path = full_paths[0]
                        yield Completion(
                            full_path,
                            start_position=-len(partial),
                            display=full_path,
                            display_meta=f"file (fuzzy: {score:.0f}%)"
                        )
        except ImportError:
            # Graceful fallback if rapidfuzz not installed
            pass


class AtFileCompleterWrapper(Completer):
    """
    Wrapper around AtFileCompleter that forces multi-column display for @ completions.
    
    This ensures that @ file completions always show in a multi-column grid,
    regardless of whether the user has selected 'readline' or 'multi' completion mode.
    """
    
    def __init__(self, at_completer: AtFileCompleter):
        self.at_completer = at_completer
    
    def get_completions(self, document: Document, complete_event):
        """Delegate to AtFileCompleter and yield completions."""
        # Simply delegate to the underlying completer
        # The multi-column display is handled by the prompt session configuration
        # when this completer is active
        yield from self.at_completer.get_completions(document, complete_event)


class AtFileCompleterPlugin(Plugin):
    """Plugin for @file reference completion and expansion.
    
    Commands:
        get_at_file_completer: Returns a completer instance for prompt_toolkit
        invalidate_file_cache: Clears the file cache (call after file changes)
        parse_at_references: Parses @file references from text, returns file contents
        has_at_references: Quick check if text contains @file references
    """

    name = "at_file_completer"
    version = "1.0.0"
    premium = "free"
    debug = False
    verbose = False

    def __init__(self):
        super().__init__()
        self._completer: Optional[AtFileCompleter] = None
        self._wrapper: Optional[AtFileCompleterWrapper] = None
        self._project_root: Optional[Path] = None

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the at-file completer plugin."""
        super().init(cfg)
        
        # Explicitly apply config to ensure consistency across platforms/environments
        if 'debug' in cfg:
            self.debug = cfg['debug']
        if 'verbose' in cfg:
            self.verbose = cfg['verbose']

        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

    def _get_completer(self, project_root: Optional[Path] = None) -> AtFileCompleterWrapper:
        """Get or create the completer instance."""
        root = project_root or Path.cwd()

        if self._completer is None or self._project_root != root:
            self._project_root = root
            self._completer = AtFileCompleter(project_root=root)
            self._wrapper = AtFileCompleterWrapper(self._completer)

        return self._wrapper

    def _parse_at_references(self, text: str) -> Tuple[List[str], str]:
        """
        Parse @file references from text.

        Returns:
            Tuple of (list of file references, cleaned prompt text)
        """
        # Pattern to match @filepath (supports paths with /, -, _, .)
        # Must be preceded by whitespace or start of string
        # Stops at whitespace or end of string
        pattern = r'(?:^|\s)@([\w./\-_]+)'

        references = re.findall(pattern, text)

        # Remove the @references from the text for the cleaned prompt
        # Use a slightly different pattern that captures the whole @reference
        cleaned = re.sub(r'(?:^|\s)@[\w./\-_]+', ' ', text)
        # Clean up extra whitespace
        cleaned = ' '.join(cleaned.split())

        return references, cleaned

    def _expand_file_patterns(self, patterns: List[str], project_root: Path) -> List[str]:
        """Expand file patterns (including wildcards) to actual file paths."""
        expanded = []

        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern:
                continue

            # Check if it's a direct file path
            direct_path = project_root / pattern
            if direct_path.is_file():
                expanded.append(pattern)
                continue

            # Try glob expansion
            matched = list(project_root.glob(pattern))
            for match in matched:
                if match.is_file():
                    try:
                        rel_path = match.relative_to(project_root)
                        expanded.append(str(rel_path))
                    except ValueError:
                        expanded.append(pattern)

        return expanded

    def _read_files(self, file_paths: List[str], project_root: Path) -> Dict[str, str]:
        """Read file contents for the given paths."""
        contents = {}

        for file_path in file_paths:
            full_path = project_root / file_path
            if not full_path.is_file():
                if self.verbose:
                    rprint(f"[yellow]File not found: {file_path}[/]")
                continue

            try:
                contents[file_path] = full_path.read_text(encoding='utf-8')
            except Exception as e:
                if self.verbose:
                    rprint(f"[yellow]Could not read {file_path}: {e}[/]")

        return contents

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle commands for the at-file completer plugin."""

        if command_name == "get_at_file_completer":
            # Return a wrapper that forces multi-column display
            project_root = params.get("project_root")
            if project_root:
                project_root = Path(project_root)
            wrapper = self._get_completer(project_root)
            return {"completer": wrapper}

        if command_name == "invalidate_file_cache":
            # Invalidate the file cache (e.g., after file changes)
            if self._completer:
                self._completer.invalidate_cache()
            return {"status": "cache_invalidated"}

        if command_name == "parse_at_references":
            # Parse @file references from a prompt
            text = params.get("text", "")
            project_root = Path(params.get("project_root", "."))

            references, cleaned_prompt = self._parse_at_references(text)

            if not references:
                return None  # No @references found

            # Expand patterns to actual files
            expanded_files = self._expand_file_patterns(references, project_root)

            if not expanded_files:
                return {
                    "error": "No files found matching the @references",
                    "references": references
                }

            # Read file contents
            file_contents = self._read_files(expanded_files, project_root)

            if not file_contents:
                return {
                    "error": "Could not read any of the referenced files",
                    "references": references,
                    "expanded_files": expanded_files
                }

            return {
                "references": references,
                "expanded_files": expanded_files,
                "file_contents": file_contents,
                "cleaned_prompt": cleaned_prompt
            }

        if command_name == "has_at_references":
            # Quick check if text contains @references
            text = params.get("text", "")
            # Must be preceded by whitespace or start of string
            has_refs = bool(re.search(r'(?:^|\s)@[\w./\-_]+', text))
            return {"has_references": has_refs}

        return None

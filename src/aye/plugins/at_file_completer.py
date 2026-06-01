'''Plugin for @file reference completion and expansion.

Allows users to reference files inline using @filename syntax with autocomplete.
Example: "I want to update @main.py with a driver function"

Usage:
    - Type @ followed by a filename to get autocomplete suggestions
    - Multiple @references can be used in a single prompt
    - Supports relative paths: @src/utils.py
    - Supports wildcards in file patterns: @src/*.py, @*.py, @tests/test_*.py
    - Supports recursive globs: @src/**/*.py, @**/*.py
    - Supports directories: @src/ (includes all files in directory recursively)
    - Supports image attachments: @screenshot.png, @design/mockup.jpg

Image expansion rules (Phase 2 of image support):
    - Direct file references (e.g. @photo.png) always include the file, image or not.
    - Image-targeted globs (e.g. @*.png, @shots/*.jpg, @assets/**/*.webp)
      include matching images.
    - Directory references (e.g. @src/, implicit @src) exclude images.
    - Generic globs (e.g. @*.*, @src/**/*) exclude images.

Examples:
    "I want to update @main.py with a driver function"
    "Refactor @src/utils.py and @src/helpers.py to use async"
    "Explain what @config.py does"
    "Update all @*.py files with better logging"
    "Fix tests in @tests/test_*.py"
    "Refactor @src/**/*.py to use dependency injection"
    "Analyze @src/ for code quality issues"
    "Describe @screenshot.png"
    "Review @src/ui.py using @design/mockup.png"
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
from aye.model.attachments import (
    IMAGE_EXTENSIONS,
    _is_image_path,
    _is_image_targeted_pattern,
    load_image_attachment,
)


class AtFileCompleter(Completer):
    """
    Completes file and folder paths when user types '@' anywhere in the input.
    Supports relative paths and wildcards.
    Respects .gitignore and .ayeignore patterns.
    
    Folders are shown before files in completion suggestions.
    """

    def __init__(self, project_root: Optional[Path] = None, file_cache: Optional[List[str]] = None):
        self.project_root = project_root or Path.cwd()
        self._file_cache: Optional[List[str]] = file_cache
        # If file_cache is provided, also initialize folder_cache to empty list
        # to ensure cache is considered valid
        self._folder_cache: Optional[List[str]] = [] if file_cache is not None else None
        self._cache_valid = file_cache is not None

    def _get_project_items(self) -> Tuple[List[str], List[str]]:
        """Get list of files and folders in project, using cache if available.
        
        Respects .gitignore and .ayeignore patterns.
        
        Returns:
            Tuple of (files_list, folders_list)
        """
        if self._cache_valid and self._file_cache is not None and self._folder_cache is not None:
            return self._file_cache, self._folder_cache

        # Load ignore patterns using shared utility
        ignore_spec = load_ignore_patterns(self.project_root)

        # Build file and folder lists - respect ignore patterns
        files = []
        folders = set()  # Use set to avoid duplicates

        try:
            for root, dirs, filenames in os.walk(self.project_root):
                # Filter out ignored directories before descending
                rel_dir = Path(root).relative_to(self.project_root).as_posix()
                
                # Filter directories
                filtered_dirs = []
                for d in dirs:
                    dir_rel_path = os.path.join(rel_dir, d + "/") if rel_dir != '.' else d + "/"
                    if not ignore_spec.match_file(dir_rel_path) and not d.startswith('.'):
                        filtered_dirs.append(d)
                        # Add this directory to folders list
                        if rel_dir == '.':
                            folders.add(d)
                        else:
                            folders.add(os.path.join(rel_dir, d).replace('\\', '/'))
                
                dirs[:] = filtered_dirs

                # Process files
                for filename in filenames:
                    if filename.startswith('.'):
                        continue
                    
                    rel_file = os.path.join(rel_dir, filename) if rel_dir != '.' else filename
                    
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
        self._folder_cache = sorted(folders)
        self._cache_valid = True
        return self._file_cache, self._folder_cache

    def _get_project_files(self) -> List[str]:
        """Get list of files in project (for backward compatibility)."""
        files, _ = self._get_project_items()
        return files

    def invalidate_cache(self):
        """Invalidate the file cache to force refresh."""
        self._cache_valid = False
        self._file_cache = None
        self._folder_cache = None

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

        # Get all project files and folders
        all_files, all_folders = self._get_project_items()

        partial_lower = partial.lower()
        
        # Determine the directory context for filtering
        # If partial ends with '/', show contents of that directory
        # If partial contains '/', filter to items in that path
        dir_prefix = ''
        name_partial = partial_lower
        if '/' in partial:
            last_slash = partial.rfind('/')
            dir_prefix = partial[:last_slash + 1].lower()
            name_partial = partial[last_slash + 1:].lower()

        if not partial:
            # Empty partial: show top folders first, then top files
            folder_completions = []
            file_completions = []
            
            # Add folders (top-level only when no partial)
            for folder in all_folders:
                if '/' not in folder:  # Top-level folders only
                    folder_completions.append(folder)
            
            # Add files (top-level only when no partial)
            for filepath in all_files:
                if '/' not in filepath:  # Top-level files only
                    file_completions.append(filepath)
            
            # Yield folders first (with trailing slash)
            for folder in sorted(folder_completions)[:10]:
                yield Completion(
                    folder + '/',
                    start_position=-len(partial),
                    display=folder + '/',
                    display_meta="folder"
                )
            
            # Then yield files
            for filepath in sorted(file_completions)[:10]:
                yield Completion(
                    filepath,
                    start_position=-len(partial),
                    display=filepath,
                    display_meta="file"
                )
            return

        # Collect matching folders and files
        matching_folders = []
        matching_files = []
        
        for folder in all_folders:
            folder_lower = folder.lower()
            folder_name_lower = Path(folder).name.lower()
            
            # If we have a directory prefix, only show items in that directory
            if dir_prefix:
                if not folder_lower.startswith(dir_prefix):
                    continue
                # Get the part after the prefix
                remainder = folder[len(dir_prefix):]
                # Only show direct children (no more slashes)
                if '/' in remainder:
                    continue
                # Match against the name partial
                if name_partial and not remainder.lower().startswith(name_partial):
                    continue
            else:
                # No directory prefix - match against full path or name
                matches = (
                    folder_lower.startswith(partial_lower) or
                    folder_name_lower.startswith(partial_lower) or
                    partial_lower in folder_lower
                )
                if not matches:
                    continue
            
            matching_folders.append(folder)
        
        for filepath in all_files:
            filepath_lower = filepath.lower()
            filename_lower = Path(filepath).name.lower()
            
            # If we have a directory prefix, only show items in that directory
            if dir_prefix:
                if not filepath_lower.startswith(dir_prefix):
                    continue
                # Get the part after the prefix
                remainder = filepath[len(dir_prefix):]
                # Only show direct children (no more slashes)
                if '/' in remainder:
                    continue
                # Match against the name partial
                if name_partial and not remainder.lower().startswith(name_partial):
                    continue
            else:
                # No directory prefix - match against full path or name
                matches = (
                    filepath_lower.startswith(partial_lower) or
                    filename_lower.startswith(partial_lower) or
                    partial_lower in filepath_lower
                )
                if not matches:
                    continue
            
            matching_files.append(filepath)

        # Yield folders first (sorted), then files (sorted)
        if matching_folders or matching_files:
            for folder in sorted(matching_folders):
                yield Completion(
                    folder + '/',
                    start_position=-len(partial),
                    display=folder + '/',
                    display_meta="folder"
                )
            
            for filepath in sorted(matching_files):
                yield Completion(
                    filepath,
                    start_position=-len(partial),
                    display=filepath,
                    display_meta="file"
                )
            return

        # Fuzzy fallback on filenames (rapidfuzz) - only for files, not folders
        try:
            from rapidfuzz import process, fuzz
            file_names = [Path(fp).name for fp in all_files]
            matches = process.extract(
                partial, file_names,
                scorer=fuzz.partial_ratio,
                limit=8
            )
            for match_name, score, _ in matches:
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
                             and image attachments.
        has_at_references: Quick check if text contains @file references
    """

    name = "at_file_completer"
    version = "2.1.0"  # Phase 2 of image support: @-image references
    premium = "free"
    debug = False
    verbose = False

    # Regex pattern for @file references - includes wildcards (* and ?) and directories
    AT_REFERENCE_PATTERN = r'(?:^|\s)@([\w./\-_*?]+/?(?:\*\*/)?[\w./\-_*?]*)'

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
        references = re.findall(self.AT_REFERENCE_PATTERN, text)

        # Remove the @references from the text for the cleaned prompt
        cleaned = re.sub(r'(?:^|\s)@[\w./\-_*?]+/?(?:\*\*/)?[\w./\-_*?]*', ' ', text)
        # Clean up extra whitespace
        cleaned = ' '.join(cleaned.split())

        return references, cleaned

    def _expand_file_patterns(
        self,
        patterns: List[str],
        project_root: Path,
    ) -> Tuple[List[str], List[str]]:
        """Expand file patterns to (source_files, image_files).

        Applies the glob/directory image expansion rule (issue.md Section 3.1):
        - Direct file refs include the file regardless of type.
        - Image-targeted globs (e.g. ``*.png``, ``shots/*.jpg``) include images.
        - Directory refs (``src/`` or implicit) and generic globs (``*.*``,
          ``src/**/*``) exclude images.

        Supports:
        - Direct file paths: src/main.py, photo.png
        - Wildcards: *.py, *.png, src/*.py, tests/test_*.py
        - Question mark wildcards: file?.py
        - Recursive globs: **/*.py, src/**/*.py
        - Directories: src/ (expands to all files in directory recursively)

        Returns:
            (source_files, image_files): two lists of POSIX-style relative paths.
            Deduplicated and order-preserving.
        """
        source_files: List[str] = []
        image_files: List[str] = []
        seen_source: set = set()
        seen_image: set = set()

        ignore_spec = load_ignore_patterns(project_root)

        def _add_file(rel_path_str: str, file_path: Path, include_images: bool) -> None:
            # Skip hidden files/dirs (consistent with project scanning).
            try:
                rel_parts = Path(rel_path_str).parts
            except Exception:
                return
            if any(part.startswith('.') for part in rel_parts):
                return
            if ignore_spec.match_file(rel_path_str):
                return

            if _is_image_path(file_path):
                if include_images and rel_path_str not in seen_image:
                    seen_image.add(rel_path_str)
                    image_files.append(rel_path_str)
                # else: image explicitly excluded by expansion rule
            else:
                if rel_path_str not in seen_source:
                    seen_source.add(rel_path_str)
                    source_files.append(rel_path_str)

        for raw_pattern in patterns:
            pattern = raw_pattern.strip()
            if not pattern:
                continue

            # Classify the pattern BEFORE any normalization
            is_directory_ref = pattern.endswith('/')
            has_wildcard = ('*' in pattern) or ('?' in pattern)
            is_direct_ref = (not has_wildcard) and (not is_directory_ref)
            is_image_targeted = _is_image_targeted_pattern(pattern)

            # Default include-images flag based on pattern shape.
            include_images = is_direct_ref or is_image_targeted

            if is_directory_ref:
                pattern = pattern.rstrip('/')

            if is_direct_ref:
                direct_path = project_root / pattern
                if direct_path.is_file():
                    try:
                        rel_path = direct_path.relative_to(project_root)
                        _add_file(rel_path.as_posix(), direct_path, include_images)
                    except ValueError:
                        pass
                    continue
                elif direct_path.is_dir():
                    # Implicit directory reference (no trailing slash) -
                    # treat like @dir/ and exclude images.
                    is_directory_ref = True
                    include_images = False
                else:
                    # Does not exist; nothing to expand for this pattern.
                    continue

            if is_directory_ref:
                dir_path = project_root / pattern
                if dir_path.is_dir():
                    for file_path in dir_path.rglob('*'):
                        if not file_path.is_file():
                            continue
                        try:
                            rel_path = file_path.relative_to(project_root)
                            _add_file(rel_path.as_posix(), file_path, include_images)
                        except ValueError:
                            pass
            else:
                # Wildcard glob
                for file_path in project_root.glob(pattern):
                    if not file_path.is_file():
                        continue
                    try:
                        rel_path = file_path.relative_to(project_root)
                        _add_file(rel_path.as_posix(), file_path, include_images)
                    except ValueError:
                        pass

        return source_files, image_files

    def _read_files(self, file_paths: List[str], project_root: Path) -> Dict[str, str]:
        """Read text source-file contents for the given paths.

        Image-extension files should NOT be passed here; they are handled by
        ``_load_images``.
        """
        contents: Dict[str, str] = {}

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

    def _load_images(
        self,
        image_paths: List[str],
        project_root: Path,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Load image files into attachment dicts.

        Returns:
            (attachments, errors)
            - attachments: list of plain dicts compatible with ``ImageAttachment``.
            - errors: list of human-readable error strings, one per failed image.
              Images are never silently dropped (issue.md Section 1.3).
        """
        attachments: List[Dict[str, Any]] = []
        errors: List[str] = []

        for rel_path in image_paths:
            full_path = project_root / rel_path
            try:
                attachment = load_image_attachment(full_path, project_root)
                attachments.append(attachment)
            except FileNotFoundError as e:
                errors.append(f"{rel_path}: {e}")
            except ValueError as e:
                # Size limit / unsupported extension
                errors.append(f"{rel_path}: {e}")
            except OSError as e:
                errors.append(f"{rel_path}: I/O error: {e}")
            except Exception as e:  # pragma: no cover - defensive
                errors.append(f"{rel_path}: unexpected error: {e}")

        return attachments, errors

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

            # Expand patterns into source files and image files separately,
            # applying the glob/directory image rule.
            source_files, image_files = self._expand_file_patterns(
                references, project_root
            )
            expanded_files = source_files + image_files

            if not expanded_files:
                return {
                    "error": "No files found matching the @references",
                    "references": references,
                }

            # Read text source files.
            file_contents = self._read_files(source_files, project_root)

            # Load image attachments (never silently dropped).
            attachments, image_errors = self._load_images(image_files, project_root)

            # Build the response. Preserve legacy keys and add new ones.
            result: Dict[str, Any] = {
                "references": references,
                "expanded_files": expanded_files,
                "file_contents": file_contents,
                "attachments": attachments,
                "image_errors": image_errors,
                "has_image_references": bool(image_files),
                "has_source_references": bool(file_contents),
                "cleaned_prompt": cleaned_prompt,
            }

            # Surface a clear error when nothing usable was loaded.
            if not file_contents and not attachments:
                if image_errors:
                    result["error"] = (
                        "Failed to load image(s): " + "; ".join(image_errors)
                    )
                else:
                    result["error"] = "Could not read any of the referenced files"

            return result

        if command_name == "has_at_references":
            # Quick check if text contains @references
            text = params.get("text", "")
            has_refs = bool(re.search(self.AT_REFERENCE_PATTERN, text))
            return {"has_references": has_refs}

        return None

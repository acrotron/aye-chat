import os
import pathlib
from collections import Counter
from typing import List, Tuple, Dict, Any, Optional
import concurrent.futures
from rich import print as rprint

import pathspec  # pip install pathspec

from .plugin_base import Plugin

# Predefined list of source code extensions to consider
SOURCE_EXTENSIONS = {
    'py', 'js', 'jsx', 'ts', 'tsx', 'java', 'c', 'cpp', 'h', 'hpp',
    'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'kts', 'scala',
    'html', 'htm', 'css', 'scss', 'sass', 'less',
    'json', 'xml', 'yaml', 'yml', 'tf', 'sh', 'toml',
    'md', 'rst', 'txt',
    'sql',
    'asm'
}


class AutoDetectMaskPlugin(Plugin):
    name = "auto_detect_mask"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the auto detect mask plugin."""
        super().init(cfg)
        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")
        pass

    def _load_gitignore(self, root: pathlib.Path) -> pathspec.PathSpec:
        """
        Load ignore patterns from .ayeignore and .gitignore files in the root
        directory and all parent directories. This ensures that detection respects
        the same ignore rules as file collection.
        """
        patterns = []
        current_path = root.resolve()

        while True:
            for ignore_name in (".gitignore", ".ayeignore"):
                ignore_file = current_path / ignore_name
                if ignore_file.is_file():
                    try:
                        with ignore_file.open("r", encoding="utf-8") as f:
                            patterns.extend(
                                line.rstrip() for line in f 
                                if line.strip() and not line.strip().startswith("#")
                            )
                    except Exception:
                        # Ignore files we can't read
                        pass
            
            if current_path.parent == current_path:  # Reached filesystem root
                break
            
            current_path = current_path.parent

        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    def _is_binary(self, file_path: pathlib.Path, blocksize: int = 4096) -> bool:
        """
        Very fast heuristic: read the first `blocksize` bytes and look
        for a null byte. If present, we treat the file as binary.
        """
        try:
            with file_path.open("rb") as f:
                chunk = f.read(blocksize)
                return b"\0" in chunk
        except OSError:
            # If we cannot read the file (permissions, etc.) treat it as binary
            return True

    def _process_file(self, file_path: pathlib.Path) -> Optional[str]:
        """
        Process a single file: check if binary, and return extension if it's a source file.
        """
        if self._is_binary(file_path):
            return None
        ext = file_path.suffix.lower().lstrip(".")
        if ext and ext in SOURCE_EXTENSIONS:
            return ext
        return None

    def _detect_top_extensions(
        self,
        root: pathlib.Path,
        ignored: pathspec.PathSpec,
        max_exts: int = 5,
    ) -> Tuple[List[str], Counter]:
        """
        Walk the directory tree, filter with the ignore spec,
        count file extensions (case-insensitive) from predefined source extensions list
        and return the most common ones (up to `max_exts`).

        Uses parallel processing for file scanning.

        Returns
        -------
        (ext_list, counter)
            ext_list – list of extensions without the leading dot,
            sorted by frequency (most common first).
            counter  – the full Counter object (useful for debugging).
        """
        file_paths: List[pathlib.Path] = []

        for dirpath, dirnames, filenames in os.walk(root):
            # -----------------------------------------------------------------
            # 1. prune ignored directories **before** we descend into them
            # -----------------------------------------------------------------
            rel_dir = pathlib.Path(dirpath).relative_to(root).as_posix()
            # `ignored.match_file` works on relative paths, just like git does.
            dirnames[:] = [
                d for d in dirnames
                if not ignored.match_file(os.path.join(rel_dir, d + "/"))
                and not d.startswith(".")   # hidden dirs (e.g. .venv) are ignored as well
            ]

            # -----------------------------------------------------------------
            # 2. collect files to process
            # -----------------------------------------------------------------
            for name in filenames:
                rel_file = os.path.join(rel_dir, name)
                if ignored.match_file(rel_file) or name.startswith("."):
                    continue

                p = pathlib.Path(dirpath) / name
                file_paths.append(p)

        # -----------------------------------------------------------------
        # 3. process files in parallel
        # -----------------------------------------------------------------
        ext_counter: Counter = Counter()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(self._process_file, fp) for fp in file_paths]
            for future in concurrent.futures.as_completed(futures):
                try:
                    ext = future.result()
                    if ext:
                        ext_counter[ext] += 1
                except Exception:
                    # Skip files that cause errors (e.g., permission issues, unexpected exceptions)
                    # to ensure robust parallel processing
                    continue

        if not ext_counter:
            return [], ext_counter

        most_common = [ext for ext, _ in ext_counter.most_common(max_exts)]
        return most_common, ext_counter

    def auto_detect_mask(
        self,
        project_root: str,
        default_mask: str = "*.py",
        max_exts: int = 5,
    ) -> str:
        """
        Return a glob mask that covers the most common source extensions
        in *project_root*.

        Parameters
        ----------
        project_root : str
            Path to the directory that should be inspected.
        default_mask : str, optional
            Mask to use when no suitable files are found.
        max_exts : int, optional
            Upper bound on how many different extensions are included
            in the mask (default 5).

        Returns
        -------
        str
            A comma-separated glob mask, e.g.  "*.js,*.jsx,*.ts".
            If detection fails, ``default_mask`` is returned.
        """
        root = pathlib.Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"'{project_root}' is not a directory")

        # Load .gitignore and .ayeignore patterns (if any)
        ignored = self._load_gitignore(root)

        # Find the most common extensions
        top_exts, counter = self._detect_top_extensions(root, ignored, max_exts)

        if not top_exts:
            # No eligible files – fall back to the user-provided default
            return default_mask

        # Build the mask string:  "*.ext1,*.ext2,…"
        mask = ",".join(f"*.{ext}" for ext in top_exts)
        return mask

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle auto-detect mask commands through plugin system."""
        if command_name == "auto_detect_mask":
            project_root = params.get("project_root", ".")
            default_mask = params.get("default_mask", "*.py")
            max_exts = params.get("max_exts", 5)
            
            mask = self.auto_detect_mask(
                project_root=project_root,
                default_mask=default_mask,
                max_exts=max_exts
            )
            return {"mask": mask}
        
        return None

"""
auto_detect_mask.py

Detect a sensible file‑mask for the current project.

Features
--------
* Respects .gitignore and .ayeignore (uses the `pathspec` library).
* Skips hidden directories and binary files (quick null‑byte test).
* Counts extensions from a predefined list of source code extensions
* Builds a glob mask such as "*.js,*.jsx,*.ts" containing the most‑common source extensions.
* Falls back to a user‑provided default mask when no eligible files are found.

Install
-------
pip install pathspec
"""

import os
import pathlib
from collections import Counter
from typing import List, Tuple

import pathspec  # pip install pathspec

# Predefined list of source code extensions to consider
SOURCE_EXTENSIONS = {
    'py', 'js', 'jsx', 'ts', 'tsx', 'java', 'c', 'cpp', 'h', 'hpp',
    'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'kts', 'scala',
    'html', 'htm', 'css', 'scss', 'sass', 'less',
    'json', 'xml', 'yaml', 'yml',
    'md', 'rst', 'txt'
}


def _load_gitignore(root: pathlib.Path) -> pathspec.PathSpec:
    """
    Build a PathSpec that matches patterns from every `.gitignore`
    and `.ayeignore` file found under *root* (including the top‑level one).
    """
    ignore_files: List[pathlib.Path] = []
    for dirpath, _, filenames in os.walk(root):
        for ignore_file in [".gitignore", ".ayeignore"]:
            if ignore_file in filenames:
                ignore_files.append(pathlib.Path(dirpath) / ignore_file)

    # Combine all patterns – `pathspec` can take an iterator of lines.
    patterns = []
    for ig in ignore_files:
        with ig.open("r", encoding="utf-8") as f:
            patterns.extend(line.rstrip() for line in f if line.strip() and not line.strip().startswith("#"))

    # `GitIgnoreSpec` implements the same syntax as git.
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _is_binary(file_path: pathlib.Path, blocksize: int = 4096) -> bool:
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


def _detect_top_extensions(
    root: pathlib.Path,
    ignored: pathspec.PathSpec,
    max_exts: int = 5,
) -> Tuple[List[str], Counter]:
    """
    Walk the directory tree, filter with the ignore spec,
    count file extensions (case‑insensitive) from predefined source extensions list
    and return the most common ones (up to `max_exts`).

    Returns
    -------
    (ext_list, counter)
        ext_list – list of extensions without the leading dot,
        sorted by frequency (most common first).
        counter  – the full Counter object (useful for debugging).
    """
    ext_counter: Counter = Counter()

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
        # 2. process files
        # -----------------------------------------------------------------
        for name in filenames:
            rel_file = os.path.join(rel_dir, name)
            if ignored.match_file(rel_file) or name.startswith("."):
                continue

            p = pathlib.Path(dirpath) / name
            if _is_binary(p):
                continue

            ext = p.suffix.lower().lstrip(".")
            # Only count extensions that are in our predefined source list
            if ext and ext in SOURCE_EXTENSIONS:
                ext_counter[ext] += 1

    if not ext_counter:
        return [], ext_counter

    most_common = [ext for ext, _ in ext_counter.most_common(max_exts)]
    return most_common, ext_counter


def auto_detect_mask(
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
        A comma‑separated glob mask, e.g.  "*.js,*.jsx,*.ts".
        If detection fails, ``default_mask`` is returned.
    """
    root = pathlib.Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"'{project_root}' is not a directory")

    # Load .gitignore and .ayeignore patterns (if any)
    ignored = _load_gitignore(root)

    # Find the most common extensions
    top_exts, counter = _detect_top_extensions(root, ignored, max_exts)

    if not top_exts:
        # No eligible files – fall back to the user‑provided default
        return default_mask

    # Build the mask string:  "*.ext1,*.ext2,…"
    mask = ",".join(f"*.{ext}" for ext in top_exts)
    return mask


# ----------------------------------------------------------------------
# Example usage
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Detect a sensible file‑mask for a project."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to the project root (default: current directory).",
    )
    parser.add_argument(
        "--default",
        default="*.py",
        help="Mask to use when detection fails.",
    )
    parser.add_argument(
        "--max-exts",
        type=int,
        default=5,
        help="Maximum number of extensions to include in the mask.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full extension counter as JSON (debug).",
    )
    args = parser.parse_args()

    mask = auto_detect_mask(args.path, default_mask=args.default, max_exts=args.max_exts)
    print(f"Detected mask: {mask}")

    if args.json:
        # Show the raw frequency table for insight
        root = pathlib.Path(args.path).expanduser().resolve()
        ignored = _load_gitignore(root)
        _, counter = _detect_top_extensions(root, ignored, args.max_exts * 10)
        print("\nExtension frequencies:")
        print(json.dumps(counter, indent=2, sort_keys=True))

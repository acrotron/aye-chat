# file_processor.py
from pathlib import Path
from typing import List, Dict, Any


def make_paths_relative(files: List[Dict[str, Any]], root: Path) -> List[Dict[str, Any]]:
    """
    Strip *root* from any file_name that already starts with it.
    This prevents double-prefixing like `src/aye/src/aye/foo.py`.
    
    Args:
        files: List of file dictionaries with 'file_name' keys
        root: Root path to make files relative to
        
    Returns:
        Modified list with relative paths
    """
    root = root.resolve()
    for f in files:
        if "file_name" not in f:
            continue
        try:
            p = Path(f["file_name"]).resolve()
            if p.is_relative_to(root):
                f["file_name"] = str(p.relative_to(root))
        except Exception:
            # If the path cannot be resolved or Python <3.9, leave it unchanged
            pass
    return files


def filter_unchanged_files(updated_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter out files from updated_files list if their content hasn't changed 
    compared to on-disk version.
    
    Args:
        updated_files: List of file dictionaries with 'file_name' and 'file_content' keys
        
    Returns:
        List containing only files that have actually changed
    """
    changed_files = []
    for item in updated_files:
        if "file_name" not in item or "file_content" not in item:
            continue
            
        file_path = Path(item["file_name"])
        new_content = item["file_content"]
        
        # If file doesn't exist on disk, consider it changed (new file)
        if not file_path.exists():
            changed_files.append(item)
            continue
            
        # Read current content and compare
        try:
            current_content = file_path.read_text(encoding="utf-8")
            if current_content != new_content:
                changed_files.append(item)
        except Exception:
            # If we can't read the file, assume it should be updated
            changed_files.append(item)
            
    return changed_files

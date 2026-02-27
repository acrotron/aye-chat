# file_processor.py
from pathlib import Path
from typing import List, Dict, Any, Optional


def make_paths_relative(files: List[Dict[str, Any]], root: Path) -> List[Dict[str, Any]]:
    """
    Convert file paths to be relative to the project root.
    
    This handles both absolute paths and paths that need resolution.
    Paths are resolved against the project root, NOT the current working directory.
    
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
        
        file_name = f["file_name"]
        
        try:
            p = Path(file_name)
            
            if p.is_absolute():
                # Absolute path: check if it's under root and make relative
                if p.is_relative_to(root):
                    f["file_name"] = str(p.relative_to(root))
                # else: leave absolute paths outside root unchanged
            else:
                # Relative path: resolve against ROOT (not CWD) to normalize,
                # then make relative to root again
                # This handles cases like "./src/../src/file.py" -> "src/file.py"
                resolved = (root / p).resolve()
                if resolved.is_relative_to(root):
                    f["file_name"] = str(resolved.relative_to(root))
                # else: path resolved outside root, leave as-is
                
        except Exception:
            # If the path cannot be processed, leave it unchanged
            pass
    return files


def filter_unchanged_files(updated_files: List[Dict[str, Any]], root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Filter out files from updated_files list if their content hasn't changed 
    compared to on-disk version.
    
    Args:
        updated_files: List of file dictionaries with 'file_name' and 'file_content' keys
        root: Optional project root path. If provided, relative paths are resolved against it.
        
    Returns:
        List containing only files that have actually changed
    """
    changed_files = []
    for item in updated_files:
        if "file_name" not in item or "file_content" not in item:
            continue
            
        file_name = item["file_name"]
        new_content = item["file_content"]
        
        # Resolve the file path
        if root is not None and not Path(file_name).is_absolute():
            file_path = root / file_name
        else:
            file_path = Path(file_name)
        
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

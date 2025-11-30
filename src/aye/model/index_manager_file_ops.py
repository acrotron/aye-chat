"""File operations for IndexManager.

This module contains classes for:
- Checking file status against the index
- Categorizing files into coarse/refine/unchanged
- Loading and saving the hash index
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable

from .index_manager_utils import calculate_hash


# =============================================================================
# File Status Checker
# =============================================================================

class FileStatusChecker:
    """
    Checks the status of files against an existing index.
    
    Determines if files are unchanged, modified, need refinement, or have errors.
    """
    
    def __init__(self, root_path: Path):
        self.root_path = root_path
    
    def check_file_status(
        self, 
        file_path: Path, 
        old_index: Dict[str, Any]
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Check a single file against the index to determine its status.
        
        Args:
            file_path: Path to the file to check
            old_index: The existing hash index
            
        Returns:
            A tuple of (status, new_metadata).
            Status can be 'unchanged', 'modified', 'needs_refinement', or 'error'.
        """
        rel_path_str = file_path.relative_to(self.root_path).as_posix()
        old_file_meta = old_index.get(rel_path_str)
        
        # Try to get file stats
        try:
            stats = file_path.stat()
            mtime = stats.st_mtime
            size = stats.st_size
        except FileNotFoundError:
            return "error", None

        is_new_format = isinstance(old_file_meta, dict)

        # Fast check: if mtime and size are the same, assume unchanged
        if is_new_format and old_file_meta.get("mtime") == mtime and old_file_meta.get("size") == size:
            if not old_file_meta.get("refined", False):
                return "needs_refinement", old_file_meta
            return "unchanged", old_file_meta

        # Slower check: read file and compare hashes
        try:
            content = file_path.read_text(encoding="utf-8")
            current_hash = calculate_hash(content)
        except (IOError, UnicodeDecodeError):
            return "error", old_file_meta

        old_hash = old_file_meta.get("hash") if is_new_format else old_file_meta
        
        if current_hash == old_hash:
            # Hash matches, but mtime/size didn't - update meta and check refinement
            updated_meta = old_file_meta.copy() if is_new_format else {}
            updated_meta.update({"hash": current_hash, "mtime": mtime, "size": size})
            if not updated_meta.get("refined", False):
                return "needs_refinement", updated_meta
            return "unchanged", updated_meta
        
        # File is modified
        new_meta = {"hash": current_hash, "mtime": mtime, "size": size, "refined": False}
        return "modified", new_meta


# =============================================================================
# File Categorizer
# =============================================================================

class FileCategorizer:
    """
    Categorizes files into groups for indexing operations.
    
    Groups files into:
    - Files needing coarse indexing (new/modified)
    - Files needing refinement
    - Unchanged files
    """
    
    def __init__(self, root_path: Path, should_stop_callback: Callable[[], bool]):
        self.root_path = root_path
        self.should_stop = should_stop_callback
        self.status_checker = FileStatusChecker(root_path)
    
    def categorize_files(
        self, 
        current_files: List[Path], 
        old_index: Dict[str, Any]
    ) -> Tuple[List[str], List[str], Dict[str, Dict[str, Any]]]:
        """
        Categorize current files into those needing coarse indexing, 
        refinement, or unchanged.
        
        Args:
            current_files: List of current project files
            old_index: The existing hash index
            
        Returns:
            Tuple of (files_to_coarse_index, files_to_refine, new_index)
        """
        files_to_coarse_index: List[str] = []
        files_to_refine: List[str] = []
        new_index: Dict[str, Dict[str, Any]] = {}

        for file_path in current_files:
            if self.should_stop():
                break
                
            rel_path_str = file_path.relative_to(self.root_path).as_posix()
            status, meta = self.status_checker.check_file_status(file_path, old_index)

            if status == "modified":
                files_to_coarse_index.append(rel_path_str)
                if meta:
                    new_index[rel_path_str] = meta
            elif status == "needs_refinement":
                files_to_refine.append(rel_path_str)
                if meta:
                    new_index[rel_path_str] = meta
            elif status == "unchanged":
                if meta:
                    new_index[rel_path_str] = meta
            # 'error' status is ignored

        return files_to_coarse_index, files_to_refine, new_index


# =============================================================================
# Index Persistence
# =============================================================================

class IndexPersistence:
    """
    Handles loading and saving the hash index to disk.
    
    Provides atomic file operations with temporary file usage.
    """
    
    def __init__(self, index_dir: Path, hash_index_path: Path):
        self.index_dir = index_dir
        self.hash_index_path = hash_index_path
    
    def load_index(self) -> Dict[str, Any]:
        """
        Load the existing hash index from disk.
        
        Returns:
            The index dictionary, or empty dict if not found or invalid.
        """
        if self.hash_index_path.is_file():
            try:
                return json.loads(self.hash_index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}
    
    def save_index(self, index_data: Dict[str, Any]) -> bool:
        """
        Save the hash index to disk atomically.
        
        Uses a temporary file to ensure atomic writes.
        
        Args:
            index_data: The index dictionary to save
            
        Returns:
            True on success, False on failure
        """
        if not index_data:
            return True
            
        self.index_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.hash_index_path.with_suffix('.json.tmp')
        
        try:
            temp_path.write_text(json.dumps(index_data, indent=2), encoding="utf-8")
            os.replace(temp_path, self.hash_index_path)
            return True
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            return False


# =============================================================================
# Deleted File Handler
# =============================================================================

def get_deleted_files(
    current_file_paths: set, 
    old_index: Dict[str, Any]
) -> List[str]:
    """
    Find files that have been deleted (in old index but not in current files).
    
    Args:
        current_file_paths: Set of current file paths (as strings)
        old_index: The existing hash index
        
    Returns:
        List of deleted file paths
    """
    return list(set(old_index.keys()) - current_file_paths)

"""Command implementations for Aye Chat.

This module contains the core command logic used by the REPL,
including project initialization, snapshot management, and diff operations.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich import print as rprint

from aye.controller.plugin_manager import PluginManager
from aye.model import onnx_manager
from aye.model import download_plugins  # Keep for backward compatibility with test patches
from aye.model.auth import (
    get_token,
    get_user_config,
    set_user_config,
    login_flow,
    delete_token,
)
from aye.model.config import DEFAULT_MODEL_ID, MODELS, SMALL_PROJECT_FILE_LIMIT
from aye.model.snapshot import (
    list_snapshots,
    prune_snapshots as snapshot_prune,
    restore_snapshot,
    cleanup_snapshots as snapshot_cleanup,
    _list_all_snapshots_with_metadata,
)
from aye.model.source_collector import get_project_files


# =============================================================================
# Project Configuration
# =============================================================================

@dataclass
class ProjectConfig:
    """Project configuration built up during initialization.
    
    This dataclass holds all configuration needed for a chat session,
    populated by ProjectContextBuilder.
    """
    # Core paths
    root: Optional[Path] = None
    
    # Settings
    verbose: bool = False
    ground_truth: Optional[str] = None
    file_mask: str = "*.py"
    selected_model: str = DEFAULT_MODEL_ID
    
    # RAG/Indexing
    use_rag: bool = False
    index_manager: Optional[Any] = None
    
    # Plugins
    plugin_manager: Optional[PluginManager] = None
    
    # Runtime state (not part of initialization)
    _restore_tip_shown: bool = field(default=False, repr=False)


class ProjectContextBuilder:
    """Builds project context with configurable initialization steps.
    
    Uses the builder pattern to separate initialization concerns,
    making each step independently testable and reusable.
    
    Example:
        conf = (
            ProjectContextBuilder()
            .with_verbose()
            .with_root(Path("./myproject"))
            .with_plugins()
            .with_file_mask("*.py")
            .with_indexing()
            .with_model()
            .build()
        )
    """
    
    def __init__(self):
        """Initialize builder with empty config."""
        self._conf = ProjectConfig()
        self._verbose_loaded = False
    
    def with_verbose(self) -> 'ProjectContextBuilder':
        """Load verbose setting from user config.
        
        Returns:
            Self for method chaining.
        """
        self._conf.verbose = get_user_config("verbose", "off").lower() == "on"
        self._verbose_loaded = True
        return self
    
    def with_ground_truth(self, path: Optional[str]) -> 'ProjectContextBuilder':
        """Load ground truth file content if path provided.
        
        Args:
            path: Path to ground truth file, or None to skip.
            
        Returns:
            Self for method chaining.
            
        Raises:
            FileNotFoundError: If path provided but file doesn't exist.
        """
        if path:
            prompt_file = Path(path)
            if not prompt_file.exists():
                raise FileNotFoundError(f"Ground truth file not found: {path}")
            self._conf.ground_truth = prompt_file.read_text(encoding="utf-8")
        return self
    
    def with_root(self, root: Optional[Path]) -> 'ProjectContextBuilder':
        """Set project root, finding it automatically if not provided.
        
        Args:
            root: Explicit project root, or None to auto-detect.
            
        Returns:
            Self for method chaining.
        """
        if root:
            self._conf.root = root.resolve()
        else:
            self._conf.root = _find_project_root(Path.cwd())
        return self
    
    def with_plugins(self) -> 'ProjectContextBuilder':
        """Initialize plugin manager and discover plugins.
        
        Returns:
            Self for method chaining.
        """
        pm = PluginManager(verbose=self._conf.verbose)
        pm.discover()
        self._conf.plugin_manager = pm
        return self
    
    def with_file_mask(
        self, 
        mask: Optional[str] = None,
        auto_detect: bool = True
    ) -> 'ProjectContextBuilder':
        """Set file mask, optionally auto-detecting from project.
        
        Args:
            mask: Explicit file mask (e.g., "*.py,*.js"), or None.
            auto_detect: If True and mask is None, use plugin to detect.
            
        Returns:
            Self for method chaining.
        """
        if mask:
            self._conf.file_mask = mask
        elif auto_detect and self._conf.plugin_manager and self._conf.root:
            response = self._conf.plugin_manager.handle_command(
                "auto_detect_mask",
                {"project_root": str(self._conf.root)}
            )
            if response and response.get("mask"):
                self._conf.file_mask = response["mask"]
                if self._conf.verbose:
                    rprint(f"[cyan]Auto-detected file mask: {self._conf.file_mask}[/]")
            else:
                self._conf.file_mask = "*.py"
        return self
    
    def with_indexing(self) -> 'ProjectContextBuilder':
        """Determine project size and initialize RAG indexing if needed.
        
        Small projects skip RAG and include all files directly.
        Larger projects use the IndexManager for intelligent retrieval.
        
        Returns:
            Self for method chaining.
        """
        if not self._conf.root:
            return self
        
        is_small, project_files = _is_small_project(
            self._conf.root,
            self._conf.file_mask,
            self._conf.verbose
        )
        
        self._conf.use_rag = not is_small
        
        if not is_small:
            try:
                # Import from the actual submodule location
                from aye.model.index_manager.index_manager import IndexManager
                self._conf.index_manager = IndexManager(
                    self._conf.root,
                    self._conf.file_mask,
                    verbose=self._conf.verbose
                )
                self._conf.index_manager.prepare_sync(verbose=self._conf.verbose)
            except Exception as e:
                if self._conf.verbose:
                    rprint(f"[yellow]Warning: Could not initialize index manager: {e}[/]")
                self._conf.use_rag = False
                self._conf.index_manager = None
        
        return self
    
    def with_model(self) -> 'ProjectContextBuilder':
        """Load selected model from user config.
        
        Returns:
            Self for method chaining.
        """
        saved_model = get_user_config("selected_model")
        if saved_model:
            # Validate model exists
            if any(m["id"] == saved_model for m in MODELS):
                self._conf.selected_model = saved_model
            else:
                # Model doesn't exist anymore, reset to default
                self._conf.selected_model = DEFAULT_MODEL_ID
                set_user_config("selected_model", DEFAULT_MODEL_ID)
        else:
            self._conf.selected_model = DEFAULT_MODEL_ID
        return self
    
    def build(self) -> ProjectConfig:
        """Build and return the final configuration.
        
        Returns:
            Populated ProjectConfig instance.
        """
        return self._conf


# =============================================================================
# Helper Functions for Builder
# =============================================================================

def _find_project_root(start: Path) -> Path:
    """Find project root by looking for common markers.
    
    Looks for .git, pyproject.toml, setup.py, package.json, etc.
    Falls back to start directory if no markers found.
    
    Args:
        start: Directory to start searching from.
        
    Returns:
        Project root path.
    """
    markers = [
        ".git",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        ".ayeignore",
    ]
    
    current = start.resolve()
    
    # Walk up the directory tree
    for _ in range(50):  # Limit to prevent infinite loops
        for marker in markers:
            if (current / marker).exists():
                return current
        
        parent = current.parent
        if parent == current:
            # Reached root
            break
        current = parent
    
    # No markers found, use start directory
    return start.resolve()


def _is_small_project(
    root: Path,
    file_mask: str,
    verbose: bool = False
) -> Tuple[bool, List[Path]]:
    """Determine if project is small enough to skip RAG.
    
    Args:
        root: Project root path.
        file_mask: File patterns to match (e.g., "*.py,*.js").
        verbose: Enable verbose output.
        
    Returns:
        Tuple of (is_small, list_of_files).
    """
    try:
        files = get_project_files(str(root), file_mask)
        file_count = len(files)
        
        is_small = file_count <= SMALL_PROJECT_FILE_LIMIT
        
        if verbose:
            if is_small:
                rprint(f"[cyan]Small project ({file_count} files) - including all files directly[/]")
            else:
                rprint(f"[cyan]Large project ({file_count} files) - using RAG indexing[/]")
        
        return is_small, files
    except Exception as e:
        if verbose:
            rprint(f"[yellow]Warning: Could not scan project files: {e}[/]")
        return True, []


# =============================================================================
# Public API - Backward Compatible
# =============================================================================

def initialize_project_context(
    root: Optional[Path] = None,
    file_mask: Optional[str] = None,
    ground_truth_file: Optional[str] = None,
) -> ProjectConfig:
    """Initialize project context for a chat session.
    
    This function maintains backward compatibility while delegating
    to the ProjectContextBuilder for actual initialization.
    
    Args:
        root: Project root path, or None to auto-detect.
        file_mask: File patterns to include, or None to auto-detect.
        ground_truth_file: Path to ground truth file, or None.
        
    Returns:
        Populated ProjectConfig instance.
    """
    # Download ONNX model if needed (side effect, do once early)
    try:
        onnx_manager.download_model_if_needed(background=False)
    except Exception:
        # Non-critical, continue without ONNX
        pass
    
    return (
        ProjectContextBuilder()
        .with_verbose()
        .with_ground_truth(ground_truth_file)
        .with_root(root)
        .with_plugins()
        .with_file_mask(file_mask)
        .with_indexing()
        .with_model()
        .build()
    )


# =============================================================================
# Auth Commands
# =============================================================================

def login_and_fetch_plugins() -> None:
    """Perform login flow and fetch plugins.
    
    This combines the login prompt with plugin download.
    """
    login_flow()
    download_plugins.fetch_plugins()


def get_auth_status_token() -> Optional[str]:
    """Get the current auth token for status display.
    
    Returns:
        Token string if available, None otherwise.
    """
    return get_token()


def logout() -> None:
    """Remove stored authentication token."""
    delete_token()


# =============================================================================
# Snapshot Commands
# =============================================================================

def get_snapshot_history() -> List[str]:
    """Get list of all snapshots with metadata.
    
    Returns:
        List of formatted snapshot strings for display.
    """
    return _list_all_snapshots_with_metadata()


def get_snapshot_content(file_path: Path, ordinal: str) -> Optional[str]:
    """Get the content of a file from a specific snapshot.
    
    Args:
        file_path: Path to the file.
        ordinal: Snapshot ordinal (e.g., "001").
        
    Returns:
        File content as string, or None if not found.
    """
    from aye.model.snapshot import get_backend
    
    backend = get_backend()
    
    # Find the snapshot matching the ordinal
    snapshots = list_snapshots(file_path)
    
    for snap_id, snap_path in snapshots:
        # Extract ordinal from snap_id (format: "001_20250101T000000")
        snap_ordinal = snap_id.split("_")[0] if "_" in snap_id else snap_id
        
        # Normalize ordinal comparison (handle "1" vs "001")
        try:
            if int(snap_ordinal) == int(ordinal):
                # For file-based backend, snap_path is the file path
                snap_file = Path(snap_path)
                if snap_file.exists():
                    return snap_file.read_text(encoding="utf-8")
        except ValueError:
            if snap_ordinal == ordinal:
                snap_file = Path(snap_path)
                if snap_file.exists():
                    return snap_file.read_text(encoding="utf-8")
    
    return None


def restore_from_snapshot(
    ordinal: Optional[str] = None,
    file_name: Optional[str] = None
) -> None:
    """Restore files from a snapshot.
    
    Args:
        ordinal: Snapshot ordinal (e.g., "001"), or None for latest.
        file_name: Specific file to restore, or None for all files.
    """
    restore_snapshot(ordinal=ordinal, file_name=file_name)


def prune_snapshots(keep_count: int = 10) -> int:
    """Delete old snapshots, keeping the most recent.
    
    Args:
        keep_count: Number of recent snapshots to keep.
        
    Returns:
        Number of snapshots deleted.
    """
    return snapshot_prune(keep_count=keep_count)


def cleanup_old_snapshots(older_than_days: int = 30) -> int:
    """Delete snapshots older than specified days.
    
    Args:
        older_than_days: Age threshold in days.
        
    Returns:
        Number of snapshots deleted.
    """
    return snapshot_cleanup(older_than_days=older_than_days)


# =============================================================================
# Diff Commands
# =============================================================================

def get_diff_paths(
    file_name: str,
    snap1: Optional[str] = None,
    snap2: Optional[str] = None
) -> Tuple[Path, Optional[Path], bool]:
    """Get paths for diff comparison.
    
    Args:
        file_name: File to diff.
        snap1: First snapshot ordinal, or None.
        snap2: Second snapshot ordinal, or None.
        
    Returns:
        Tuple of (current_path, snapshot_path, is_stash_ref).
    """
    current_path = Path(file_name)
    
    if snap1 and snap2:
        # Compare two snapshots
        # This would need snapshot paths - for now return current vs snap1
        pass
    
    # Get latest snapshot for file
    snapshots = list_snapshots(current_path)
    
    if not snapshots:
        return current_path, None, False
    
    # snapshots is list of (batch_id, snapshot_path) tuples
    if isinstance(snapshots[0], tuple):
        _, snapshot_path = snapshots[0]
        return current_path, Path(snapshot_path), False
    
    return current_path, None, False

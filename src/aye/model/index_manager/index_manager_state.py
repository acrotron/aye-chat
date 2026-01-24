"""State management classes for IndexManager.

This module contains:
- IndexConfig: Configuration dataclass
- SafeState: Thread-safe state container
- IndexingState: All indexing state in one place
- ProgressTracker: Progress display abstraction
- InitializationCoordinator: Initialization logic
- ErrorHandler: Centralized error handling
"""

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional

from rich import print as rprint


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class IndexConfig:
    """Configuration for IndexManager."""
    root_path: Path
    file_mask: str
    verbose: bool = False
    debug: bool = False
    save_interval: int = 20
    max_workers: int = 2
    
    @classmethod
    def from_params(
        cls,
        root_path: Path,
        file_mask: str,
        verbose: bool = False,
        debug: bool = False
    ) -> 'IndexConfig':
        """Create config from individual parameters (backward compatibility)."""
        from .index_manager_utils import MAX_WORKERS
        return cls(
            root_path=root_path,
            file_mask=file_mask,
            verbose=verbose,
            debug=debug,
            max_workers=MAX_WORKERS
        )
    
    @property
    def index_dir(self) -> Path:
        """Get the index directory path."""
        return self.root_path / ".aye"
    
    @property
    def hash_index_path(self) -> Path:
        """Get the hash index file path."""
        return self.index_dir / "file_index.json"
    
    @property
    def chroma_db_path(self) -> Path:
        """Get the ChromaDB directory path."""
        return self.index_dir / "chroma_db"


# =============================================================================
# Thread-Safe State Container
# =============================================================================

class SafeState:
    """Thread-safe state container with simple get/update interface."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}
    
    def update(self, key: str, value: Any) -> None:
        """Update a single value."""
        with self._lock:
            self._data[key] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value."""
        with self._lock:
            return self._data.get(key, default)
    
    def update_many(self, updates: Dict[str, Any]) -> None:
        """Update multiple values atomically."""
        with self._lock:
            self._data.update(updates)
    
    def get_many(self, keys: List[str]) -> Dict[str, Any]:
        """Get multiple values atomically."""
        with self._lock:
            return {k: self._data.get(k) for k in keys}
    
    def increment(self, key: str, amount: int = 1) -> int:
        """Increment a numeric value and return the new value."""
        with self._lock:
            current = self._data.get(key, 0)
            new_value = current + amount
            self._data[key] = new_value
            return new_value


# =============================================================================
# Indexing State
# =============================================================================

@dataclass
class IndexingState:
    """
    Consolidated state for all indexing operations.
    
    Replaces multiple individual instance variables with a single state object.
    """
    # Status flags
    is_indexing: bool = False
    is_refining: bool = False
    is_discovering: bool = False
    shutdown_requested: bool = False
    
    # Progress counters
    coarse_total: int = 0
    coarse_processed: int = 0
    refine_total: int = 0
    refine_processed: int = 0
    discovery_total: int = 0
    discovery_processed: int = 0
    
    # Generation counter for invalidating old runs
    generation: int = 0
    
    # Work queues
    files_to_coarse_index: List[str] = field(default_factory=list)
    files_to_refine: List[str] = field(default_factory=list)
    
    # Index data
    target_index: Dict[str, Any] = field(default_factory=dict)
    current_index_on_disk: Dict[str, Any] = field(default_factory=dict)
    
    def reset_coarse_progress(self, total: int) -> None:
        """Reset coarse indexing progress."""
        self.coarse_total = total
        self.coarse_processed = 0
    
    def reset_refine_progress(self, total: int) -> None:
        """Reset refinement progress."""
        self.refine_total = total
        self.refine_processed = 0
    
    def reset_discovery_progress(self) -> None:
        """Reset discovery progress."""
        self.discovery_total = 0
        self.discovery_processed = 0
    
    def increment_generation(self) -> int:
        """Increment and return the new generation."""
        self.generation += 1
        return self.generation
    
    def has_work(self) -> bool:
        """Check if there's indexing work to do."""
        return bool(self.files_to_coarse_index or self.files_to_refine)
    
    def is_active(self) -> bool:
        """Check if any background work is in progress."""
        return self.is_indexing or self.is_refining or self.is_discovering
    
    def clear_work_queues(self) -> None:
        """Clear all work queues."""
        self.files_to_coarse_index = []
        self.files_to_refine = []
        self.target_index = {}


# =============================================================================
# Progress Tracker
# =============================================================================

class ProgressTracker:
    """
    Thread-safe progress tracking with display formatting.
    
    Tracks progress for three phases: discovery, coarse, refine.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._phases: Dict[str, Dict[str, int]] = {
            'discovery': {'processed': 0, 'total': 0},
            'coarse': {'processed': 0, 'total': 0},
            'refine': {'processed': 0, 'total': 0}
        }
        self._active_phase: Optional[str] = None
    
    def set_active(self, phase: Optional[str]) -> None:
        """Set the currently active phase."""
        with self._lock:
            self._active_phase = phase
    
    def set_total(self, phase: str, total: int) -> None:
        """Set the total count for a phase."""
        with self._lock:
            self._phases[phase]['total'] = total
            self._phases[phase]['processed'] = 0
    
    def increment(self, phase: str) -> int:
        """Increment processed count and return new value."""
        with self._lock:
            self._phases[phase]['processed'] += 1
            return self._phases[phase]['processed']
    
    def get_progress(self, phase: str) -> tuple:
        """Get (processed, total) for a phase."""
        with self._lock:
            p = self._phases[phase]
            return p['processed'], p['total']
    
    def get_display(self) -> str:
        """Get formatted progress display string."""
        acquired = self._lock.acquire(timeout=0.01)
        if not acquired:
            return "indexing..."
        try:
            phase = self._active_phase
            if phase is None:
                return ""
            
            p = self._phases[phase]
            processed, total = p['processed'], p['total']
            
            if phase == 'discovery':
                if total > 0:
                    return f"discovering files {processed}/{total}"
                return "discovering files..."
            elif phase == 'coarse':
                return f"indexing {processed}/{total}"
            elif phase == 'refine':
                return f"refining {processed}/{total}"
            return ""
        finally:
            self._lock.release()
    
    def is_active(self) -> bool:
        """Check if any phase is active."""
        with self._lock:
            return self._active_phase is not None


# =============================================================================
# Initialization Coordinator
# =============================================================================

# Exception types that indicate ChromaDB corruption
_CORRUPTION_INDICATORS = (
    "database disk image is malformed",
    "file is not a database",
    "no such table",
    "database is locked",
    "unable to open database",
    "corrupt",
    "OperationalError",
    "DatabaseError",
    "IntegrityError",
)


def _is_corruption_error(error: Exception) -> bool:
    """
    Check if an exception indicates ChromaDB corruption.
    
    Args:
        error: The exception to check
        
    Returns:
        True if the error looks like DB corruption
    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    
    # Check error message for corruption indicators
    for indicator in _CORRUPTION_INDICATORS:
        if indicator.lower() in error_str:
            return True
    
    # Check exception type name
    if error_type in ("OperationalError", "DatabaseError", "IntegrityError"):
        return True
    
    # SQLite errors often indicate corruption
    if "sqlite" in error_str or "sqlite" in error_type.lower():
        return True
    
    return False


class InitializationCoordinator:
    """
    Coordinates vector DB initialization.
    
    Handles initialization state, locking, retry logic, and corruption recovery.
    """
    
    def __init__(self, config: IndexConfig):
        self.config = config
        self.collection: Optional[Any] = None
        self._is_initialized = False
        self._in_progress = False
        self._recovery_attempted = False
        self._lock = threading.Lock()
    
    @property
    def is_initialized(self) -> bool:
        """Check if initialization is complete."""
        return self._is_initialized
    
    @property
    def in_progress(self) -> bool:
        """Check if initialization is in progress."""
        return self._in_progress
    
    @property
    def is_ready(self) -> bool:
        """Check if the collection is ready for use."""
        return self._is_initialized and self.collection is not None
    
    def initialize(self, blocking: bool = True) -> bool:
        """
        Initialize the ChromaDB collection.
        
        Args:
            blocking: If True, wait for lock. If False, return immediately
                      if lock is held.
                      
        Returns:
            True on success or if already initialized.
        """
        from aye.model import vector_db, onnx_manager
        
        # Fast path: already initialized
        if self._is_initialized:
            return self.collection is not None
        
        # Try to acquire lock
        if blocking:
            acquired = self._lock.acquire(timeout=0.1)
        else:
            acquired = self._lock.acquire(blocking=False)
        
        if not acquired:
            return self._is_initialized and self.collection is not None
        
        try:
            if self._is_initialized:
                return self.collection is not None
            
            self._in_progress = True
            model_status = onnx_manager.get_model_status()
            
            if model_status == "READY":
                return self._do_initialize()
            elif model_status == "FAILED":
                self._is_initialized = True
                self.collection = None
                return False
            
            return False
        finally:
            self._in_progress = False
            self._lock.release()
    
    def _do_initialize(self) -> bool:
        """Perform the actual initialization with corruption recovery."""
        from aye.model import vector_db
        
        try:
            self.collection = vector_db.initialize_index(self.config.root_path)
            self._is_initialized = True
            if self.config.debug:
                rprint("[bold cyan]Code lookup is now active.[/]")
            return True
        except Exception as e:
            # Check if this looks like corruption
            if _is_corruption_error(e) and not self._recovery_attempted:
                rprint(f"[yellow]Detected possible index corruption: {e}[/]")
                return self._attempt_recovery()
            
            # Not corruption or recovery already attempted
            rprint(f"[red]Failed to initialize local code search: {e}[/red]")
            self._is_initialized = True
            self.collection = None
            return False
    
    def _attempt_recovery(self) -> bool:
        """
        Attempt to recover from ChromaDB corruption.
        
        Strategy:
        1. Quarantine the corrupt chroma_db directory
        2. Invalidate the hash index (so files get re-indexed)
        3. Retry initialization with fresh DB
        
        Returns:
            True if recovery succeeded, False otherwise
        """
        from aye.model import vector_db
        
        self._recovery_attempted = True
        timestamp = int(time.time())
        
        rprint("[yellow]Attempting automatic recovery...[/]")
        
        # Step 1: Quarantine corrupt ChromaDB
        chroma_path = self.config.chroma_db_path
        if chroma_path.exists():
            corrupt_path = chroma_path.parent / f"chroma_db.corrupt.{timestamp}"
            try:
                shutil.move(str(chroma_path), str(corrupt_path))
                rprint(f"[cyan]Quarantined corrupt DB to: {corrupt_path.name}[/]")
            except Exception as move_err:
                # If move fails, try to delete
                rprint(f"[yellow]Could not quarantine DB ({move_err}), attempting delete...[/]")
                try:
                    shutil.rmtree(str(chroma_path), ignore_errors=True)
                except Exception:
                    pass
        
        # Step 2: Invalidate hash index (so all files get re-indexed)
        hash_index_path = self.config.hash_index_path
        if hash_index_path.exists():
            corrupt_index_path = hash_index_path.parent / f"file_index.json.corrupt.{timestamp}"
            try:
                shutil.move(str(hash_index_path), str(corrupt_index_path))
                rprint(f"[cyan]Quarantined hash index to: {corrupt_index_path.name}[/]")
            except Exception:
                # If move fails, just delete
                try:
                    hash_index_path.unlink(missing_ok=True)
                except Exception:
                    pass
        
        # Step 3: Retry initialization
        try:
            rprint("[cyan]Reinitializing code search index...[/]")
            self.collection = vector_db.initialize_index(self.config.root_path)
            self._is_initialized = True
            rprint("[green]Recovery successful! Index will be rebuilt in the background.[/]")
            return True
        except Exception as retry_err:
            rprint(f"[red]Recovery failed: {retry_err}[/red]")
            rprint("[yellow]Code search will be disabled for this session.[/]")
            rprint("[yellow]You can manually delete .aye/chroma_db and restart to try again.[/]")
            self._is_initialized = True
            self.collection = None
            return False


# =============================================================================
# Error Handler
# =============================================================================

class ErrorHandler:
    """
    Centralized error handling with context.
    
    Respects verbose/debug settings for output control.
    """
    
    def __init__(self, verbose: bool = False, debug: bool = False):
        self.verbose = verbose
        self.debug = debug
    
    def handle(self, error: Exception, context: str = "") -> None:
        """Handle an error with optional context."""
        if self.debug:
            if context:
                rprint(f"[red]Error in {context}: {error}[/red]")
            else:
                rprint(f"[red]Error: {error}[/red]")
    
    def handle_silent(self, error: Exception, context: str = "") -> None:
        """Handle an error silently (only log in debug mode)."""
        if self.debug:
            self.handle(error, context)
    
    def warn(self, message: str) -> None:
        """Display a warning message."""
        if self.verbose or self.debug:
            rprint(f"[yellow]{message}[/yellow]")
    
    def info(self, message: str) -> None:
        """Display an info message (debug only)."""
        if self.debug:
            rprint(f"[cyan]{message}[/cyan]")

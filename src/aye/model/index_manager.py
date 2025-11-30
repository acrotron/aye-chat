"""Index Manager for project file indexing and vector search.

This module provides the IndexManager class which manages:
- File hash index for tracking changes
- Vector database for semantic code search
- Background indexing with coarse and refined passes
"""

import os
import time
import threading
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from rich import print as rprint
from rich.prompt import Confirm

from aye.model.models import VectorIndexResult
from aye.model.source_collector import get_project_files, get_project_files_with_limit
from aye.model import vector_db, onnx_manager

from .index_manager_utils import (
    DaemonThreadPoolExecutor,
    MAX_WORKERS,
    set_low_priority,
    set_discovery_thread_low_priority,
    register_manager,
)
from .index_manager_file_ops import (
    FileCategorizer,
    IndexPersistence,
    get_deleted_files,
)


class IndexManager:
    """
    Manages the file hash index and vector database for a project.
    
    Uses a two-phase progressive indexing strategy:
    1. Coarse Indexing: A fast, file-per-chunk pass for immediate usability
    2. Refinement: A background process that replaces coarse chunks with 
       fine-grained, AST-based chunks
    """
    
    # Save progress after every N files
    SAVE_INTERVAL = 20
    
    def __init__(
        self, 
        root_path: Path, 
        file_mask: str, 
        verbose: bool = False, 
        debug: bool = False
    ):
        self.root_path = root_path
        self.file_mask = file_mask
        self.verbose = verbose
        self.debug = debug
        
        # Paths
        self.index_dir = root_path / ".aye"
        self.hash_index_path = self.index_dir / "file_index.json"
        
        # Vector DB
        self.collection: Optional[Any] = None
        self._is_initialized = False
        self._initialization_lock = threading.Lock()
        self._initialization_in_progress = False
        
        # Work queues
        self._files_to_coarse_index: List[str] = []
        self._files_to_refine: List[str] = []
        
        # Index state
        self._target_index: Dict[str, Any] = {}
        self._current_index_on_disk: Dict[str, Any] = {}
        
        # Progress tracking
        self._coarse_total: int = 0
        self._coarse_processed: int = 0
        self._refine_total: int = 0
        self._refine_processed: int = 0
        
        # Status flags
        self._is_indexing: bool = False
        self._is_refining: bool = False
        self._is_discovering: bool = False
        self._discovery_total: int = 0
        self._discovery_processed: int = 0
        
        # Locks
        self._progress_lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._shutdown_requested = False
        
        # Helper objects
        self._persistence = IndexPersistence(self.index_dir, self.hash_index_path)
        self._categorizer = FileCategorizer(root_path, self._should_stop)
        
        # Register for cleanup on exit
        register_manager(self)

    # =========================================================================
    # Shutdown and Lifecycle
    # =========================================================================
    
    def shutdown(self) -> None:
        """
        Request shutdown of background indexing and save pending progress.
        
        Should be called before application exits for clean shutdown.
        """
        with self._shutdown_lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True
        
        # Save pending progress
        try:
            self._save_progress()
        except Exception:
            pass
        
        # Wait briefly for background threads to notice shutdown
        deadline = time.time() + 0.5
        while self._is_active() and time.time() < deadline:
            time.sleep(0.05)
    
    def _should_stop(self) -> bool:
        """Check if shutdown has been requested."""
        with self._shutdown_lock:
            return self._shutdown_requested
    
    def _is_active(self) -> bool:
        """Check if any background work is in progress."""
        return self._is_indexing or self._is_refining or self._is_discovering

    # =========================================================================
    # Initialization
    # =========================================================================
    
    def _lazy_initialize(self, blocking: bool = True) -> bool:
        """
        Initialize the ChromaDB collection if not already done.
        
        Args:
            blocking: If True, wait for lock. If False, return immediately 
                      if lock is held.
                      
        Returns:
            True on success or if already initialized.
        """
        if self._should_stop():
            return False
        
        # Fast path: already initialized
        if self._is_initialized:
            return self.collection is not None
        
        # Try to acquire lock
        if blocking:
            acquired = self._initialization_lock.acquire(timeout=0.1)
        else:
            acquired = self._initialization_lock.acquire(blocking=False)
        
        if not acquired:
            return self._is_initialized and self.collection is not None
        
        try:
            if self._is_initialized:
                return self.collection is not None
            
            self._initialization_in_progress = True
            model_status = onnx_manager.get_model_status()
            
            if model_status == "READY":
                try:
                    self.collection = vector_db.initialize_index(self.root_path)
                    self._is_initialized = True
                    if self.debug:
                        rprint("[bold cyan]Code lookup is now active.[/]")
                    return True
                except Exception as e:
                    rprint(f"[red]Failed to initialize local code search: {e}[/red]")
                    self._is_initialized = True
                    self.collection = None
                    return False
            
            elif model_status == "FAILED":
                self._is_initialized = True
                self.collection = None
                return False

            return False
        finally:
            self._initialization_in_progress = False
            self._initialization_lock.release()

    # =========================================================================
    # Synchronous Preparation
    # =========================================================================
    
    def prepare_sync(self, verbose: bool = False) -> None:
        """
        Perform a fast scan for file changes and prepare indexing queues.
        
        If more than 1000 files are found, asks for user confirmation and 
        switches to async discovery.
        """
        if self._should_stop():
            return
        
        # Try non-blocking initialization
        if not self._is_initialized:
            self._lazy_initialize(blocking=False)
        
        if not self._is_initialized:
            if verbose and onnx_manager.get_model_status() == "DOWNLOADING":
                rprint("[yellow]Code lookup is initializing (downloading models)... "
                       "Project scan will begin shortly.[/]")

        old_index = self._persistence.load_index()
        
        # Try to enumerate up to 1000 files synchronously
        current_files, limit_hit = get_project_files_with_limit(
            root_dir=str(self.root_path), 
            file_mask=self.file_mask,
            limit=1000
        )
        
        if limit_hit:
            self._handle_large_project(old_index)
            return
        
        # Small project: process synchronously
        self._process_small_project(current_files, old_index)
    
    def _handle_large_project(self, old_index: Dict[str, Any]) -> None:
        """Handle projects with more than 1000 files."""
        rprint("\n[bold yellow]⚠️  Whoa! 1000+ files discovered...[/]")
        rprint("[yellow]Is this really how large your project is, or did some "
               "libraries get included by accident?[/]")
        rprint("[yellow]You can use .gitignore or .ayeignore to exclude "
               "subfolders and files.[/]\n")
        
        if not Confirm.ask("[bold]Do you want to continue with indexing?[/bold]", 
                          default=False):
            rprint("[cyan]Indexing cancelled. Please update your ignore files "
                   "and restart aye chat.[/]")
            return
        
        rprint("[cyan]Starting async file discovery... The chat will be "
               "available immediately.\n")
        
        with self._progress_lock:
            self._current_index_on_disk = old_index.copy()
        
        # Start async discovery
        discovery_thread = threading.Thread(
            target=self._async_file_discovery,
            args=(old_index,),
            daemon=True
        )
        discovery_thread.start()
    
    def _process_small_project(
        self, 
        current_files: List[Path], 
        old_index: Dict[str, Any]
    ) -> None:
        """Process a small project (< 1000 files) synchronously."""
        files_to_coarse, files_to_refine, new_index = self._categorizer.categorize_files(
            current_files, old_index
        )
        
        current_paths_str = {
            p.relative_to(self.root_path).as_posix() for p in current_files
        }
        
        # Handle deleted files
        if self.collection:
            deleted = get_deleted_files(current_paths_str, old_index)
            if deleted:
                if self.debug:
                    rprint(f"  [red]Deleted:[/] {len(deleted)} file(s) from index.")
                vector_db.delete_from_index(self.collection, deleted)
        
        # Update state
        if files_to_coarse:
            if self.debug:
                rprint(f"  [green]Found:[/] {len(files_to_coarse)} new or modified "
                       "file(s) for initial indexing.")
            self._files_to_coarse_index = files_to_coarse
            self._coarse_total = len(files_to_coarse)
            self._coarse_processed = 0
        
        if files_to_refine:
            if self.debug:
                rprint(f"  [cyan]Found:[/] {len(files_to_refine)} file(s) to refine "
                       "for better search quality.")
            self._files_to_refine = files_to_refine
        
        if not files_to_coarse and not files_to_refine:
            if self.debug:
                rprint("[green]Project index is up-to-date.[/]")

        self._target_index = new_index
        self._current_index_on_disk = old_index.copy()

    # =========================================================================
    # Async File Discovery
    # =========================================================================
    
    def _async_file_discovery(self, old_index: Dict[str, Any]) -> None:
        """
        Asynchronously discover all project files and categorize them.
        
        Runs in a background thread. After completion, starts background 
        indexing if there's work to do.
        """
        set_discovery_thread_low_priority()
        
        try:
            with self._progress_lock:
                self._is_discovering = True
                self._discovery_processed = 0
                self._discovery_total = 0
                self._current_index_on_disk = old_index.copy()
            
            if self._should_stop():
                return
            
            current_files = get_project_files(
                root_dir=str(self.root_path), 
                file_mask=self.file_mask
            )
            
            if self._should_stop():
                return
            
            with self._progress_lock:
                self._discovery_total = len(current_files)
            
            files_to_coarse, files_to_refine, new_index = self._categorizer.categorize_files(
                current_files, old_index
            )
            
            if self._should_stop():
                return
            
            # Handle deleted files
            current_paths_str = {
                p.relative_to(self.root_path).as_posix() for p in current_files
            }
            
            if self._is_initialized and self.collection:
                deleted = get_deleted_files(current_paths_str, old_index)
                if deleted:
                    vector_db.delete_from_index(self.collection, deleted)
            
            # Update work queues
            with self._progress_lock:
                self._files_to_coarse_index = files_to_coarse
                self._files_to_refine = files_to_refine
                self._target_index = new_index
                self._coarse_total = len(files_to_coarse)
                self._coarse_processed = 0
                self._is_discovering = False
            
            self._log_discovery_results(files_to_coarse, files_to_refine)
            
            # Start background indexing
            if self.has_work() and not self._should_stop():
                indexing_thread = threading.Thread(
                    target=self.run_sync_in_background, 
                    daemon=True
                )
                indexing_thread.start()
                    
        except Exception as e:
            with self._progress_lock:
                self._is_discovering = False
            if self.verbose:
                rprint(f"[red]Error during async file discovery: {e}[/red]")
    
    def _log_discovery_results(
        self, 
        files_to_coarse: List[str], 
        files_to_refine: List[str]
    ) -> None:
        """Log the results of file discovery."""
        if not self.debug:
            return
            
        if files_to_coarse:
            rprint(f"  [green]Found:[/] {len(files_to_coarse)} new or modified "
                   "file(s) for initial indexing.")
        if files_to_refine:
            rprint(f"  [cyan]Found:[/] {len(files_to_refine)} file(s) to refine "
                   "for better search quality.")
        if not files_to_coarse and not files_to_refine:
            rprint("[green]Project index is up-to-date.[/]")

    # =========================================================================
    # Background Indexing
    # =========================================================================
    
    def run_sync_in_background(self) -> None:
        """
        Wait for code search to be ready, then run indexing and refinement.
        
        This method blocks the background thread but not the main thread.
        """
        # Wait for initialization
        while not self._is_initialized and not self._should_stop():
            if self._lazy_initialize(blocking=True):
                break
            if onnx_manager.get_model_status() == "FAILED":
                return
            time.sleep(1)

        if self._should_stop() or not self.collection:
            return

        # Wait for discovery to complete
        while self._is_discovering and not self._should_stop():
            time.sleep(0.5)

        if self._should_stop() or not self.has_work():
            return

        os.environ['TOKENIZERS_PARALLELISM'] = 'false'

        try:
            # Phase 1: Coarse indexing
            if self._files_to_coarse_index and not self._should_stop():
                self._is_indexing = True
                self._run_work_phase(
                    self._process_one_file_coarse, 
                    self._files_to_coarse_index, 
                    is_refinement=False
                )
                self._is_indexing = False

            if self._should_stop():
                return

            # Phase 2: Refinement
            all_files_to_refine = sorted(list(set(
                self._files_to_refine + self._files_to_coarse_index
            )))

            if all_files_to_refine and not self._should_stop():
                self._is_refining = True
                self._refine_total = len(all_files_to_refine)
                self._refine_processed = 0
                self._run_work_phase(
                    self._process_one_file_refine, 
                    all_files_to_refine, 
                    is_refinement=True
                )
                self._is_refining = False
        finally:
            self._save_progress()
            self._is_indexing = self._is_refining = False
            self._files_to_coarse_index = self._files_to_refine = []
            self._target_index = {}

    def _run_work_phase(
        self, 
        worker_func: Callable, 
        file_list: List[str], 
        is_refinement: bool
    ) -> None:
        """Run a work phase (coarse or refinement) with parallel processing."""
        files_to_process = self._filter_files_for_processing(file_list, is_refinement)
        
        if not files_to_process:
            return
        
        processed_since_last_save = 0
        
        with DaemonThreadPoolExecutor(
            max_workers=MAX_WORKERS, 
            initializer=set_low_priority
        ) as executor:
            future_to_path = {
                executor.submit(worker_func, path): path 
                for path in files_to_process
            }

            for future in concurrent.futures.as_completed(future_to_path):
                if self._should_stop():
                    for f in future_to_path:
                        f.cancel()
                    break
                    
                path = future_to_path[future]
                try:
                    if future.result():
                        self._update_index_after_processing(path, is_refinement)
                        processed_since_last_save += 1
                except Exception:
                    pass

                if processed_since_last_save >= self.SAVE_INTERVAL:
                    self._save_progress()
                    processed_since_last_save = 0

        if processed_since_last_save > 0:
            self._save_progress()
    
    def _filter_files_for_processing(
        self, 
        file_list: List[str], 
        is_refinement: bool
    ) -> List[str]:
        """Filter out files that should be skipped for resume support."""
        files_to_process = []
        
        with self._progress_lock:
            for path in file_list:
                if self._should_stop():
                    break
                    
                if not is_refinement:
                    # Skip files already indexed
                    if path in self._current_index_on_disk:
                        self._coarse_processed += 1
                        continue
                else:
                    # Skip files already refined
                    current_meta = self._current_index_on_disk.get(path)
                    if current_meta and current_meta.get('refined', False):
                        self._refine_processed += 1
                        continue
                        
                files_to_process.append(path)
        
        return files_to_process
    
    def _update_index_after_processing(
        self, 
        path: str, 
        is_refinement: bool
    ) -> None:
        """Update the index after successfully processing a file."""
        with self._progress_lock:
            if is_refinement:
                if path in self._current_index_on_disk:
                    self._current_index_on_disk[path]['refined'] = True
                else:
                    final_meta = self._target_index.get(path)
                    if final_meta:
                        final_meta = final_meta.copy()
                        final_meta['refined'] = True
                        self._current_index_on_disk[path] = final_meta
            else:
                final_meta = self._target_index.get(path)
                if final_meta:
                    self._current_index_on_disk[path] = final_meta

    # =========================================================================
    # File Processing Workers
    # =========================================================================
    
    def _process_one_file_coarse(self, rel_path_str: str) -> Optional[str]:
        """Process a single file for coarse indexing."""
        if self._should_stop():
            return None
        try:
            content = (self.root_path / rel_path_str).read_text(encoding="utf-8")
            if self.collection:
                vector_db.update_index_coarse(self.collection, {rel_path_str: content})
            return rel_path_str
        except Exception:
            return None
        finally:
            with self._progress_lock:
                self._coarse_processed += 1

    def _process_one_file_refine(self, rel_path_str: str) -> Optional[str]:
        """Process a single file for refinement."""
        if self._should_stop():
            return None
        try:
            content = (self.root_path / rel_path_str).read_text(encoding="utf-8")
            if self.collection:
                vector_db.refine_file_in_index(self.collection, rel_path_str, content)
            return rel_path_str
        except Exception:
            return None
        finally:
            with self._progress_lock:
                self._refine_processed += 1

    # =========================================================================
    # Progress and Persistence
    # =========================================================================
    
    def _save_progress(self) -> None:
        """Save current index state to disk."""
        with self._save_lock:
            with self._progress_lock:
                index_to_save = self._current_index_on_disk.copy()
            self._persistence.save_index(index_to_save)

    def has_work(self) -> bool:
        """Check if there's indexing work to do."""
        return bool(self._files_to_coarse_index or self._files_to_refine)

    def is_indexing(self) -> bool:
        """Check if indexing is in progress (non-blocking)."""
        return self._is_indexing or self._is_refining or self._is_discovering

    def get_progress_display(self) -> str:
        """Get progress display string."""
        acquired = self._progress_lock.acquire(timeout=0.01)
        if not acquired:
            return "indexing..."
        try:
            if self._is_discovering:
                if self._discovery_total > 0:
                    return f"discovering files {self._discovery_processed}/{self._discovery_total}"
                return "discovering files..."
            if self._is_indexing:
                return f"indexing {self._coarse_processed}/{self._coarse_total}"
            if self._is_refining:
                return f"refining {self._refine_processed}/{self._refine_total}"
            return ""
        finally:
            self._progress_lock.release()

    # =========================================================================
    # Query Interface
    # =========================================================================
    
    def query(
        self, 
        query_text: str, 
        n_results: int = 10, 
        min_relevance: float = 0.0
    ) -> List[VectorIndexResult]:
        """
        Query the vector index (non-blocking).
        
        If the index is not yet initialized or initialization is in progress,
        returns empty results immediately to avoid blocking the main thread.
        """
        if self._should_stop():
            return []
        
        # Fast path: if initialization is in progress, return empty immediately
        # This avoids lock contention when background indexing is running
        if self._initialization_in_progress:
            if self.debug:
                rprint("[yellow]Index initialization in progress, returning empty context.[/]")
            return []
        
        if not self._is_initialized:
            if not self._lazy_initialize(blocking=False):
                if self.debug:
                    rprint("[yellow]Index not ready yet, returning empty context.[/]")
                return []
        
        if not self.collection:
            return []
            
        return vector_db.query_index(
            collection=self.collection,
            query_text=query_text,
            n_results=n_results,
            min_relevance=min_relevance
        )

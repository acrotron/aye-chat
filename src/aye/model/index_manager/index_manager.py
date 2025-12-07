"""Index Manager for project file indexing and vector search.

This module provides the IndexManager class which manages:
- File hash index for tracking changes
- Vector database for semantic code search
- Background indexing with coarse and refined passes
"""

import os
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional

from rich import print as rprint
from rich.prompt import Confirm

from aye.model.models import VectorIndexResult
from aye.model.source_collector import get_project_files, get_project_files_with_limit
from aye.model import vector_db, onnx_manager
from aye.model.config import SMALL_PROJECT_FILE_LIMIT

from .index_manager_utils import register_manager, set_discovery_thread_low_priority
from .index_manager_file_ops import FileCategorizer, IndexPersistence, get_deleted_files
from .index_manager_state import (
    IndexConfig,
    IndexingState,
    ProgressTracker,
    InitializationCoordinator,
    ErrorHandler,
)
from .index_manager_executor import PhaseExecutor


class IndexManager:
    """
    Manages the file hash index and vector database for a project.
    
    Uses a two-phase progressive indexing strategy:
    1. Coarse Indexing: A fast, file-per-chunk pass for immediate usability
    2. Refinement: A background process that replaces coarse chunks with
       fine-grained, AST-based chunks
    """
    
    def __init__(
        self,
        root_path: Path,
        file_mask: str,
        verbose: bool = False,
        debug: bool = False
    ):
        # Create config from parameters
        self.config = IndexConfig.from_params(root_path, file_mask, verbose, debug)
        
        # Initialize state objects
        self._state = IndexingState()
        self._progress = ProgressTracker()
        self._error_handler = ErrorHandler(verbose, debug)
        self._init_coordinator = InitializationCoordinator(self.config)
        
        # Locks
        self._state_lock = threading.Lock()
        self._save_lock = threading.Lock()
        
        # Helper objects
        self._persistence = IndexPersistence(
            self.config.index_dir,
            self.config.hash_index_path
        )
        self._categorizer = FileCategorizer(
            self.config.root_path,
            self._should_stop
        )
        
        # Register for cleanup on exit
        register_manager(self)
    
    # =========================================================================
    # Properties for backward compatibility
    # =========================================================================
    
    @property
    def is_discovering(self) -> bool:
        return self._state.is_discovering
    
    @property
    def root_path(self) -> Path:
        return self.config.root_path
    
    @property
    def file_mask(self) -> str:
        return self.config.file_mask
    
    @property
    def verbose(self) -> bool:
        return self.config.verbose
    
    @property
    def debug(self) -> bool:
        return self.config.debug
    
    @property
    def collection(self) -> Optional[Any]:
        return self._init_coordinator.collection
    
    # =========================================================================
    # Shutdown and Lifecycle
    # =========================================================================
    
    def shutdown(self) -> None:
        """Request shutdown of background indexing and save pending progress."""
        with self._state_lock:
            if self._state.shutdown_requested:
                return
            self._state.shutdown_requested = True
        
        self._save_progress()
        self._wait_for_background_work(timeout=0.5)
    
    def _wait_for_background_work(self, timeout: float) -> None:
        """Wait for background work to complete."""
        deadline = time.time() + timeout
        while self._state.is_active() and time.time() < deadline:
            time.sleep(0.05)
    
    def _should_stop(self) -> bool:
        """Check if shutdown has been requested."""
        with self._state_lock:
            return self._state.shutdown_requested
    
    # =========================================================================
    # Synchronous Preparation
    # =========================================================================
    
    def prepare_sync(self, verbose: bool = False) -> None:
        """
        Perform a fast scan for file changes and prepare indexing queues.
        
        If more than 1000 files are found, asks for user confirmation and
        switches to async discovery.
        
        Skips all processing if the root path is the user's home directory.
        """
        # Skip indexing in home directory to avoid scanning large/irrelevant areas
        if self.config.root_path == Path.home():
            if verbose:
                rprint("[yellow]Skipping indexing in home directory.[/]")
            return
        
        if self._should_stop():
            return
        
        self._try_initialize(verbose)
        old_index = self._persistence.load_index()
        
        current_files, limit_hit = get_project_files_with_limit(
            root_dir=str(self.config.root_path),
            file_mask=self.config.file_mask,
            limit=SMALL_PROJECT_FILE_LIMIT
        )
        
        if limit_hit:
            self._handle_large_project(old_index)
        else:
            self._process_small_project(current_files, old_index)
    
    def _try_initialize(self, verbose: bool) -> None:
        """Try to initialize the vector DB."""
        if not self._init_coordinator.is_initialized:
            self._init_coordinator.initialize(blocking=False)
        
        if not self._init_coordinator.is_initialized:
            if verbose and onnx_manager.get_model_status() == "DOWNLOADING":
                rprint("[yellow]Code lookup is initializing (downloading models)... "
                       "Project scan will begin shortly.[/]")
    
    def _handle_large_project(self, old_index: Dict[str, Any]) -> None:
        """Handle projects with more than 1000 files."""
        rprint("\n[bold yellow]⚠️  Whoa! 200+ files discovered...[/]")
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
        
        with self._state_lock:
            self._state.current_index_on_disk = old_index.copy()
        
        self._start_async_discovery(old_index)
    
    def _start_async_discovery(self, old_index: Dict[str, Any]) -> None:
        """Start async file discovery in a background thread."""
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
            p.relative_to(self.config.root_path).as_posix() for p in current_files
        }
        
        self._handle_deleted_files(current_paths_str, old_index)
        self._update_state_after_categorization(
            files_to_coarse, files_to_refine, new_index, old_index
        )
    
    def _handle_deleted_files(
        self,
        current_paths: set,
        old_index: Dict[str, Any]
    ) -> None:
        """Handle files that have been deleted."""
        if not self._init_coordinator.collection:
            return
        
        deleted = get_deleted_files(current_paths, old_index)
        if deleted:
            self._error_handler.info(f"Deleted: {len(deleted)} file(s) from index.")
            vector_db.delete_from_index(self._init_coordinator.collection, deleted)
    
    def _update_state_after_categorization(
        self,
        files_to_coarse: List[str],
        files_to_refine: List[str],
        new_index: Dict[str, Any],
        old_index: Dict[str, Any]
    ) -> None:
        """Update state after file categorization."""
        with self._state_lock:
            if files_to_coarse:
                self._error_handler.info(
                    f"Found: {len(files_to_coarse)} new or modified file(s) for initial indexing."
                )
                self._state.files_to_coarse_index = files_to_coarse
                self._state.reset_coarse_progress(len(files_to_coarse))
            
            if files_to_refine:
                self._error_handler.info(
                    f"Found: {len(files_to_refine)} file(s) to refine for better search quality."
                )
                self._state.files_to_refine = files_to_refine
            
            if not files_to_coarse and not files_to_refine:
                self._error_handler.info("Project index is up-to-date.")
            
            self._state.target_index = new_index
            self._state.current_index_on_disk = old_index.copy()
    
    # =========================================================================
    # Async File Discovery
    # =========================================================================
    
    def _async_file_discovery(self, old_index: Dict[str, Any]) -> None:
        """Asynchronously discover all project files and categorize them."""
        set_discovery_thread_low_priority()
        
        try:
            self._prepare_discovery(old_index)
            
            if self._should_stop():
                return
            
            current_files, new_index = self._discover_and_categorize_files(old_index)
            
            if self._should_stop() or current_files is None:
                return
            
            self._finalize_discovery(current_files, old_index, new_index)
            
        except Exception as e:
            self._error_handler.handle(e, "async file discovery")
        finally:
            with self._state_lock:
                self._state.is_discovering = False
            self._progress.set_active(None)
    
    def _prepare_discovery(self, old_index: Dict[str, Any]) -> None:
        """Prepare state for discovery."""
        with self._state_lock:
            self._state.is_discovering = True
            self._state.increment_generation()
            self._state.reset_discovery_progress()
            self._state.current_index_on_disk = old_index.copy()
        
        self._progress.set_active('discovery')
    
    def _discover_and_categorize_files(
        self,
        old_index: Dict[str, Any]
    ) -> tuple:
        """Discover and categorize project files."""
        current_files = get_project_files(
            root_dir=str(self.config.root_path),
            file_mask=self.config.file_mask
        )
        
        if self._should_stop():
            return None, None
        
        self._progress.set_total('discovery', len(current_files))
        
        files_to_coarse, files_to_refine, new_index = self._categorizer.categorize_files(
            current_files, old_index
        )
        
        if self._should_stop():
            return None, None
        
        # Update work queues
        with self._state_lock:
            self._state.files_to_coarse_index = files_to_coarse
            self._state.files_to_refine = files_to_refine
            self._state.target_index = new_index
            self._state.reset_coarse_progress(len(files_to_coarse))
        
        return current_files, new_index
    
    def _finalize_discovery(
        self,
        current_files: List[Path],
        old_index: Dict[str, Any],
        new_index: Dict[str, Any]
    ) -> None:
        """Finalize discovery and start indexing if needed."""
        current_paths_str = {
            p.relative_to(self.config.root_path).as_posix() for p in current_files
        }
        
        if self._init_coordinator.is_ready:
            self._handle_deleted_files(current_paths_str, old_index)
        
        self._log_discovery_results()
        self._start_indexing_if_needed()
    
    def _log_discovery_results(self) -> None:
        """Log the results of file discovery."""
        with self._state_lock:
            files_to_coarse = self._state.files_to_coarse_index
            files_to_refine = self._state.files_to_refine
        
        if files_to_coarse:
            self._error_handler.info(
                f"Found: {len(files_to_coarse)} new or modified file(s) for initial indexing."
            )
        if files_to_refine:
            self._error_handler.info(
                f"Found: {len(files_to_refine)} file(s) to refine for better search quality."
            )
        if not files_to_coarse and not files_to_refine:
            self._error_handler.info("Project index is up-to-date.")
    
    def _start_indexing_if_needed(self) -> None:
        """Start background indexing if there's work to do."""
        if self._state.has_work() and not self._should_stop():
            indexing_thread = threading.Thread(
                target=self.run_sync_in_background,
                daemon=True
            )
            indexing_thread.start()
    
    # =========================================================================
    # Background Indexing
    # =========================================================================
    
    def run_sync_in_background(self) -> None:
        """Wait for code search to be ready, then run indexing and refinement."""
        if not self._wait_for_initialization():
            return
        
        if not self._wait_for_discovery():
            return
        
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        
        with self._state_lock:
            current_generation = self._state.generation
        
        try:
            self._execute_coarse_phase(current_generation)
            
            if not self._should_continue_to_refinement(current_generation):
                return
            
            self._execute_refinement_phase(current_generation)
        finally:
            self._finalize_indexing(current_generation)
    
    def _wait_for_initialization(self) -> bool:
        """Wait for vector DB initialization."""
        while not self._init_coordinator.is_initialized and not self._should_stop():
            if self._init_coordinator.initialize(blocking=True):
                break
            if onnx_manager.get_model_status() == "FAILED":
                return False
            time.sleep(1)
        
        return not self._should_stop() and self._init_coordinator.collection is not None
    
    def _wait_for_discovery(self) -> bool:
        """Wait for file discovery to complete."""
        while self._state.is_discovering and not self._should_stop():
            time.sleep(0.5)
        
        return not self._should_stop() and self._state.has_work()
    
    def _execute_coarse_phase(self, generation: int) -> None:
        """Execute the coarse indexing phase."""
        with self._state_lock:
            files_to_index = self._state.files_to_coarse_index.copy()
        
        if not files_to_index or self._should_stop():
            return
        
        self._state.is_indexing = True
        
        executor = self._create_phase_executor()
        executor.execute_coarse_phase(files_to_index, generation)
        
        self._state.is_indexing = False
    
    def _should_continue_to_refinement(self, generation: int) -> bool:
        """Check if we should continue to the refinement phase."""
        with self._state_lock:
            if self._state.generation != generation:
                return False
        return not self._should_stop()
    
    def _execute_refinement_phase(self, generation: int) -> None:
        """Execute the refinement phase."""
        with self._state_lock:
            all_files_to_refine = sorted(list(set(
                self._state.files_to_refine + self._state.files_to_coarse_index
            )))
        
        if not all_files_to_refine or self._should_stop():
            return
        
        self._state.is_refining = True
        self._state.reset_refine_progress(len(all_files_to_refine))
        
        executor = self._create_phase_executor()
        executor.execute_refine_phase(all_files_to_refine, generation)
        
        self._state.is_refining = False
    
    def _create_phase_executor(self) -> PhaseExecutor:
        """Create a PhaseExecutor instance."""
        return PhaseExecutor(
            config=self.config,
            state=self._state,
            progress=self._progress,
            error_handler=self._error_handler,
            collection=self._init_coordinator.collection,
            should_stop=self._should_stop,
            save_callback=self._save_progress
        )
    
    def _finalize_indexing(self, generation: int) -> None:
        """Finalize indexing and clean up."""
        self._save_progress()
        self._state.is_indexing = False
        self._state.is_refining = False
        
        with self._state_lock:
            if self._state.generation == generation:
                self._state.clear_work_queues()
    
    # =========================================================================
    # Progress and Persistence
    # =========================================================================
    
    def _save_progress(self) -> None:
        """Save current index state to disk."""
        with self._save_lock:
            with self._state_lock:
                index_to_save = self._state.current_index_on_disk.copy()
            self._persistence.save_index(index_to_save)
    
    def has_work(self) -> bool:
        """Check if there's indexing work to do."""
        return self._state.has_work()
    
    def is_indexing(self) -> bool:
        """Check if indexing is in progress (non-blocking)."""
        return self._state.is_active()
    
    def get_progress_display(self) -> str:
        """Get progress display string."""
        return self._progress.get_display()
    
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
        
        if self._init_coordinator.in_progress:
            self._error_handler.info(
                "Index initialization in progress, returning empty context."
            )
            return []
        
        if not self._init_coordinator.is_initialized:
            if not self._init_coordinator.initialize(blocking=False):
                self._error_handler.info(
                    "Index not ready yet, returning empty context."
                )
                return []
        
        if not self._init_coordinator.collection:
            return []
        
        return vector_db.query_index(
            collection=self._init_coordinator.collection,
            query_text=query_text,
            n_results=n_results,
            min_relevance=min_relevance
        )


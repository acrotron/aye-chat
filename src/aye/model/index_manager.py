import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Tuple
import threading
import concurrent.futures
import weakref
import time
import atexit

from rich import print as rprint
from rich.prompt import Confirm

from aye.model.models import VectorIndexResult
from aye.model.source_collector import get_project_files, get_project_files_with_limit
from aye.model import vector_db, onnx_manager

# --- Custom Daemon ThreadPoolExecutor ---
# This is a workaround for the standard ThreadPoolExecutor not creating daemon threads.
# Daemon threads are necessary here so that the background indexing process
# does not block the main application from exiting.
# This implementation is based on the CPython 3.9+ source.
from concurrent.futures.thread import _worker

class DaemonThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    def _adjust_thread_count(self):
        # This method is a copy of the original from Python 3.9+
        # with one change: `t.daemon = True`.
        if self._idle_semaphore.acquire(blocking=False):
            return

        def weak_ref_cb(_, q=self._work_queue):
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = f"{self._thread_name_prefix or self}_{num_threads}"
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weak_ref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
            )
            t.daemon = True  # This is the key change.
            t.start()
            self._threads.add(t)

# --- End Custom Executor ---

def _set_low_priority():
    """
    Set the priority of the current worker process to low to avoid
    interfering with the main UI thread. This is for POSIX-compliant systems.
    """
    if hasattr(os, 'nice'):
        try:
            # A positive value increases the "niceness" and thus lowers the priority.
            os.nice(5)
        except OSError:
            # This can happen if the user doesn't have permission to change priority.
            # It's not critical, so we can ignore it.
            pass

def _set_discovery_thread_low_priority():
    """
    Set the priority of the discovery/categorization thread to low to avoid
    consuming 100% CPU. This is for POSIX-compliant systems.
    """
    if hasattr(os, 'nice'):
        try:
            # A positive value increases the "niceness" and thus lowers the priority.
            os.nice(5)
        except OSError:
            # This can happen if the user doesn't have permission to change priority.
            # It's not critical, so we can ignore it.
            pass

# Determine a reasonable number of workers for background indexing
# to avoid saturating the CPU and making the UI unresponsive.
try:
    # Use half the available cores, with a max of 4, but always at least 1.
    CPU_COUNT = os.cpu_count() or 2
    MAX_WORKERS = min(4, max(1, CPU_COUNT // 2))
except (ImportError, NotImplementedError):
    MAX_WORKERS = 2 # A safe fallback if cpu_count() is not available.


# Global registry of IndexManager instances for cleanup on exit
_active_managers: List['IndexManager'] = []
_cleanup_lock = threading.Lock()


def _cleanup_all_managers():
    """Cleanup function called at exit to properly shut down all managers."""
    with _cleanup_lock:
        for manager in _active_managers:
            try:
                manager.shutdown()
            except Exception:
                pass  # Ignore errors during cleanup


# Register the cleanup function
atexit.register(_cleanup_all_managers)


class IndexManager:
    """
    Manages the file hash index and the vector database for a project.
    This class encapsulates all logic for scanning, indexing, and querying project files.
    It uses a two-phase progressive indexing strategy:
    1. Coarse Indexing: A fast, file-per-chunk pass for immediate usability.
    2. Refinement: A background process that replaces coarse chunks with fine-grained ones.
    """
    def __init__(self, root_path: Path, file_mask: str, verbose: bool = False, debug: bool = False):
        self.root_path = root_path
        self.file_mask = file_mask
        self.verbose = verbose
        self.debug = debug
        self.index_dir = root_path / ".aye"
        self.hash_index_path = self.index_dir / "file_index.json"
        self.SAVE_INTERVAL = 20  # Save progress after every N files
        
        self.collection: Optional[Any] = None
        self._is_initialized = False
        self._initialization_lock = threading.Lock()

        # --- Attributes for background indexing ---
        self._files_to_coarse_index: List[str] = []
        self._files_to_refine: List[str] = []
        
        self._target_index: Dict[str, Any] = {}
        self._current_index_on_disk: Dict[str, Any] = {}
        
        self._coarse_total: int = 0
        self._coarse_processed: int = 0
        self._refine_total: int = 0
        self._refine_processed: int = 0
        
        self._is_indexing: bool = False
        self._is_refining: bool = False
        self._is_discovering: bool = False
        self._discovery_total: int = 0
        self._discovery_processed: int = 0
        self._progress_lock = threading.Lock()
        self._save_lock = threading.Lock()
        
        # Shutdown control
        self._shutdown_requested = False
        self._shutdown_lock = threading.Lock()
        
        # Register this manager for cleanup
        with _cleanup_lock:
            _active_managers.append(self)

    def shutdown(self):
        """
        Request shutdown of background indexing and save any pending progress.
        This should be called before the application exits to ensure clean shutdown.
        """
        with self._shutdown_lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True
        
        # Save any pending progress before shutdown
        try:
            self._save_progress()
        except Exception:
            pass  # Ignore errors during shutdown save
        
        # Give background threads a moment to notice the shutdown flag
        # But don't wait too long - we want a quick exit
        deadline = time.time() + 0.5
        while (self._is_indexing or self._is_refining or self._is_discovering) and time.time() < deadline:
            time.sleep(0.05)

    def _should_stop(self) -> bool:
        """Check if shutdown has been requested."""
        with self._shutdown_lock:
            return self._shutdown_requested

    def _lazy_initialize(self) -> bool:
        """
        Initializes the ChromaDB collection if it hasn't been already and if the
        ONNX model is ready. Returns True on success or if already initialized.
        """
        if self._should_stop():
            return False
            
        with self._initialization_lock:
            if self._is_initialized:
                return self.collection is not None

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
                    self._is_initialized = True  # Mark as "initialized" to avoid retrying
                    self.collection = None
                    return False
            
            elif model_status == "FAILED":
                self._is_initialized = True  # Avoid retrying on failure
                self.collection = None
                return False

            # If status is DOWNLOADING or NOT_DOWNLOADED, we are not ready.
            return False

    def _calculate_hash(self, content: str) -> str:
        """Calculate the SHA-256 hash of a string."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _check_file_status(self, file_path: Path, old_index: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Checks a single file against the index to determine its status.
        
        Returns:
            A tuple of (status, new_metadata).
            Status can be 'unchanged', 'modified', 'needs_refinement', or 'error'.
        """
        rel_path_str = file_path.relative_to(self.root_path).as_posix()
        old_file_meta = old_index.get(rel_path_str)
        
        try:
            stats = file_path.stat()
            mtime = stats.st_mtime
            size = stats.st_size
        except FileNotFoundError:
            return "error", None

        is_new_format = isinstance(old_file_meta, dict)

        # Fast check: if mtime and size are the same, assume unchanged.
        if is_new_format and old_file_meta.get("mtime") == mtime and old_file_meta.get("size") == size:
            if not old_file_meta.get("refined", False):
                return "needs_refinement", old_file_meta
            return "unchanged", old_file_meta

        # Slower check: read file and compare hashes.
        try:
            content = file_path.read_text(encoding="utf-8")
            current_hash = self._calculate_hash(content)
        except (IOError, UnicodeDecodeError):
            return "error", old_file_meta # Keep old meta if read fails

        old_hash = old_file_meta.get("hash") if is_new_format else old_file_meta
        if current_hash == old_hash:
            # Hash matches, but mtime/size didn't. Update meta and check refinement.
            updated_meta = old_file_meta.copy() if is_new_format else {}
            updated_meta.update({"hash": current_hash, "mtime": mtime, "size": size})
            if not updated_meta.get("refined", False):
                return "needs_refinement", updated_meta
            return "unchanged", updated_meta
        
        # If we reach here, the file is modified.
        new_meta = {"hash": current_hash, "mtime": mtime, "size": size, "refined": False}
        return "modified", new_meta

    def _load_old_index(self) -> Dict[str, Any]:
        """Load the existing hash index from disk, or return an empty dict if not found or invalid."""
        old_index: Dict[str, Any] = {}
        if self.hash_index_path.is_file():
            try:
                old_index = json.loads(self.hash_index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                old_index = {}
        return old_index

    def _categorize_files(self, current_files: List[Path], old_index: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Dict[str, Any]]]:
        """Categorize current files into those needing coarse indexing, refinement, or unchanged."""
        files_to_coarse_index: List[str] = []
        files_to_refine: List[str] = []
        new_index: Dict[str, Dict[str, Any]] = {}

        for file_path in current_files:
            if self._should_stop():
                break
                
            rel_path_str = file_path.relative_to(self.root_path).as_posix()
            status, meta = self._check_file_status(file_path, old_index)

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

    def _handle_deleted_files(self, current_file_paths_str: set, old_index: Dict[str, Any]):
        """Handle files that have been deleted by removing them from the vector index."""
        deleted_files = list(set(old_index.keys()) - current_file_paths_str)
        if deleted_files:
            if self.debug:
                rprint(f"  [red]Deleted:[/] {len(deleted_files)} file(s) from index.")
            if self.collection:
                vector_db.delete_from_index(self.collection, deleted_files)
        return deleted_files

    def _warn_large_indexing(self, files_to_coarse_index: List[str], files_to_refine: List[str]) -> bool:
        """Warn the user if the number of files to index is very large and ask for confirmation."""
        total_files_to_index = len(files_to_coarse_index) + len(files_to_refine)
        if total_files_to_index > 500:
            rprint(f"\n[bold yellow]⚠️  Whoa, I found {total_files_to_index:,} files to index![/]")
            rprint("[yellow]Is this really how large your project is, or did some libraries get included by accident?[/]")
            rprint("[yellow]You can use .gitignore or .ayeignore to exclude subfolders and files.[/]\n")
            
            if not Confirm.ask("[bold]Do you want to continue with indexing?[/bold]", default=False):
                rprint("[cyan]Indexing cancelled. Please update your ignore files and restart aye chat.[/]")
                return False
            rprint("[cyan]Proceeding with indexing...\n")
        return True

    def _async_file_discovery(self, old_index: Dict[str, Any]):
        """
        Asynchronously discover all project files and categorize them.
        This runs in a background thread and updates the work queues as files are found.
        After completion, it starts the background indexing thread if there's work to do.
        
        Resume support: Files already present in old_index with matching hashes will be
        skipped, allowing indexing to resume from where it left off.
        """
        # Set low priority for this discovery thread to avoid 100% CPU usage
        _set_discovery_thread_low_priority()
        
        try:
            with self._progress_lock:
                self._is_discovering = True
                self._discovery_processed = 0
                self._discovery_total = 0  # Unknown until complete
                # Initialize _current_index_on_disk from old_index immediately
                # This is crucial for resume support - it tracks what's already indexed
                # Only files from old_index (previously saved to disk) should be here
                self._current_index_on_disk = old_index.copy()
            
            if self._should_stop():
                return
            
            # Get all files (no limit)
            current_files = get_project_files(root_dir=str(self.root_path), file_mask=self.file_mask)
            
            if self._should_stop():
                return
            
            with self._progress_lock:
                self._discovery_total = len(current_files)
            
            # Categorize files - this will use old_index to determine which files
            # are unchanged, need refinement, or are new/modified
            files_to_coarse_index, files_to_refine, new_index = self._categorize_files(current_files, old_index)
            
            if self._should_stop():
                return
            
            # Handle deleted files
            current_file_paths_str = {p.relative_to(self.root_path).as_posix() for p in current_files}
            
            # Only handle deleted files if we have an initialized collection
            if self._is_initialized and self.collection:
                self._handle_deleted_files(current_file_paths_str, old_index)
            
            # Update work queues and state
            with self._progress_lock:
                self._files_to_coarse_index = files_to_coarse_index
                self._files_to_refine = files_to_refine
                self._target_index = new_index
                # NOTE: Do NOT merge new_index into _current_index_on_disk here!
                # _current_index_on_disk should only contain files that have been
                # successfully indexed and saved to disk. New files will be added
                # to _current_index_on_disk after they are processed in _run_work_phase.
                self._coarse_total = len(files_to_coarse_index)
                self._coarse_processed = 0
                self._is_discovering = False
            
            if self.debug:
                with self._progress_lock:
                    coarse_count = len(self._files_to_coarse_index)
                    refine_count = len(self._files_to_refine)
                if coarse_count > 0:
                    rprint(f"  [green]Found:[/] {coarse_count} new or modified file(s) for initial indexing.")
                if refine_count > 0:
                    rprint(f"  [cyan]Found:[/] {refine_count} file(s) to refine for better search quality.")
                if coarse_count == 0 and refine_count == 0:
                    rprint("[green]Project index is up-to-date.[/]")
            
            # Start background indexing if there's work to do
            if self.has_work() and not self._should_stop():
                indexing_thread = threading.Thread(target=self.run_sync_in_background, daemon=True)
                indexing_thread.start()
                    
        except Exception as e:
            with self._progress_lock:
                self._is_discovering = False
            if self.verbose:
                rprint(f"[red]Error during async file discovery: {e}[/red]")

    def prepare_sync(self, verbose: bool = False) -> None:
        """
        Performs a fast scan for file changes and prepares lists of files for
        coarse indexing and refinement. If more than 1000 files are found,
        asks for user confirmation and switches to async discovery.
        """
        if self._should_stop():
            return
            
        if not self._is_initialized and not self._lazy_initialize():
            if verbose and onnx_manager.get_model_status() == "DOWNLOADING":
                rprint("[yellow]Code lookup is initializing (downloading models)... Project scan will begin shortly.[/]")
            return

        if not self.collection:
            if verbose:
                rprint("[yellow]Code lookup is disabled. Skipping project scan.[/]")
            return

        old_index = self._load_old_index()
        
        # Try to enumerate up to 1000 files synchronously
        current_files, limit_hit = get_project_files_with_limit(
            root_dir=str(self.root_path), 
            file_mask=self.file_mask,
            limit=1000
        )
        
        if limit_hit:
            # We hit the 1000 file limit - warn and ask for confirmation
            rprint(f"\n[bold yellow]⚠️  Whoa! 1000+ files discovered...[/]")
            rprint("[yellow]Is this really how large your project is, or did some libraries get included by accident?[/]")
            rprint("[yellow]You can use .gitignore or .ayeignore to exclude subfolders and files.[/]\n")
            
            if not Confirm.ask("[bold]Do you want to continue with indexing?[/bold]", default=False):
                rprint("[cyan]Indexing cancelled. Please update your ignore files and restart aye chat.[/]")
                return
            
            rprint("[cyan]Starting async file discovery... The chat will be available immediately.\n")
            
            # Initialize _current_index_on_disk from old_index before starting async discovery
            # This ensures resume support works correctly - only previously indexed files are here
            with self._progress_lock:
                self._current_index_on_disk = old_index.copy()
            
            # Start async file discovery in a background thread
            discovery_thread = threading.Thread(
                target=self._async_file_discovery,
                args=(old_index,),
                daemon=True
            )
            discovery_thread.start()
            return
        
        # Less than 1000 files - proceed with synchronous categorization
        files_to_coarse_index, files_to_refine, new_index = self._categorize_files(current_files, old_index)
        
        current_file_paths_str = {p.relative_to(self.root_path).as_posix() for p in current_files}
        deleted_files = self._handle_deleted_files(current_file_paths_str, old_index)
        
        if files_to_coarse_index:
            if self.debug:
                rprint(f"  [green]Found:[/] {len(files_to_coarse_index)} new or modified file(s) for initial indexing.")
            self._files_to_coarse_index = files_to_coarse_index
            self._coarse_total = len(files_to_coarse_index)
            self._coarse_processed = 0
        
        if files_to_refine:
            if self.debug:
                rprint(f"  [cyan]Found:[/] {len(files_to_refine)} file(s) to refine for better search quality.")
            self._files_to_refine = files_to_refine
        
        if not deleted_files and not files_to_coarse_index and not files_to_refine:
            if self.debug:
                rprint("[green]Project index is up-to-date.[/]")

        self._target_index = new_index
        # Only copy old_index (already indexed files) to _current_index_on_disk
        self._current_index_on_disk = old_index.copy()

    def _process_one_file_coarse(self, rel_path_str: str) -> Optional[str]:
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

    def _save_progress(self):
        with self._save_lock:
            with self._progress_lock:
                index_to_save = self._current_index_on_disk.copy()
            
            if not index_to_save:
                return
                
            self.index_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self.hash_index_path.with_suffix('.json.tmp')
            try:
                temp_path.write_text(json.dumps(index_to_save, indent=2), encoding="utf-8")
                os.replace(temp_path, self.hash_index_path)
            except Exception:
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)

    def _run_work_phase(self, worker_func: Callable, file_list: List[str], is_refinement: bool):
        processed_since_last_save = 0
        
        # Filter out files that should be skipped for resume support
        files_to_process = []
        with self._progress_lock:
            for path in file_list:
                if self._should_stop():
                    break
                # For coarse indexing: skip files already in _current_index_on_disk
                # (they were indexed in a previous session and saved to disk)
                if not is_refinement:
                    # Check if file is already indexed (exists in current index on disk)
                    # _current_index_on_disk only contains files that were previously
                    # indexed and saved, NOT files from _target_index
                    if path in self._current_index_on_disk:
                        # File was already coarse-indexed in a previous session
                        self._coarse_processed += 1
                        continue
                # For refinement: skip files already refined
                else:
                    current_meta = self._current_index_on_disk.get(path)
                    if current_meta and current_meta.get('refined', False):
                        # File already refined, skip
                        self._refine_processed += 1
                        continue
                files_to_process.append(path)
        
        if not files_to_process:
            return
        
        with DaemonThreadPoolExecutor(max_workers=MAX_WORKERS, initializer=_set_low_priority) as executor:
            future_to_path = {executor.submit(worker_func, path): path for path in files_to_process}

            for future in concurrent.futures.as_completed(future_to_path):
                if self._should_stop():
                    # Cancel remaining futures
                    for f in future_to_path:
                        f.cancel()
                    break
                    
                path = future_to_path[future]
                try:
                    if future.result():
                        with self._progress_lock:
                            if is_refinement:
                                if path in self._current_index_on_disk:
                                    self._current_index_on_disk[path]['refined'] = True
                                else:
                                    # File might have been coarse-indexed in this session
                                    # and not yet in _current_index_on_disk
                                    final_meta = self._target_index.get(path)
                                    if final_meta:
                                        final_meta = final_meta.copy()
                                        final_meta['refined'] = True
                                        self._current_index_on_disk[path] = final_meta
                            else:
                                # Coarse indexing complete - add to _current_index_on_disk
                                final_meta = self._target_index.get(path)
                                if final_meta:
                                    self._current_index_on_disk[path] = final_meta
                            processed_since_last_save += 1
                except Exception:
                    pass

                if processed_since_last_save >= self.SAVE_INTERVAL:
                    self._save_progress()
                    processed_since_last_save = 0

        if processed_since_last_save > 0:
            self._save_progress()

    def run_sync_in_background(self):
        """
        Waits for the local code search to be ready, then runs the indexing and
        refinement process in the background.
        """
        # Wait for the local code search to be ready. This will block the background thread,
        # but not the main application thread.
        while not self._is_initialized and not self._should_stop():
            if self._lazy_initialize():
                break
            # If model download has failed, exit this thread.
            if onnx_manager.get_model_status() == "FAILED":
                return
            time.sleep(1)

        if self._should_stop():
            return

        if not self.collection:
            return  # RAG system is disabled, so no indexing work to do.

        # Wait for async discovery to complete if it's running
        while self._is_discovering and not self._should_stop():
            time.sleep(0.5)

        if self._should_stop():
            return

        if not self.has_work():
            return

        # Set TOKENIZERS_PARALLELISM to false for this background process
        # to avoid warnings and potential deadlocks with our own thread pool.
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'

        try:
            if self._files_to_coarse_index and not self._should_stop():
                self._is_indexing = True
                self._run_work_phase(self._process_one_file_coarse, self._files_to_coarse_index, is_refinement=False)
                self._is_indexing = False

            if self._should_stop():
                return

            all_files_to_refine = sorted(list(set(self._files_to_refine + self._files_to_coarse_index)))

            if all_files_to_refine and not self._should_stop():
                self._is_refining = True
                self._refine_total = len(all_files_to_refine)
                self._refine_processed = 0
                self._run_work_phase(self._process_one_file_refine, all_files_to_refine, is_refinement=True)
                self._is_refining = False
        finally:
            # Save final progress before clearing state
            self._save_progress()
            
            self._is_indexing = self._is_refining = False
            self._files_to_coarse_index = self._files_to_refine = []
            self._target_index = {}
            # Don't clear _current_index_on_disk here - it's saved to disk already

    def has_work(self) -> bool:
        return bool(self._files_to_coarse_index or self._files_to_refine)

    def is_indexing(self) -> bool:
        """Check if indexing is in progress. Non-blocking check."""
        # Use a non-blocking check to avoid delays in the main thread
        # We read the flags directly - they're simple booleans and the worst
        # case is a slightly stale value which is acceptable
        return self._is_indexing or self._is_refining or self._is_discovering

    def get_progress_display(self) -> str:
        """Get progress display string. Uses lock but should be fast."""
        # Try to acquire lock with timeout to avoid blocking main thread
        acquired = self._progress_lock.acquire(timeout=0.01)
        if not acquired:
            # Lock is held by background thread, return a generic message
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

    def query(self, query_text: str, n_results: int = 10, min_relevance: float = 0.0) -> List[VectorIndexResult]:
        if self._should_stop():
            return []
            
        if not self._is_initialized and not self._lazy_initialize():
            if onnx_manager.get_model_status() == "DOWNLOADING":
                rprint("[yellow]Code lookup is still initializing (downloading models)... Search is temporarily disabled.[/]")
            return []

        if not self.collection:
            return []  # RAG system is disabled.
            
        return vector_db.query_index(
            collection=self.collection,
            query_text=query_text,
            n_results=n_results,
            min_relevance=min_relevance
        )

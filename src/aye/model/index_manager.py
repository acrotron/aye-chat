import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Tuple
import threading
import concurrent.futures
import weakref
import time

from rich import print as rprint

from aye.model.models import VectorIndexResult
from aye.model.source_collector import get_project_files
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

# Determine a reasonable number of workers for background indexing
# to avoid saturating the CPU and making the UI unresponsive.
try:
    # Use half the available cores, with a max of 4, but always at least 1.
    CPU_COUNT = os.cpu_count() or 2
    MAX_WORKERS = min(4, max(1, CPU_COUNT // 2))
except (ImportError, NotImplementedError):
    MAX_WORKERS = 2 # A safe fallback if cpu_count() is not available.


class IndexManager:
    """
    Manages the file hash index and the vector database for a project.
    This class encapsulates all logic for scanning, indexing, and querying project files.
    It uses a two-phase progressive indexing strategy:
    1. Coarse Indexing: A fast, file-per-chunk pass for immediate usability.
    2. Refinement: A background process that replaces coarse chunks with fine-grained ones.
    """
    def __init__(self, root_path: Path, file_mask: str, verbose: bool = False):
        self.root_path = root_path
        self.file_mask = file_mask
        self.verbose = verbose
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
        self._progress_lock = threading.Lock()

    def _lazy_initialize(self) -> bool:
        """
        Initializes the ChromaDB collection if it hasn't been already and if the
        ONNX model is ready. Returns True on success or if already initialized.
        """
        with self._initialization_lock:
            if self._is_initialized:
                return self.collection is not None

            model_status = onnx_manager.get_model_status()
            
            if model_status == "READY":
                try:
                    self.collection = vector_db.initialize_index(self.root_path)
                    self._is_initialized = True
                    if self.verbose:
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

    def prepare_sync(self, verbose: bool = False) -> None:
        """
        Performs a fast scan for file changes and prepares lists of files for
        coarse indexing and refinement.
        """
        if not self._is_initialized and not self._lazy_initialize():
            if verbose and onnx_manager.get_model_status() == "DOWNLOADING":
                rprint("[yellow]Code lookup is initializing (downloading models)... Project scan will begin shortly.[/]")
            return

        if not self.collection:
            if verbose:
                rprint("[yellow]Code lookup is disabled. Skipping project scan.[/]")
            return

        old_index: Dict[str, Any] = {}
        if self.hash_index_path.is_file():
            try:
                old_index = json.loads(self.hash_index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                old_index = {}

        current_files = get_project_files(root_dir=str(self.root_path), file_mask=self.file_mask)
        
        new_index: Dict[str, Dict[str, Any]] = {}
        files_to_coarse_index: List[str] = []
        files_to_refine: List[str] = []
        
        current_file_paths_str = {p.relative_to(self.root_path).as_posix() for p in current_files}

        for file_path in current_files:
            rel_path_str = file_path.relative_to(self.root_path).as_posix()
            status, meta = self._check_file_status(file_path, old_index)

            if status == "modified":
                files_to_coarse_index.append(rel_path_str)
                if meta: new_index[rel_path_str] = meta
            elif status == "needs_refinement":
                files_to_refine.append(rel_path_str)
                if meta: new_index[rel_path_str] = meta
            elif status == "unchanged":
                if meta: new_index[rel_path_str] = meta
            # 'error' status is ignored

        deleted_files = list(set(old_index.keys()) - current_file_paths_str)
        if deleted_files:
            if verbose:
                rprint(f"  [red]Deleted:[/] {len(deleted_files)} file(s) from index.")
            vector_db.delete_from_index(self.collection, deleted_files)

        if files_to_coarse_index:
            if verbose:
                rprint(f"  [green]Found:[/] {len(files_to_coarse_index)} new or modified file(s) for initial indexing.")
            self._files_to_coarse_index = files_to_coarse_index
            self._coarse_total = len(files_to_coarse_index)
            self._coarse_processed = 0
        
        if files_to_refine:
            if verbose:
                rprint(f"  [cyan]Found:[/] {len(files_to_refine)} file(s) to refine for better search quality.")
            self._files_to_refine = files_to_refine
        
        if not deleted_files and not files_to_coarse_index and not files_to_refine:
            if verbose:
                rprint("[green]Project index is up-to-date.[/]")

        self._target_index = new_index
        self._current_index_on_disk = old_index.copy()

    def _process_one_file_coarse(self, rel_path_str: str) -> Optional[str]:
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
        with self._progress_lock:
            index_to_save = self._current_index_on_disk.copy()
        
        if not index_to_save: return
            
        self.index_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.hash_index_path.with_suffix('.json.tmp')
        try:
            temp_path.write_text(json.dumps(index_to_save, indent=2), encoding="utf-8")
            os.replace(temp_path, self.hash_index_path)
        except Exception:
            if temp_path.exists(): temp_path.unlink(missing_ok=True)

    def _run_work_phase(self, worker_func: Callable, file_list: List[str], is_refinement: bool):
        processed_since_last_save = 0
        with DaemonThreadPoolExecutor(max_workers=MAX_WORKERS, initializer=_set_low_priority) as executor:
            future_to_path = {executor.submit(worker_func, path): path for path in file_list}

            for future in concurrent.futures.as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    if future.result():
                        with self._progress_lock:
                            if is_refinement:
                                if path in self._current_index_on_disk:
                                    self._current_index_on_disk[path]['refined'] = True
                            else:
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
        while not self._is_initialized:
            if self._lazy_initialize():
                break
            # If model download has failed, exit this thread.
            if onnx_manager.get_model_status() == "FAILED":
                return
            time.sleep(1)

        if not self.collection:
            return  # RAG system is disabled, so no indexing work to do.

        if not self.has_work():
            return

        # Set TOKENIZERS_PARALLELISM to false for this background process
        # to avoid warnings and potential deadlocks with our own thread pool.
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'

        try:
            if self._files_to_coarse_index:
                self._is_indexing = True
                self._run_work_phase(self._process_one_file_coarse, self._files_to_coarse_index, is_refinement=False)
                self._is_indexing = False

            all_files_to_refine = sorted(list(set(self._files_to_refine + self._files_to_coarse_index)))

            if all_files_to_refine:
                self._is_refining = True
                self._refine_total = len(all_files_to_refine)
                self._refine_processed = 0
                self._run_work_phase(self._process_one_file_refine, all_files_to_refine, is_refinement=True)
                self._is_refining = False
        finally:
            self._is_indexing = self._is_refining = False
            self._files_to_coarse_index = self._files_to_refine = []
            self._target_index = self._current_index_on_disk = {}

    def has_work(self) -> bool:
        return bool(self._files_to_coarse_index or self._files_to_refine)

    def is_indexing(self) -> bool:
        return self._is_indexing or self._is_refining

    def get_progress_display(self) -> str:
        with self._progress_lock:
            if self._is_indexing:
                return f"indexing {self._coarse_processed}/{self._coarse_total}"
            if self._is_refining:
                return f"refining {self._refine_processed}/{self._refine_total}"
            return ""

    def query(self, query_text: str, n_results: int = 10, min_relevance: float = 0.0) -> List[VectorIndexResult]:
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

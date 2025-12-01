"""Phase execution for IndexManager.

This module contains:
- PhaseExecutor: Handles coarse and refinement phase execution
- File processing workers
"""

import os
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, TYPE_CHECKING

from aye.model import vector_db

from .index_manager_utils import DaemonThreadPoolExecutor, set_low_priority

if TYPE_CHECKING:
    from .index_manager_state import IndexingState, IndexConfig, ProgressTracker, ErrorHandler


class PhaseExecutor:
    """
    Executes indexing phases (coarse and refinement).
    
    Handles parallel file processing, progress tracking, and periodic saves.
    """
    
    def __init__(
        self,
        config: 'IndexConfig',
        state: 'IndexingState',
        progress: 'ProgressTracker',
        error_handler: 'ErrorHandler',
        collection: Any,
        should_stop: Callable[[], bool],
        save_callback: Callable[[], None]
    ):
        self.config = config
        self.state = state
        self.progress = progress
        self.error_handler = error_handler
        self.collection = collection
        self.should_stop = should_stop
        self.save_callback = save_callback
        self._progress_lock = __import__('threading').Lock()
    
    def execute_coarse_phase(self, file_list: List[str], generation: int) -> None:
        """Execute the coarse indexing phase."""
        if not file_list or self.should_stop():
            return
        
        self.progress.set_active('coarse')
        self.progress.set_total('coarse', len(file_list))
        
        self._run_phase(
            worker_func=self._process_one_file_coarse,
            file_list=file_list,
            is_refinement=False,
            generation=generation
        )
        
        self.progress.set_active(None)
    
    def execute_refine_phase(self, file_list: List[str], generation: int) -> None:
        """Execute the refinement phase."""
        if not file_list or self.should_stop():
            return
        
        self.progress.set_active('refine')
        self.progress.set_total('refine', len(file_list))
        
        self._run_phase(
            worker_func=self._process_one_file_refine,
            file_list=file_list,
            is_refinement=True,
            generation=generation
        )
        
        self.progress.set_active(None)
    
    def _run_phase(
        self,
        worker_func: Callable,
        file_list: List[str],
        is_refinement: bool,
        generation: int
    ) -> None:
        """Run a work phase with parallel processing."""
        files_to_process = self._filter_files_for_processing(file_list, is_refinement)
        
        if not files_to_process:
            return
        
        processed_since_last_save = 0
        
        with DaemonThreadPoolExecutor(
            max_workers=self.config.max_workers,
            initializer=set_low_priority
        ) as executor:
            future_to_path = {
                executor.submit(worker_func, path): path
                for path in files_to_process
            }
            
            for future in concurrent.futures.as_completed(future_to_path):
                if self._should_abort(generation):
                    self._cancel_remaining_futures(future_to_path)
                    break
                
                path = future_to_path[future]
                try:
                    if future.result():
                        self._update_index_after_processing(path, is_refinement)
                        processed_since_last_save += 1
                except Exception as e:
                    self.error_handler.handle_silent(e, f"processing {path}")
                
                if processed_since_last_save >= self.config.save_interval:
                    self.save_callback()
                    processed_since_last_save = 0
        
        if processed_since_last_save > 0:
            self.save_callback()
    
    def _should_abort(self, generation: int) -> bool:
        """Check if the phase should be aborted."""
        if self.should_stop():
            return True
        with self._progress_lock:
            return self.state.generation != generation
    
    def _cancel_remaining_futures(self, future_to_path: Dict) -> None:
        """Cancel all remaining futures."""
        for f in future_to_path:
            f.cancel()
    
    def _filter_files_for_processing(
        self,
        file_list: List[str],
        is_refinement: bool
    ) -> List[str]:
        """Filter out files that should be skipped for resume support."""
        files_to_process = []
        
        with self._progress_lock:
            for path in file_list:
                if self.should_stop():
                    break
                
                if not is_refinement:
                    # Skip files already indexed
                    if path in self.state.current_index_on_disk:
                        self.progress.increment('coarse')
                        continue
                else:
                    # Skip files already refined
                    current_meta = self.state.current_index_on_disk.get(path)
                    if current_meta and current_meta.get('refined', False):
                        self.progress.increment('refine')
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
                if path in self.state.current_index_on_disk:
                    self.state.current_index_on_disk[path]['refined'] = True
                else:
                    final_meta = self.state.target_index.get(path)
                    if final_meta:
                        final_meta = final_meta.copy()
                        final_meta['refined'] = True
                        self.state.current_index_on_disk[path] = final_meta
            else:
                final_meta = self.state.target_index.get(path)
                if final_meta:
                    self.state.current_index_on_disk[path] = final_meta
    
    # =========================================================================
    # File Processing Workers
    # =========================================================================
    
    def _process_one_file_coarse(self, rel_path_str: str) -> Optional[str]:
        """Process a single file for coarse indexing."""
        if self.should_stop():
            return None
        try:
            content = (self.config.root_path / rel_path_str).read_text(encoding="utf-8")
            if self.collection:
                vector_db.update_index_coarse(self.collection, {rel_path_str: content})
            return rel_path_str
        except Exception as e:
            self.error_handler.handle_silent(e, f"coarse indexing {rel_path_str}")
            return None
        finally:
            self.progress.increment('coarse')
    
    def _process_one_file_refine(self, rel_path_str: str) -> Optional[str]:
        """Process a single file for refinement."""
        if self.should_stop():
            return None
        try:
            content = (self.config.root_path / rel_path_str).read_text(encoding="utf-8")
            if self.collection:
                vector_db.refine_file_in_index(self.collection, rel_path_str, content)
            return rel_path_str
        except Exception as e:
            self.error_handler.handle_silent(e, f"refining {rel_path_str}")
            return None
        finally:
            self.progress.increment('refine')

"""Utility classes and functions for IndexManager.

This module contains:
- DaemonThreadPoolExecutor for background indexing
- Process priority utilities
- Global manager cleanup registry
- Constants for worker configuration
"""

import os
import hashlib
import threading
import weakref
import atexit
import concurrent.futures
from concurrent.futures.thread import _worker
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .index_manager import IndexManager


# =============================================================================
# Constants
# =============================================================================

# Determine a reasonable number of workers for background indexing
# to avoid saturating the CPU and making the UI unresponsive.
try:
    CPU_COUNT = os.cpu_count() or 2
    MAX_WORKERS = min(4, max(1, CPU_COUNT // 2))
except (ImportError, NotImplementedError):
    MAX_WORKERS = 2


# =============================================================================
# Custom Daemon ThreadPoolExecutor
# =============================================================================

class DaemonThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """
    A ThreadPoolExecutor that creates daemon threads.
    
    This is a workaround for the standard ThreadPoolExecutor not creating 
    daemon threads. Daemon threads are necessary so that background indexing 
    does not block the main application from exiting.
    
    This implementation is based on the CPython 3.9+ source.
    """
    
    def _adjust_thread_count(self):
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
            t.daemon = True  # Key change: make thread a daemon
            t.start()
            self._threads.add(t)


# =============================================================================
# Process Priority Utilities
# =============================================================================

def set_low_priority() -> None:
    """
    Set the priority of the current worker process to low.
    
    This avoids interfering with the main UI thread. 
    Works on POSIX-compliant systems.
    """
    if hasattr(os, 'nice'):
        try:
            os.nice(5)
        except OSError:
            # User may not have permission to change priority
            pass


def set_discovery_thread_low_priority() -> None:
    """
    Set the priority of the discovery/categorization thread to low.
    
    This avoids consuming 100% CPU during file discovery.
    Works on POSIX-compliant systems.
    """
    if hasattr(os, 'nice'):
        try:
            os.nice(5)
        except OSError:
            pass


# =============================================================================
# Global Manager Cleanup Registry
# =============================================================================

_active_managers: List['IndexManager'] = []
_cleanup_lock = threading.Lock()


def register_manager(manager: 'IndexManager') -> None:
    """Register an IndexManager instance for cleanup on exit."""
    with _cleanup_lock:
        _active_managers.append(manager)


def unregister_manager(manager: 'IndexManager') -> None:
    """Unregister an IndexManager instance."""
    with _cleanup_lock:
        if manager in _active_managers:
            _active_managers.remove(manager)


def _cleanup_all_managers() -> None:
    """Cleanup function called at exit to properly shut down all managers."""
    with _cleanup_lock:
        for manager in _active_managers:
            try:
                manager.shutdown()
            except Exception:
                pass


# Register the cleanup function
atexit.register(_cleanup_all_managers)


# =============================================================================
# Hash Utilities
# =============================================================================

def calculate_hash(content: str) -> str:
    """Calculate the SHA-256 hash of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

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
import atexit
import concurrent.futures
import concurrent.futures.thread as _cf_thread
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

class _DaemonThread(threading.Thread):
    """A threading.Thread subclass that always runs as a daemon."""

    def __init__(self, *args, **kwargs):
        kwargs["daemon"] = True
        super().__init__(*args, **kwargs)


_adjust_thread_count_lock = threading.Lock()


class DaemonThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """
    A ThreadPoolExecutor that creates daemon worker threads so background
    indexing does not block the main application from exiting.

    Rather than re-implementing ``_adjust_thread_count`` (whose body uses
    private attributes that change between CPython releases — e.g. Python 3.14
    refactored ``_initializer``/``_initargs`` storage), this temporarily swaps
    the ``threading.Thread`` reference used inside
    ``concurrent.futures.thread`` for a daemon subclass, then delegates to the
    standard implementation.
    """

    def _adjust_thread_count(self):
        with _adjust_thread_count_lock:
            original = _cf_thread.threading.Thread
            _cf_thread.threading.Thread = _DaemonThread
            try:
                super()._adjust_thread_count()
            finally:
                _cf_thread.threading.Thread = original


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

"""Abstract base class for snapshot storage backends."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union


class SnapshotBackend(ABC):
    """Abstract base class for snapshot storage backends."""

    @abstractmethod
    def create_snapshot(self, file_paths: List[Path], prompt: Optional[str] = None) -> str:
        """Create a snapshot of the given files.

        Args:
            file_paths: List of file paths to snapshot
            prompt: Optional user prompt that triggered this snapshot

        Returns:
            Batch identifier string (e.g., "001_20231201T120000")
        """
        pass

    @abstractmethod
    def list_snapshots(self, file: Optional[Path] = None) -> Union[List[str], List[Tuple[str, str]]]:
        """List all snapshots or snapshots for a specific file.

        Args:
            file: Optional file path to filter by

        Returns:
            If file is None: List of formatted strings for all snapshots
            If file is provided: List of (batch_id, snapshot_path) tuples
        """
        pass

    @abstractmethod
    def restore_snapshot(self, ordinal: Optional[str] = None, file_name: Optional[str] = None) -> None:
        """Restore files from a snapshot.

        Args:
            ordinal: Optional snapshot ordinal (e.g., "001")
            file_name: Optional specific file to restore
        """
        pass

    @abstractmethod
    def list_all_snapshots(self) -> List[Path]:
        """List all snapshot directories/identifiers in chronological order (oldest first)."""
        pass

    @abstractmethod
    def delete_snapshot(self, snapshot_id: Any) -> None:
        """Delete a specific snapshot."""
        pass

    @abstractmethod
    def prune_snapshots(self, keep_count: int = 10) -> int:
        """Delete all but the most recent N snapshots. Returns count deleted."""
        pass

    @abstractmethod
    def cleanup_snapshots(self, older_than_days: int = 30) -> int:
        """Delete snapshots older than N days. Returns count deleted."""
        pass

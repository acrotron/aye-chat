"""ONNX model download and status management.

This module handles downloading the ONNX embedding model used for
vector search and tracks its status.
"""

import os
import threading
from pathlib import Path

import chromadb  # pylint: disable=wrong-import-position
from chromadb.utils import embedding_functions  # pylint: disable=wrong-import-position

# Represents the download status of the ONNX model.
_status = "NOT_CHECKED"  # pylint: disable=invalid-name
_lock = threading.Lock()


def _get_model_flag_file() -> Path:
    """Determines the path for a flag file to indicate a successful download."""
    try:
        # We create a flag inside Chroma's cache dir if possible, as it's a good central spot.
        chroma_cache_dir = Path(os.getenv("CHROMA_CACHE_DIR", Path.home() / ".cache/chroma"))
        return chroma_cache_dir / "onnx_model.downloaded"
    except Exception:  # pylint: disable=broad-exception-caught
        # Fallback to our own config directory if home directory is not writable, etc.
        return Path.home() / ".aye" / "onnx_model.downloaded"


_model_flag_file = _get_model_flag_file()


def get_model_status():
    """
    Checks and returns the current status of the ONNX model.
    Statuses: "READY", "NOT_DOWNLOADED", "DOWNLOADING", "FAILED".
    """
    global _status  # pylint: disable=global-statement
    with _lock:
        if _status == "NOT_CHECKED":
            if _model_flag_file.exists():
                _status = "READY"
            else:
                _status = "NOT_DOWNLOADED"
        return _status


def download_onnx():
    """Trigger ONNX model download by creating a collection with embedding function.

    This is an artificial workaround to trigger model download since
    direct ONNXMiniLM_L6_V2() invocation does not work in our environment.
    """
    print("Preparing the system...")
    client = chromadb.Client()
    ef = embedding_functions.DefaultEmbeddingFunction()
    coll = client.create_collection(name="my_collection", embedding_function=ef)

    coll.add(
        documents=["Sample text 1", "Sample text 2"],
        ids=["id1", "id2"]
    )


def _download_model_sync():
    """Blocking function to download the model and create a flag file on success."""
    global _status  # pylint: disable=global-statement

    # Imports trigger model download as a side effect
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2  # noqa: F401 pylint: disable=import-outside-toplevel,unused-import
    from aye.model.vector_db import suppress_stdout_stderr  # noqa: F401 pylint: disable=import-outside-toplevel,unused-import

    try:
        with _lock:
            _status = "DOWNLOADING"

        # This is the blocking call that downloads the model files on first run.
        download_onnx()

        # If the download succeeds, create the flag file for future checks.
        _model_flag_file.parent.mkdir(parents=True, exist_ok=True)
        _model_flag_file.touch()

        with _lock:
            _status = "READY"

    except Exception:  # pylint: disable=broad-exception-caught
        with _lock:
            _status = "FAILED"


def download_model_if_needed(background: bool = True):
    """
    Checks for the ONNX model and starts a download if it's missing.

    Args:
        background: If True, the download runs on a background daemon thread.
                    If False, it runs synchronously and blocks.
    """
    if get_model_status() == "NOT_DOWNLOADED":
        if background:
            thread = threading.Thread(target=_download_model_sync, daemon=True)
            thread.start()
        else:
            _download_model_sync()

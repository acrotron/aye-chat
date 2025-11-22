import os
import threading
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from rich import print as rprint
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

# Represents the download status of offline LLM models.
_model_status: Dict[str, str] = {}  # model_id -> status
_status_lock = threading.Lock()

# Available offline models configuration
OFFLINE_MODELS = {
    "offline/deepseek-coder-6.7b": {
        "repo_id": "deepseek-ai/DeepSeek-Coder-6.7B-Instruct-GGUF",
        "filename": "deepseek-coder-6.7b-instruct.Q4_K_M.gguf",
        "size_gb": 3.8,
        "context_length": 16384
    },
    "offline/qwen2.5-coder-7b": {
        "repo_id": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "size_gb": 4.2,
        "context_length": 32768
    }
}

def _get_model_cache_dir() -> Path:
    """Get the cache directory for offline models."""
    return Path.home() / ".cache" / "aye" / "offline_models"

def _get_model_flag_file(model_id: str) -> Path:
    """Get the flag file path for a specific model."""
    model_name = model_id.replace("/", "_")
    return _get_model_cache_dir() / f"{model_name}.downloaded"

def get_model_status(model_id: str) -> str:
    """
    Checks and returns the current status of an offline model.
    Statuses: "READY", "NOT_DOWNLOADED", "DOWNLOADING", "FAILED".
    """
    with _status_lock:
        if model_id not in _model_status:
            flag_file = _get_model_flag_file(model_id)
            if flag_file.exists():
                _model_status[model_id] = "READY"
            else:
                _model_status[model_id] = "NOT_DOWNLOADED"
        return _model_status[model_id]

def _set_model_status(model_id: str, status: str) -> None:
    """Set the status of a model."""
    with _status_lock:
        _model_status[model_id] = status

def _download_model_with_progress(model_id: str, repo_id: str, filename: str, size_gb: float) -> bool:
    """
    Download a model from Hugging Face with progress display.
    Returns True on success, False on failure.
    """
    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import HfHubHTTPError
    except ImportError:
        rprint("[red]Error: huggingface_hub is required for offline models.[/]")
        rprint("[red]Install it with: pip install huggingface_hub[/]")
        return False

    try:
        cache_dir = _get_model_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        model_path = cache_dir / filename
        
        # Check if already downloaded
        if model_path.exists():
            return True
            
        rprint(f"[yellow]Downloading {model_id} ({size_gb}GB)...[/]")
        rprint("[yellow]This may take several minutes depending on your internet connection.[/]")
        
        # Download with progress (huggingface_hub handles progress internally)
        downloaded_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            cache_dir=str(cache_dir),
            local_dir=str(cache_dir),
            local_dir_use_symlinks=False
        )
        
        # Create flag file to mark successful download
        flag_file = _get_model_flag_file(model_id)
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.touch()
        
        rprint(f"[green]✅ {model_id} downloaded successfully![/]")
        return True
        
    except HfHubHTTPError as e:
        rprint(f"[red]Failed to download model: {e}[/]")
        return False
    except Exception as e:
        rprint(f"[red]Error downloading model: {e}[/]")
        return False

def download_model_sync(model_id: str) -> bool:
    """
    Synchronously download an offline model.
    Returns True on success, False on failure.
    """
    if model_id not in OFFLINE_MODELS:
        rprint(f"[red]Unknown offline model: {model_id}[/]")
        return False
        
    model_config = OFFLINE_MODELS[model_id]
    
    _set_model_status(model_id, "DOWNLOADING")
    
    success = _download_model_with_progress(
        model_id=model_id,
        repo_id=model_config["repo_id"],
        filename=model_config["filename"],
        size_gb=model_config["size_gb"]
    )
    
    if success:
        _set_model_status(model_id, "READY")
    else:
        _set_model_status(model_id, "FAILED")
        
    return success

def get_model_path(model_id: str) -> Optional[Path]:
    """
    Get the local file path for a downloaded model.
    Returns None if model is not downloaded.
    """
    if get_model_status(model_id) != "READY":
        return None
        
    if model_id not in OFFLINE_MODELS:
        return None
        
    model_config = OFFLINE_MODELS[model_id]
    cache_dir = _get_model_cache_dir()
    model_path = cache_dir / model_config["filename"]
    
    return model_path if model_path.exists() else None

def get_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """Get configuration for an offline model."""
    return OFFLINE_MODELS.get(model_id)

def is_offline_model(model_id: str) -> bool:
    """Check if a model ID refers to an offline model."""
    return model_id.startswith("offline/")

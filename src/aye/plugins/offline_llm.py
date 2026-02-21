import os
import json
import threading
from typing import Dict, Any, Optional
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.spinner import Spinner

from .plugin_base import Plugin
from .model_plugin_utils import (
    TRUNCATED_RESPONSE_MESSAGE,
    get_conversation_id,
    build_user_message,
    build_history_message,
    create_error_response,
    parse_llm_response,
    load_history,
    save_history,
)
from aye.model.config import SYSTEM_PROMPT
from aye.model.offline_llm_manager import (
    download_model_sync,
    get_model_status,
    get_model_path,
    get_model_config,
    is_offline_model
)

# History file name for this plugin
HISTORY_FILENAME = "chat_history.json"


class OfflineLLMPlugin(Plugin):
    name = "offline_llm"
    version = "1.0.1"  # Version bump for new_chat fix
    premium = "free"

    def __init__(self):
        super().__init__()
        self.chat_history: Dict[str, list] = {}
        self.history_file: Optional[Path] = None
        self._llm_instance = None
        self._current_model_id = None
        self._model_lock = threading.Lock()

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the offline LLM plugin."""
        super().init(cfg)
        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

    def _get_history_file_path(self, root: Optional[Any]) -> Path:
        """Get the history file path for this plugin."""
        if root:
            return Path(root) / ".aye" / HISTORY_FILENAME
        return Path.cwd() / ".aye" / HISTORY_FILENAME

    def _check_dependencies(self) -> bool:
        """Check if required dependencies are available."""
        try:
            import llama_cpp
            return True
        except ImportError:
            rprint("[yellow]llama-cpp-python not available for offline inference.[/]")
            rprint("[yellow]Install it with `pip install llama-cpp-python`, restart and try again.[/]")
            return False

    def _load_model(self, model_id: str) -> bool:
        """Load a model into memory for inference. Returns True on success, False on failure."""
        with self._model_lock:
            # If same model already loaded, return success
            if self._current_model_id == model_id and self._llm_instance is not None:
                return True
                
            # Unload previous model
            if self._llm_instance is not None:
                del self._llm_instance
                self._llm_instance = None
                self._current_model_id = None

            if not self._check_dependencies():
                return False

            model_path = get_model_path(model_id)
            if not model_path:
                if self.verbose:
                    rprint(f"[yellow]Model {model_id} not downloaded.[/]")
                return False

            try:
                from llama_cpp import Llama
                
                model_config = get_model_config(model_id)
                context_length = model_config.get("context_length", 16384) if model_config else 16384
                
                if self.verbose:
                    rprint(f"[cyan]Loading {model_id} into memory...[/]")
                
                self._llm_instance = Llama(
                    model_path=str(model_path),
                    n_ctx=context_length,
                    n_threads=None,  # Auto-detect
                    verbose=False
                )
                
                self._current_model_id = model_id
                
                if self.verbose:
                    rprint(f"[green]✅ {model_id} loaded and ready for inference.[/]")
                
                return True
                
            except Exception as e:
                if self.verbose:
                    rprint(f"[red]Failed to load model {model_id}: {e}[/]")
                return False

    def _load_history(self) -> None:
        """Load chat history from disk."""
        self.chat_history = load_history(self.history_file, self.verbose, "offline model")

    def _save_history(self) -> None:
        """Save chat history to disk."""
        save_history(self.history_file, self.chat_history, self.verbose, "offline model")

    def _generate_response(self, model_id: str, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None, system_prompt: Optional[str] = None, max_output_tokens: int = 4096) -> Optional[Dict[str, Any]]:
        """Generate a response using the offline model."""
        if not self._load_model(model_id):
            return create_error_response(f"Failed to load offline model '{model_id}'.", self.verbose)
            
        if not self._llm_instance:
            return create_error_response(f"Model instance for '{model_id}' not available after load attempt.", self.verbose)

        conv_id = get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []

        user_message = build_user_message(prompt, source_files)
        history_message = build_history_message(prompt, source_files)
        
        # Build conversation history
        effective_system_prompt = system_prompt if system_prompt else SYSTEM_PROMPT
        messages = [{"role": "system", "content": effective_system_prompt}]
        messages.extend(self.chat_history[conv_id])
        messages.append({"role": "user", "content": user_message})
        
        # Format for llama.cpp chat completion
        try:
            if self.debug:
                print(messages)
            response = self._llm_instance.create_chat_completion(
                messages=messages,
                temperature=0.7,
                max_tokens=max_output_tokens,
                response_format={"type": "json_object"}
            )

            if self.debug:
                print(response)
                print("----------------")
            
            if response and "choices" in response and response["choices"]:
                generated_text = response["choices"][0]["message"]["content"]
                
                if self.debug:
                    print(generated_text)
                    print("----------------")

                # Update chat history with lightweight message
                self.chat_history[conv_id].append({"role": "user", "content": history_message})
                self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                self._save_history()
                
                res = parse_llm_response(generated_text, self.debug, check_truncation=True)

                if self.debug:
                    print("----- parse_llm_response -------")
                    print(res)
                    print("----------------")
                return res
            else:
                return create_error_response("No response generated from offline model", self.verbose)
                
        except Exception as e:
            print(f"Error generating response: {e}")
            return create_error_response(f"Error generating response: {e}", self.verbose)

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle commands for the offline LLM plugin."""

        if self.debug:
            print("[DEBUG] offline_llm on_command entering...")
        
        if command_name == "download_offline_model":
            model_id = params.get("model_id", "")
            model_name = params.get("model_name", model_id)
            size_gb = params.get("size_gb", 0)
            
            if not is_offline_model(model_id):
                return {"success": False, "error": "Not an offline model"}
                
            # Check if already downloaded
            if get_model_status(model_id) == "READY":
                rprint(f"[green]✅ {model_name} is already downloaded and ready.[/]")
                return {"success": True}
                
            # Download the model
            success = download_model_sync(model_id)
            return {"success": success}
        
        if command_name == "new_chat":
            # Offline LLM plugin always handles new_chat to clear its history
            # (even if no offline model is currently selected, we clean up any existing history)
            root = params.get("root")
            history_file = self._get_history_file_path(root)
            history_file.unlink(missing_ok=True)
            self.chat_history = {}
            if self.verbose: 
                rprint("[yellow]Offline model chat history cleared.[/]")
            return {"status": "offline_history_cleared", "handled": True}

        if command_name == "local_model_invoke":
            model_id = params.get("model_id", "")
            
            # Only handle offline models
            if not is_offline_model(model_id):
                return None
                
            # Check if model is ready
            if get_model_status(model_id) != "READY":
                msg = f"Offline model '{model_id}' is not ready. Please download it via the 'model' command."
                return create_error_response(msg, self.verbose)
                
            prompt = params.get("prompt", "").strip()
            source_files = params.get("source_files", {})
            chat_id = params.get("chat_id")
            root = params.get("root")
            system_prompt = params.get("system_prompt")
            max_output_tokens = params.get("max_output_tokens", 4096)

            self.history_file = self._get_history_file_path(root)
            self._load_history()

            res = self._generate_response(model_id, prompt, source_files, chat_id, system_prompt, max_output_tokens)
            if self.debug:
                print("[DEBUG] -------- end of offline_llm -------")
                print(res)
            return res
            
        return None

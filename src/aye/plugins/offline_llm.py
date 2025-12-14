import os
import json
import threading
from typing import Dict, Any, Optional
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.spinner import Spinner

from .plugin_base import Plugin
from aye.model.config import SYSTEM_PROMPT
from aye.model.offline_llm_manager import (
    download_model_sync,
    get_model_status,
    get_model_path,
    get_model_config,
    is_offline_model
)
from aye.controller.util import is_truncated_json


# Message shown when LLM response is truncated due to output token limits
TRUNCATED_RESPONSE_MESSAGE = (
    "It looks like my response was cut off because it exceeded the output limit. "
    "This usually happens when you ask me to generate or modify many files at once.\n\n"
    "**To fix this, please try:**\n"
    "1. Break your request into smaller parts (e.g., one file at a time)\n"
    "2. Use the `with` command to focus on specific files: `with file1.py, file2.py: your request`\n"
    "3. Ask me to work on fewer files or smaller changes in each request\n\n"
    "For example, instead of 'update all files to add logging', try:\n"
    "  `with src/main.py: add logging to this file`"
)


class OfflineLLMPlugin(Plugin):
    name = "offline_llm"
    version = "1.0.0"
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
        if not self.history_file:
            self.chat_history = {}
            return

        if self.history_file.exists():
            try:
                data = json.loads(self.history_file.read_text(encoding="utf-8"))
                self.chat_history = data.get("conversations", {})
            except Exception as e:
                if self.verbose:
                    rprint(f"[yellow]Could not load offline model chat history: {e}[/]")
                self.chat_history = {}
        else:
            self.chat_history = {}

    def _save_history(self) -> None:
        """Save chat history to disk."""
        if not self.history_file:
            return

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"conversations": self.chat_history}
            self.history_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            if self.verbose:
                rprint(f"[yellow]Could not save offline model chat history: {e}[/]")

    def _get_conversation_id(self, chat_id: Optional[int] = None) -> str:
        """Get conversation ID for history tracking."""
        return str(chat_id) if chat_id and chat_id > 0 else "default"

    def _build_user_message(self, prompt: str, source_files: Dict[str, str]) -> str:
        """Build the user message with optional source files appended."""
        user_message = prompt
        if source_files:
            user_message += "\n\n--- Source files are below. ---\n"
            for file_name, content in source_files.items():
                user_message += f"\n** {file_name} **\n```\n{content}\n```\n"
        return user_message

    def _parse_llm_response(self, generated_text: str) -> Dict[str, Any]:
        """Parse LLM response text and convert to expected format."""
        empty_response = {
                "answer_summary": "No response",
                "source_files": []
        }

        try:
            llm_response = json.loads(generated_text)
        except json.JSONDecodeError as e:
            if self.debug:
                print(f"JSON decode error: {e}")
            
            # Check if this looks like a truncated response
            if is_truncated_json(generated_text):
                if self.debug:
                    print(f"[DEBUG] Response appears to be truncated:")
                    print(generated_text)
                return {
                    "summary": TRUNCATED_RESPONSE_MESSAGE,
                    "updated_files": []
                }
            
            # Not truncated, just malformed - return as plain text
            return {
                "summary": generated_text if generated_text else "No response",
                "updated_files": []
            }
        
        props = llm_response.get("properties")
        if not props:
            props = llm_response

        res = {
            "summary": props.get("answer_summary", ""),
            "updated_files": [
                {
                    "file_name": f.get("file_name"),
                    "file_content": f.get("file_content")
                }
                for f in props.get("source_files", [])
            ]
        }

        if self.debug:
            print("----- returning from parse_llm_resp -----")
            print(res)
        return res

    def _create_error_response(self, error_msg: str) -> Dict[str, Any]:
        """Create a standardized error response."""
        if self.verbose:
            rprint(f"[red]{error_msg}[/]")
        return {
            "summary": error_msg,
            "updated_files": []
        }

    def _generate_response(self, model_id: str, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None, system_prompt: Optional[str] = None, max_output_tokens: int = 4096) -> Optional[Dict[str, Any]]:
        """Generate a response using the offline model."""
        if not self._load_model(model_id):
            return self._create_error_response(f"Failed to load offline model '{model_id}'.")
            
        if not self._llm_instance:
            return self._create_error_response(f"Model instance for '{model_id}' not available after load attempt.")

        conv_id = self._get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []

        user_message = self._build_user_message(prompt, source_files)
        
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

                # Update chat history
                self.chat_history[conv_id].append({"role": "user", "content": user_message})
                self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                self._save_history()
                
                res = self._parse_llm_response(generated_text)

                if self.debug:
                    print("----- parse_llm_resp -------")
                    print(res)
                    print("----------------")
                return res
            else:
                return self._create_error_response("No response generated from offline model")
                
        except Exception as e:
            print(f"Error generating response: {e}")
            return self._create_error_response(f"Error generating response: {e}")

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
            root = params.get("root")
            history_file = Path(root) / ".aye" / "offline_chat_history.json" if root else Path.cwd() / ".aye" / "offline_chat_history.json"
            history_file.unlink(missing_ok=True)
            self.chat_history = {}
            if self.verbose: 
                rprint("[yellow]Offline model chat history cleared.[/]")
            return {"status": "offline_history_cleared"}

        if command_name == "local_model_invoke":
            model_id = params.get("model_id", "")
            
            # Only handle offline models
            if not is_offline_model(model_id):
                return None
                
            # Check if model is ready
            if get_model_status(model_id) != "READY":
                msg = f"Offline model '{model_id}' is not ready. Please download it via the 'model' command."
                return self._create_error_response(msg)
                
            prompt = params.get("prompt", "").strip()
            source_files = params.get("source_files", {})
            chat_id = params.get("chat_id")
            root = params.get("root")
            system_prompt = params.get("system_prompt")
            max_output_tokens = params.get("max_output_tokens", 4096)

            self.history_file = Path(root) / ".aye" / "offline_chat_history.json" if root else Path.cwd() / ".aye" / "offline_chat_history.json"
            self._load_history()

            res = self._generate_response(model_id, prompt, source_files, chat_id, system_prompt, max_output_tokens)
            if self.debug:
                print("[DEBUG] -------- end of offline_llm -------")
                print(res)
            return res
            
        return None

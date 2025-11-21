import os
import json
import threading
from typing import Dict, Any, Optional
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.spinner import Spinner

from .plugin_base import Plugin
from aye.model.offline_llm_manager import (
    download_model_sync,
    get_model_status,
    get_model_path,
    get_model_config,
    is_offline_model
)

# System prompt for offline models (same as local_model.py)
SYSTEM_PROMPT = (
    "You are a helpful assistant. Your name is Archie if you are asked to respond in JSON format, "
    "or Régine if not. You provide clear and concise answers. Answer **directly**, give only the "
    "information the user asked for. When you are unsure, say so. You generate your responses in "
    "text-friendly format because your responses will be displayed in a terminal: use ASCII and pseudo-graphics.\n\n"
    "You follow instructions closely and respond accurately to a given prompt. You emphasize precise "
    "instruction-following and accuracy over speed of response: take your time to understand a question.\n\n"
    "Focus on accuracy in your response and follow the instructions precisely. At the same time, keep "
    "your answers brief and concise unless asked otherwise. Keep the tone professional and neutral.\n\n"
    "There may be source files appended to a user question, only use them if a question asks for help "
    "with code generation or troubleshooting; ignore them if a question is not software code related.\n\n"
    "UNDER NO CIRCUMSTANCES YOU ARE TO UPDATE SOURCE FILES UNLESS EXPLICITLY ASKED.\n\n"
    "When asked to do updates or implement features - you generate full files only as they will be "
    "inserted as is. Do not use diff notation: return only clean full files.\n\n"
    "You MUST respond with a JSON object that conforms to this schema:\n"
    '{\n'
    '    "type": "object",\n'
    '    "properties": {\n'
    '        "answer_summary": {\n'
    '            "type": "string",\n'
    '            "description": "Detailed answer to a user question"\n'
    '        },\n'
    '        "source_files": {\n'
    '            "type": "array",\n'
    '            "items": {\n'
    '                "type": "object",\n'
    '                "properties": {\n'
    '                    "file_name": {\n'
    '                        "type": "string",\n'
    '                        "description": "Name of the source file including relative path"\n'
    '                    },\n'
    '                    "file_content": {\n'
    '                        "type": "string",\n'
    '                        "description": "Full text/content of the source file"\n'
    '                    }\n'
    '                },\n'
    '                "required": ["file_name", "file_content"],\n'
    '                "additionalProperties": false\n'
    '            }\n'
    '        }\n'
    '    },\n'
    '    "required": ["answer_summary", "source_files"],\n'
    '    "additionalProperties": false\n'
    '}'
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
            if self.verbose:
                rprint("[yellow]llama-cpp-python not available for offline inference.[/]")
            return False

    def _load_model(self, model_id: str) -> bool:
        """
        Load a model into memory for inference.
        Returns True on success, False on failure.
        """
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
        try:
            llm_response = json.loads(generated_text)
        except json.JSONDecodeError:
            llm_response = {
                "answer_summary": generated_text,
                "source_files": []
            }
        
        return {
            "summary": llm_response.get("answer_summary", ""),
            "updated_files": [
                {
                    "file_name": f.get("file_name"),
                    "file_content": f.get("file_content")
                }
                for f in llm_response.get("source_files", [])
            ]
        }

    def _create_error_response(self, error_msg: str) -> Dict[str, Any]:
        """Create a standardized error response."""
        if self.verbose:
            rprint(f"[red]{error_msg}[/]")
        return {
            "summary": error_msg,
            "updated_files": []
        }

    def _generate_response(self, model_id: str, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Generate a response using the offline model."""
        if not self._load_model(model_id):
            return None
            
        if not self._llm_instance:
            return None

        conv_id = self._get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []

        user_message = self._build_user_message(prompt, source_files)
        
        # Build conversation history
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.chat_history[conv_id])
        messages.append({"role": "user", "content": user_message})
        
        # Format for llama.cpp chat completion
        try:
            response = self._llm_instance.create_chat_completion(
                messages=messages,
                temperature=0.7,
                max_tokens=4096,
                response_format={"type": "json_object"}
            )
            
            if response and "choices" in response and response["choices"]:
                generated_text = response["choices"][0]["message"]["content"]
                
                # Update chat history
                self.chat_history[conv_id].append({"role": "user", "content": user_message})
                self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                self._save_history()
                
                return self._parse_llm_response(generated_text)
            else:
                return self._create_error_response("No response generated from offline model")
                
        except Exception as e:
            return self._create_error_response(f"Error generating response: {e}")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle commands for the offline LLM plugin."""
        
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
                if self.verbose:
                    rprint(f"[yellow]Offline model {model_id} not ready.[/]")
                return None
                
            prompt = params.get("prompt", "").strip()
            source_files = params.get("source_files", {})
            chat_id = params.get("chat_id")
            root = params.get("root")

            self.history_file = Path(root) / ".aye" / "offline_chat_history.json" if root else Path.cwd() / ".aye" / "offline_chat_history.json"
            self._load_history()

            return self._generate_response(model_id, prompt, source_files, chat_id)
            
        return None

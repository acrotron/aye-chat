import os
import json
from typing import Dict, Any, Optional
import httpx
from pathlib import Path

from rich import print as rprint

from .plugin_base import Plugin
from .model_plugin_utils import (
    get_conversation_id,
    build_user_message,
    build_history_message,
    create_error_response,
    parse_llm_response,
    load_history,
    save_history,
)
from aye.model.config import SYSTEM_PROMPT, MODELS, DEFAULT_MAX_OUTPUT_TOKENS
from aye.model.auth import get_user_config

LLM_TIMEOUT = 600.0

# History file name for this plugin
HISTORY_FILENAME = "chat_history.json"


def _get_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """Get configuration for a specific model."""
    for model in MODELS:
        if model["id"] == model_id:
            return model
    return None


def _is_local_model_configured() -> bool:
    """Check if local model (OpenAI-compatible or Gemini) is configured."""
    # OpenAI-compatible API
    if get_user_config("llm_api_url") and get_user_config("llm_api_key"):
        return True
    # Gemini API
    if os.environ.get("GEMINI_API_KEY"):
        return True
    return False


class LocalModelPlugin(Plugin):
    name = "local_model"
    version = "1.0.1"  # Version bump for new_chat fix
    premium = "free"

    def __init__(self):
        super().__init__()
        self.chat_history: Dict[str, list] = {}
        self.history_file: Optional[Path] = None

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the local model plugin."""
        super().init(cfg)
        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

    def _get_history_file_path(self, root: Optional[Any]) -> Path:
        """Get the history file path for this plugin."""
        if root:
            return Path(root) / ".aye" / HISTORY_FILENAME
        return Path.cwd() / ".aye" / HISTORY_FILENAME

    def _load_history(self) -> None:
        """Load chat history from disk."""
        self.chat_history = load_history(self.history_file, self.verbose, "local model")

    def _save_history(self) -> None:
        """Save chat history to disk."""
        save_history(self.history_file, self.chat_history, self.verbose, "local model")

    def _handle_openai_compatible(self, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None, system_prompt: Optional[str] = None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> Optional[Dict[str, Any]]:
        """Handle OpenAI-compatible API endpoints.
        
        Reads configuration from:
        - get_user_config("llm_api_url") / AYE_LLM_API_URL
        - get_user_config("llm_api_key") / AYE_LLM_API_KEY  
        - get_user_config("llm_model") / AYE_LLM_MODEL (default: gpt-3.5-turbo)
        """
        api_url = get_user_config("llm_api_url")
        api_key = get_user_config("llm_api_key")
        model_name = get_user_config("llm_model", "gpt-3.5-turbo")
        
        if not api_url or not api_key:
            return None
        
        conv_id = get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []
        
        user_message = build_user_message(prompt, source_files)
        history_message = build_history_message(prompt, source_files)
        effective_system_prompt = system_prompt if system_prompt else SYSTEM_PROMPT
        messages = [{"role": "system", "content": effective_system_prompt}] + self.chat_history[conv_id] + [{"role": "user", "content": user_message}]
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {"model": model_name, "messages": messages, "temperature": 0.7, "max_tokens": max_output_tokens, "response_format": {"type": "json_object"}}
        
        try:
            with httpx.Client(timeout=LLM_TIMEOUT) as client:
                response = client.post(api_url, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                if result.get("choices") and result["choices"][0].get("message"):
                    generated_text = result["choices"][0]["message"]["content"]
                    self.chat_history[conv_id].append({"role": "user", "content": history_message})
                    self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                    self._save_history()
                    return parse_llm_response(generated_text, self.debug)
                return create_error_response("Failed to get a valid response from the OpenAI-compatible API", self.verbose)
        except httpx.HTTPStatusError as e:
            error_msg = f"OpenAI API error: {e.response.status_code}"
            try:
                error_detail = e.response.json()
                if "error" in error_detail:
                    error_msg += f" - {error_detail['error'].get('message', str(error_detail['error']))}"
            except: error_msg += f" - {e.response.text[:200]}"
            return create_error_response(error_msg, self.verbose)
        except Exception as e:
            return create_error_response(f"Error calling OpenAI-compatible API: {str(e)}", self.verbose)

    def _handle_gemini_pro_25(self, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None, system_prompt: Optional[str] = None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> Optional[Dict[str, Any]]:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None

        conv_id = get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []

        user_message = build_user_message(prompt, source_files)
        history_message = build_history_message(prompt, source_files)
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        
        contents = [{"role": "user" if msg["role"] == "user" else "model", "parts": [{"text": msg["content"]}]} for msg in self.chat_history[conv_id]]
        contents.append({"role": "user", "parts": [{"text": user_message}]})
        
        effective_system_prompt = system_prompt if system_prompt else SYSTEM_PROMPT
        payload = {"contents": contents, "systemInstruction": {"parts": [{"text": effective_system_prompt}]}, "generationConfig": {"temperature": 0.7, "topK": 40, "topP": 0.95, "maxOutputTokens": max_output_tokens, "responseMimeType": "application/json"}}

        try:
            with httpx.Client(timeout=LLM_TIMEOUT) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                if result.get("candidates") and result["candidates"][0].get("content"):
                    generated_text = result["candidates"][0]["content"]["parts"][0].get("text", "")
                    self.chat_history[conv_id].append({"role": "user", "content": history_message})
                    self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                    self._save_history()
                    return parse_llm_response(generated_text, self.debug)
                return create_error_response("Failed to get a valid response from Gemini API", self.verbose)
        except httpx.HTTPStatusError as e:
            return create_error_response(f"Gemini API error: {e.response.status_code} - {e.response.text}", self.verbose)
        except Exception as e:
            return create_error_response(f"Error calling Gemini API: {str(e)}", self.verbose)

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "new_chat":
            # Only handle if local model is configured
            if not _is_local_model_configured():
                return None
            
            root = params.get("root")
            history_file = self._get_history_file_path(root)
            history_file.unlink(missing_ok=True)
            self.chat_history = {}
            if self.verbose:
                rprint("[yellow]Local model chat history cleared.[/]")
            return {"status": "local_history_cleared", "handled": True}

        if command_name == "local_model_invoke":
            prompt = params.get("prompt", "").strip()
            model_id = params.get("model_id", "")
            source_files = params.get("source_files", {})
            chat_id = params.get("chat_id")
            root = params.get("root")
            system_prompt = params.get("system_prompt")
            max_output_tokens = params.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)

            self.history_file = self._get_history_file_path(root)
            self._load_history()

            result = self._handle_openai_compatible(prompt, source_files, chat_id, system_prompt, max_output_tokens)
            if result is not None: return result

            if model_id == "google/gemini-2.5-pro":
                return self._handle_gemini_pro_25(prompt, source_files, chat_id, system_prompt, max_output_tokens)
            
            return None

        return None

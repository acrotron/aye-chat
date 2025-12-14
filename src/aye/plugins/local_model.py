import os
import json
from typing import Dict, Any, Optional
import httpx
from pathlib import Path

from rich import print as rprint

from .plugin_base import Plugin
from aye.model.config import SYSTEM_PROMPT, MODELS, DEFAULT_MAX_OUTPUT_TOKENS
from aye.controller.util import is_truncated_json

LLM_TIMEOUT = 600.0


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


def _get_model_config(model_id: str) -> Optional[Dict[str, Any]]:
    """Get configuration for a specific model."""
    for model in MODELS:
        if model["id"] == model_id:
            return model
    return None


class LocalModelPlugin(Plugin):
    name = "local_model"
    version = "1.0.0"
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
        
    def _load_history(self) -> None:
        """Load chat history from disk."""
        if not self.history_file:
            if self.verbose:
                rprint("[yellow]History file path not set for local model. Skipping load.[/]")
            self.chat_history = {}
            return

        if self.history_file.exists():
            try:
                data = json.loads(self.history_file.read_text(encoding="utf-8"))
                self.chat_history = data.get("conversations", {})
            except Exception as e:
                if self.verbose:
                    rprint(f"[yellow]Could not load chat history: {e}[/]")
                self.chat_history = {}
        else:
            self.chat_history = {}

    def _save_history(self) -> None:
        """Save chat history to disk."""
        if not self.history_file:
            if self.verbose:
                rprint("[yellow]History file path not set for local model. Skipping save.[/]")
            return

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"conversations": self.chat_history}
            self.history_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            if self.verbose:
                rprint(f"[yellow]Could not save chat history: {e}[/]")

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
            # Check if this looks like a truncated response
            #if is_truncated_json(generated_text):
            #    return {
            #        "summary": TRUNCATED_RESPONSE_MESSAGE,
            #        "updated_files": []
            #    }
            
            # Not truncated, just malformed - return as plain text
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

    def _handle_databricks(self, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None, system_prompt: Optional[str] = None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> Optional[Dict[str, Any]]:
        api_url = os.environ.get("AYE_DBX_API_URL")
        api_key = os.environ.get("AYE_DBX_API_KEY")
        model_name = os.environ.get("AYE_DBX_MODEL", "gpt-3.5-turbo")
        
        if not api_url or not api_key:
            return None
        
        conv_id = self._get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []
        
        user_message = self._build_user_message(prompt, source_files)
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
                    generated_text = result["choices"][0]["message"]["content"][0]["text"]
                    self.chat_history[conv_id].append({"role": "user", "content": user_message})
                    self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                    self._save_history()
                    return self._parse_llm_response(generated_text)
                return self._create_error_response("Failed to get a valid response from the Databricks API")
        except httpx.HTTPStatusError as e:
            error_msg = f"DBX API error: {e.response.status_code}"
            try:
                error_detail = e.response.json()
                if "error" in error_detail:
                    error_msg += f" - {error_detail['error'].get('message', str(error_detail['error']))}"
            except: error_msg += f" - {e.response.text[:200]}"
            return self._create_error_response(error_msg)
        except Exception as e:
            return self._create_error_response(f"Error calling Databricks API: {str(e)}")

    def _handle_openai_compatible(self, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None, system_prompt: Optional[str] = None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> Optional[Dict[str, Any]]:
        api_url = os.environ.get("AYE_LLM_API_URL")
        api_key = os.environ.get("AYE_LLM_API_KEY")
        model_name = os.environ.get("AYE_LLM_MODEL", "gpt-3.5-turbo")
        
        if not api_url or not api_key:
            return None
        
        conv_id = self._get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []
        
        user_message = self._build_user_message(prompt, source_files)
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
                    self.chat_history[conv_id].append({"role": "user", "content": user_message})
                    self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                    self._save_history()
                    return self._parse_llm_response(generated_text)
                return self._create_error_response("Failed to get a valid response from the OpenAI-compatible API")
        except httpx.HTTPStatusError as e:
            error_msg = f"OpenAI API error: {e.response.status_code}"
            try:
                error_detail = e.response.json()
                if "error" in error_detail:
                    error_msg += f" - {error_detail['error'].get('message', str(error_detail['error']))}"
            except: error_msg += f" - {e.response.text[:200]}"
            return self._create_error_response(error_msg)
        except Exception as e:
            return self._create_error_response(f"Error calling OpenAI-compatible API: {str(e)}")

    def _handle_gemini_pro_25(self, prompt: str, source_files: Dict[str, str], chat_id: Optional[int] = None, system_prompt: Optional[str] = None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> Optional[Dict[str, Any]]:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None

        conv_id = self._get_conversation_id(chat_id)
        if conv_id not in self.chat_history:
            self.chat_history[conv_id] = []

        user_message = self._build_user_message(prompt, source_files)
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
                    self.chat_history[conv_id].append({"role": "user", "content": user_message})
                    self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                    self._save_history()
                    return self._parse_llm_response(generated_text)
                return self._create_error_response("Failed to get a valid response from Gemini API")
        except httpx.HTTPStatusError as e:
            return self._create_error_response(f"Gemini API error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            return self._create_error_response(f"Error calling Gemini API: {str(e)}")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "new_chat":
            root = params.get("root")
            history_file = Path(root) / ".aye" / "chat_history.json" if root else Path.cwd() / ".aye" / "chat_history.json"
            history_file.unlink(missing_ok=True)
            self.chat_history = {}
            if self.verbose: rprint("[yellow]Local model chat history cleared.[/]")
            return {"status": "local_history_cleared"}

        if command_name == "local_model_invoke":
            prompt = params.get("prompt", "").strip()
            model_id = params.get("model_id", "")
            source_files = params.get("source_files", {})
            chat_id = params.get("chat_id")
            root = params.get("root")
            system_prompt = params.get("system_prompt")
            max_output_tokens = params.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)

            self.history_file = Path(root) / ".aye" / "chat_history.json" if root else Path.cwd() / ".aye" / "chat_history.json"
            self._load_history()

            result = self._handle_openai_compatible(prompt, source_files, chat_id, system_prompt, max_output_tokens)
            if result is not None: return result

            result = self._handle_databricks(prompt, source_files, chat_id, system_prompt, max_output_tokens)
            if result is not None: return result

            if model_id == "google/gemini-2.5-pro":
                return self._handle_gemini_pro_25(prompt, source_files, chat_id, system_prompt, max_output_tokens)
            
            return None

        return None

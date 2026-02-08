import os
import json
from typing import Dict, Any, Optional
import httpx
from pathlib import Path
import traceback

from rich import print as rprint

from .plugin_base import Plugin
from aye.model.config import SYSTEM_PROMPT, MODELS, DEFAULT_MAX_OUTPUT_TOKENS
from aye.model.auth import get_user_config
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


def _extract_json_object(raw_response: str, prefer_last: bool = True, require_keys=None):
    """
    Best-effort extraction of a JSON object (dict) from a raw LLM response.

    Handles common failure modes where the model returns:
    - extra commentary before/after JSON
    - multiple JSON objects (e.g., an invalid attempt + a corrected attempt)

    Args:
        raw_response: raw LLM response string
        prefer_last: when multiple JSON objects exist, return the last parsed object
        require_keys: optional iterable of keys; if provided, only consider objects
                      that contain all of these keys

    Returns:
        dict or None
    """
    # 1) Direct JSON parse
    try:
        obj = json.loads(raw_response)
        if isinstance(obj, dict):
            if require_keys and not all(k in obj for k in require_keys):
                return None
            return obj
    except Exception:
        pass

    text = str(raw_response)

    # 2) Scan for balanced JSON object candidates (string/escape-aware)
    candidates = []
    depth = 0
    start = None
    in_str = False
    escape = False

    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue

        # not in string
        if ch == '"':
            in_str = True
            continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : i + 1])
                start = None

    parsed = []
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if not isinstance(obj, dict):
                continue
            if require_keys and not all(k in obj for k in require_keys):
                continue
            parsed.append(obj)
        except Exception:
            continue

    if not parsed:
        return None

    return parsed[-1] if prefer_last else parsed[0]


class DatabricksModelPlugin(Plugin):
    name = "databricks_model"
    version = "1.0.0"
    premium = "free"

    def __init__(self):
        super().__init__()
        # Keep our own writable flags. The base Plugin may expose read-only
        # properties backed by config; tests expect these to be settable.
        self._verbose: bool = False
        self._debug: bool = False

        self.chat_history: Dict[str, list] = {}
        self.history_file: Optional[Path] = None

    @property
    def verbose(self) -> bool:  # type: ignore[override]
        return bool(self._verbose)

    @verbose.setter
    def verbose(self, value: bool) -> None:  # type: ignore[override]
        self._verbose = bool(value)

    @property
    def debug(self) -> bool:  # type: ignore[override]
        return bool(self._debug)

    @debug.setter
    def debug(self, value: bool) -> None:  # type: ignore[override]
        self._debug = bool(value)

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the local model plugin."""
        super().init(cfg)

        # Ensure flags reflect cfg and remain writable.
        if isinstance(cfg, dict):
            if "verbose" in cfg:
                self._verbose = bool(cfg.get("verbose"))
            if "debug" in cfg:
                self._debug = bool(cfg.get("debug"))

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
        llm_response: Any
        try:
            llm_response = json.loads(generated_text)
        except Exception:
            llm_response = {
                "answer_summary": generated_text,
                "source_files": [],
            }

        # If the JSON is valid but isn't an object (e.g. null, list, number),
        # treat it as plain text.
        if not isinstance(llm_response, dict):
            llm_response = {
                "answer_summary": generated_text,
                "source_files": [],
            }

        return {
            "summary": llm_response.get("answer_summary", ""),
            "updated_files": [
                {
                    "file_name": f.get("file_name"),
                    "file_content": f.get("file_content"),
                }
                for f in llm_response.get("source_files", [])
                if isinstance(f, dict)
            ],
        }

    def _create_error_response(self, error_msg: str) -> Dict[str, Any]:
        """Create a standardized error response."""
        if self.verbose:
            rprint(f"[red]{error_msg}[/]")
        return {
            "summary": error_msg,
            "updated_files": [],
        }

    def _handle_databricks(
        self,
        prompt: str,
        source_files: Dict[str, str],
        chat_id: Optional[int] = None,
        system_prompt: Optional[str] = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> Optional[Dict[str, Any]]:
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

        messages_json = (
            [{"role": "system", "content": effective_system_prompt}]
            + self.chat_history[conv_id]
            + [{"role": "user", "content": user_message}]
        )
        messages = messages_json
        if self.debug:
            print(">>>>>>>>>>>>>>>>")
            print(self.chat_history[conv_id])
            print(">>>>>>>>>>>>>>>>")

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_output_tokens,
        }

        try:
            with httpx.Client(timeout=LLM_TIMEOUT) as client:
                response = client.post(api_url, json=payload, headers=headers)
                if self.verbose and response.status_code != 200:
                    print(f"Status code: {response.status_code}")
                    print("-----------------")
                    print(response.text)
                    print("-----------------")
                response.raise_for_status()
                result = response.json()
                if result.get("choices") and result["choices"][0].get("message"):
                    raw_response = result["choices"][0]["message"]["content"]
                    generated_json = _extract_json_object(raw_response)
                    generated_text = json.dumps(generated_json)
                    if self.debug:
                        print("-----------------")
                        print(response.text)
                        print("-----------------")
                        print(generated_text)
                        print("-----------------")
                    self.chat_history[conv_id].append({"role": "user", "content": user_message})
                    self.chat_history[conv_id].append({"role": "assistant", "content": generated_text})
                    self._save_history()
                    return self._parse_llm_response(generated_text)
                return self._create_error_response("Failed to get a valid response from the Databricks API")
        except httpx.HTTPStatusError as e:
            traceback.print_exc()
            error_msg = f"DBX API error: {e.response.status_code}"
            try:
                error_detail = e.response.json()
                if "error" in error_detail:
                    if isinstance(error_detail["error"], dict):
                        error_msg += f" - {error_detail['error'].get('message', str(error_detail['error']))}"
                    else:
                        error_msg += f" - {str(error_detail['error'])}"
            except Exception:
                error_msg += f" - {e.response.text[:200]}"
            return self._create_error_response(error_msg)
        except Exception as e:
            traceback.print_exc()
            return self._create_error_response(f"Error calling Databricks API: {str(e)}")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "new_chat":
            root = params.get("root")
            history_file = (
                Path(root) / ".aye" / "chat_history.json"
                if root
                else Path.cwd() / ".aye" / "chat_history.json"
            )
            history_file.unlink(missing_ok=True)
            self.chat_history = {}
            if self.verbose:
                rprint("[yellow]Local model chat history cleared.[/]")
            return {"status": "local_history_cleared"}

        if command_name == "local_model_invoke":
            prompt = params.get("prompt", "").strip()
            model_id = params.get("model_id", "")
            source_files = params.get("source_files", {})
            chat_id = params.get("chat_id")
            root = params.get("root")
            system_prompt = params.get("system_prompt")
            max_output_tokens = params.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)

            self.history_file = (
                Path(root) / ".aye" / "chat_history.json" if root else Path.cwd() / ".aye" / "chat_history.json"
            )
            self._load_history()

            result = self._handle_databricks(prompt, source_files, chat_id, system_prompt, max_output_tokens)
            if result is not None:
                return result

            return None

        return None

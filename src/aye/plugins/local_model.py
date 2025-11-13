import os
import json
from typing import Dict, Any, Optional
import httpx

from rich import print as rprint

from .plugin_base import Plugin


# Shared system prompt for all local model handlers
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
    '    "answer_summary": "string - Detailed answer to a user question",\n'
    '    "source_files": [\n'
    '        {\n'
    '            "file_name": "string - Name of the source file including relative path",\n'
    '            "file_content": "string - Full text/content of the source file"\n'
    '        }\n'
    '    ]\n'
    '}'
)


class LocalModelPlugin(Plugin):
    name = "local_model"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the local model plugin."""
        super().init(cfg)
        if self.verbose:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

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
            # If the response isn't valid JSON, wrap it as a simple response
            llm_response = {
                "answer_summary": generated_text,
                "source_files": []
            }
        
        # Return in the format expected by the REPL
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

    def _handle_openai_compatible(self, prompt: str, source_files: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Handle OpenAI-compatible API calls using AYE_LLM_API_URL, AYE_LLM_API_KEY, and AYE_LLM_MODEL."""
        api_url = os.environ.get("AYE_LLM_API_URL")
        api_key = os.environ.get("AYE_LLM_API_KEY")
        model_name = os.environ.get("AYE_LLM_MODEL", "gpt-3.5-turbo")
        
        # Both API URL and API key environment variables must be set
        if not api_url or not api_key:
            return None
        
        # Build the user message
        user_message = self._build_user_message(prompt, source_files)
        
        # Prepare the OpenAI-compatible API request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.7,
            "max_tokens": 16384,
            "response_format": {"type": "json_object"}
        }
        
        try:
            # Make the API call
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{api_url.rstrip('/')}/v1/chat/completions",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                
                # Parse the OpenAI-format response
                result = response.json()
                
                # Extract the generated content
                if "choices" in result and result["choices"]:
                    choice = result["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        generated_text = choice["message"]["content"]
                        return self._parse_llm_response(generated_text)
                
                # If we couldn't extract a proper response
                return self._create_error_response(
                    "Failed to get a valid response from the OpenAI-compatible API"
                )
                
        except httpx.HTTPStatusError as e:
            error_msg = f"OpenAI API error: {e.response.status_code}"
            try:
                error_detail = e.response.json()
                if "error" in error_detail:
                    error_msg += f" - {error_detail['error'].get('message', str(error_detail['error']))}"
            except:
                error_msg += f" - {e.response.text[:200]}"
            return self._create_error_response(error_msg)
            
        except Exception as e:
            return self._create_error_response(f"Error calling OpenAI-compatible API: {str(e)}")

    def _handle_gemini_pro_25(self, prompt: str, source_files: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Handle Gemini Pro 2.5 model invocation via direct API call."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None

        # Build the user message
        user_message = self._build_user_message(prompt, source_files)

        # Prepare the API request
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key
        }
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_message}]
                }
            ],
            "systemInstruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "generationConfig": {
                "temperature": 0.7,
                "topK": 40,
                "topP": 0.95,
                "maxOutputTokens": 16384,
                "responseMimeType": "application/json"
            }
        }

        try:
            # Make the API call
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                
                # Parse the response
                result = response.json()
                
                # Extract the generated content
                if "candidates" in result and result["candidates"]:
                    candidate = result["candidates"][0]
                    if "content" in candidate and "parts" in candidate["content"]:
                        generated_text = candidate["content"]["parts"][0].get("text", "")
                        return self._parse_llm_response(generated_text)
                
                # If we couldn't extract a proper response
                return self._create_error_response("Failed to get a valid response from Gemini API")
                
        except httpx.HTTPStatusError as e:
            error_msg = f"Gemini API error: {e.response.status_code} - {e.response.text}"
            return self._create_error_response(error_msg)
            
        except Exception as e:
            return self._create_error_response(f"Error calling Gemini API: {str(e)}")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Handle local model invocation.
        Routes to specific model handlers based on model_id or environment variables.
        """
        if command_name == "local_model_invoke":
            prompt = params.get("prompt", "").strip()
            model_id = params.get("model_id", "")
            source_files = params.get("source_files", {})

            # First, check for generic OpenAI-compatible API configuration
            result = self._handle_openai_compatible(prompt, source_files)
            if result is not None:
                return result

            # Then check model ID and route to specific handlers
            if model_id == "google/gemini-2.5-pro":
                return self._handle_gemini_pro_25(prompt, source_files)
            else:
                # Model not handled by this plugin
                return None

        # If the command is not for this plugin, return None.
        return None

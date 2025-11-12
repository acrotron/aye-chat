import os
import json
from typing import Dict, Any, Optional
import httpx

from rich import print as rprint

from .plugin_base import Plugin


class LocalModelPlugin(Plugin):
    name = "local_model"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the local model plugin."""
        super().init(cfg)
        if self.verbose:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

    def _handle_gemini_pro_25(self, prompt: str, source_files: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Handle Gemini Pro 2.5 model invocation via direct API call."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            # Return None to indicate this handler cannot process the request
            # This allows fallback to regular API handling
            return None

        # Prepare the structured request matching the expected schema
        system_prompt = (
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

        # Build the user message with source files if provided
        user_message = prompt
        if source_files:
            user_message += "\n\n--- Source files are below. ---\n"
            for file_name, content in source_files.items():
                user_message += f"\n** {file_name} **\n```\n{content}\n```\n"

        # Prepare the API request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
        #url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
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
                "parts": [{"text": system_prompt}]
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
                        
                        # Parse the JSON response from the model
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
                
                # If we couldn't extract a proper response
                return {
                    "summary": "Failed to get a valid response from Gemini API",
                    "updated_files": []
                }
                
        except httpx.HTTPStatusError as e:
            error_msg = f"Gemini API error: {e.response.status_code} - {e.response.text}"
            if self.verbose:
                rprint(f"[red]{error_msg}[/]")
            return {
                "summary": error_msg,
                "updated_files": []
            }
        except Exception as e:
            error_msg = f"Error calling Gemini API: {str(e)}"
            if self.verbose:
                rprint(f"[red]{error_msg}[/]")
            return {
                "summary": error_msg,
                "updated_files": []
            }

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Handle local model invocation.
        Routes to specific model handlers based on model_id.
        """
        if command_name == "local_model_invoke":
            prompt = params.get("prompt", "").strip()
            model_id = params.get("model_id", "")
            source_files = params.get("source_files", {})

            # Check model ID and route to appropriate handler
            if model_id == "google/gemini-2.5-pro":
                return self._handle_gemini_pro_25(prompt, source_files)
            else:
                # Model not handled by this plugin
                return None

        # If the command is not for this plugin, return None.
        return None

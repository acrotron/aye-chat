import os
import json
import time
from typing import Any, Dict, Optional

import httpx
from .auth import get_token

# -------------------------------------------------
# 👉  EDIT THIS TO POINT TO YOUR SERVICE
# -------------------------------------------------
api_url = os.environ.get("AYE_CHAT_API_URL")
BASE_URL = api_url if api_url else "https://api.ayechat.ai"
TIMEOUT = 900.0
DEBUG = False


def _auth_headers() -> Dict[str, str]:
    token = get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}


def _check_response(resp: httpx.Response) -> Dict[str, Any]:
    """Validate an HTTP response.

    * Raises for non‑2xx status codes.
    * If the response body is JSON and contains an ``error`` key, prints
      the error message and raises ``Exception`` with that message.
    * If parsing JSON fails, falls back to raw text for the error message.
    Returns the parsed JSON payload for successful calls.
    """
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Try to extract a JSON error message, otherwise use text.
        try:
            err_json = resp.json()
            err_msg = err_json.get("error") or resp.text
        except Exception:
            err_msg = resp.text
        print(f"Error: {err_msg}")
        raise Exception(err_msg) from exc

    # Successful status – still check for an error field in the payload.
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        # Not JSON – return empty dict.
        return {}

    if isinstance(payload, dict) and "error" in payload:
        err_msg = payload["error"]
        print(f"Error: {err_msg}")
        raise Exception(err_msg)
    return payload


def cli_invoke(chat_id=-1, message="", source_files={},
               model: Optional[str] = None,
               dry_run: bool = False,
               poll_interval=2.0, poll_timeout=TIMEOUT):
    payload = {"chat_id": chat_id, "message": message, "source_files": source_files, "dry_run": dry_run}
    if model:
        payload["model"] = model
    url = f"{BASE_URL}/invoke_cli"

    if (DEBUG):
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        if (DEBUG): print(f"[DEBUG] Initial response status: {resp.status_code}")
        data = _check_response(resp)
        if (DEBUG): print(f"[DEBUG] Initial response data: {data}")

    # If server already returned the final payload, just return it
    # (previous logic kept for compatibility)
    # if resp.status_code != 202 or "response_url" not in data:
    #     return data

    # Otherwise poll the presigned GET URL until the object exists, then download+return it
    response_url = data["response_url"]
    if (DEBUG): print(f"[DEBUG] Polling response URL: {response_url}")
    deadline = time.time() + poll_timeout
    last_status = None
    poll_count = 0

    while time.time() < deadline:
        try:
            poll_count += 1
            if (DEBUG): print(f"[DEBUG] Poll attempt {poll_count}, status: {last_status}")
            r = httpx.get(response_url, timeout=TIMEOUT)  # default verify=True
            last_status = r.status_code
            if (DEBUG): print(f"[DEBUG] Poll response status: {r.status_code}")
            if r.status_code == 200:
                if (DEBUG): print(f"[DEBUG] Response body length: {len(r.text)} bytes")
                if (DEBUG): print(f"[DEBUG] Response body preview: {r.text[:200]}")
                try:
                    result = r.json()
                    if (DEBUG): print(f"[DEBUG] Successfully parsed JSON response")
                    return result
                except json.JSONDecodeError as e:
                    if (DEBUG): print(f"[DEBUG] JSON decode error: {e}")
                    if (DEBUG): print(f"[DEBUG] Full response text: {r.text}")
                    raise
            if r.status_code in (403, 404):
                time.sleep(poll_interval)
                continue
            r.raise_for_status()  # other non‑2xx errors are unexpected
        except httpx.RequestError as e:
            # transient network issue; retry
            if (DEBUG): print(f"[DEBUG] Network error: {e}")
            time.sleep(poll_interval)
            continue

    raise TimeoutError(f"Timed out waiting for response object from LLM")


def fetch_plugin_manifest(dry_run: bool = False):
    """Fetch the plugin manifest from the server."""
    url = f"{BASE_URL}/plugins"
    payload = {"dry_run": dry_run}

    if (DEBUG):
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        if (DEBUG): print(f"[DEBUG] Response status: {resp.status_code}")
        _check_response(resp)  # will raise on error and print the message
        return resp.json()


def fetch_server_time(dry_run: bool = False) -> int:
    """Fetch the current server timestamp."""
    url = f"{BASE_URL}/time"
    params = {"dry_run": dry_run}

    if (DEBUG):
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Query params: {json.dumps(params, indent=2)}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.get(url, params=params)
        if (DEBUG): print(f"[DEBUG] Response status: {resp.status_code}")
        if not resp.ok:
            # Use the same helper for consistency but avoid raising for 200‑like cases
            try:
                _check_response(resp)
            except Exception:
                # _check_response already printed the error; re‑raise
                raise
        else:
            # Successful response – still ensure no embedded error field
            payload = _check_response(resp)
            return payload['timestamp']

def send_feedback(feedback_text: str, chat_id: int = 0):
    """Send user feedback to the feedback endpoint.
    Includes the current chat ID (or 0 if not available).
    """
    url = f"{BASE_URL}/feedback"
    payload = {"feedback": feedback_text, "chat_id": chat_id}

    if (DEBUG):
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")

    try:
        with httpx.Client(timeout=10.0, verify=True) as client:
            # Fire-and-forget call. Errors are ignored to not block exit.
            resp = client.post(url, json=payload, headers=_auth_headers())
            if (DEBUG): print(f"[DEBUG] Response status: {resp.status_code}")
    except Exception as e:
        # Silently ignore all errors, but log in debug mode.
        if (DEBUG): print(f"[DEBUG] Error sending feedback: {e}")
        pass

def test_json_parsing_issue():
    """
    Test driver to demonstrate the JSON parsing issue.
    This function simulates the logic in llm_invoker.py that causes the error.
    """
    print("--- Running Test Driver for JSON Parsing Issue ---")

    # This is the full response string received from the API. Using a raw, triple-quoted
    # string ensures that backslashes are preserved for the JSON parser.
    full_api_response_str = r'''
    {"user_id": "v@acrotron.com", "chat_id": 125, "chat_title": "Add Generic OpenAI-style Local Model", "assistant_response": "To test the local OpenAI-compatible invocation, you have two options:\n\n## Option 1: Use OpenRouter (Recommended)\n\nOpenRouter provides an OpenAI-compatible API endpoint. Set these environment variables:\n\n```bash\nexport AYE_LLM_API_URL=\"https://openrouter.ai/api\"\nexport AYE_LLM_API_KEY=\"your-openrouter-token\"\nexport AYE_LLM_MODEL=\"google/gemini-2.0-flash-exp:free\"  # Or any model from OpenRouter\n```\n\nThen run `aye chat` - it will use OpenRouter instead of the default API.\n\n## Option 2: Use a Mock Server for Testing\n\nCreate a simple test server that mimics OpenAI's API:\n\n```python\n# test_server.py\nfrom flask import Flask, request, jsonify\nimport json\n\napp = Flask(__name__)\n\n@app.route('/v1/chat/completions', methods=['POST'])\ndef chat_completions():\n    data = request.json\n    \n    # Extract the user message\n    user_msg = data['messages'][-1]['content']\n    \n    # Create a mock response\n    mock_response = {\n        \"answer_summary\": f\"Mock response to: {user_msg[:50]}...\",\n        \"source_files\": []\n    }\n    \n    return jsonify({\n        \"choices\": [{\n            \"message\": {\n                \"content\": json.dumps(mock_response)\n            }\n        }]\n    })\n\nif __name__ == '__main__':\n    app.run(port=8080)\n```\n\nThen set:\n```bash\nexport AYE_LLM_API_URL=\"http://localhost:8080\"\nexport AYE_LLM_API_KEY=\"test-key\"\nexport AYE_LLM_MODEL=\"test-model\"\n```\n\n## Option 3: Use Gemini Directly (Already Working)\n\nSince you have a Gemini API key, the local model plugin already supports it:\n\n```bash\nexport GEMINI_API_KEY=\"your-gemini-api-key\"\naye chat\n# Then select \"Google: Gemini 2.5 Pro\" model\n```\n\nThis will use the local Gemini handler instead of going through the main API.\n\n## Testing Verification\n\nTo verify which path is being used, temporarily add debug output to `llm_invoker.py`:\n\n```python\n# In invoke_llm function, after local_response check:\nif local_response is not None:\n    rprint(\"[cyan]Using local model handler[/]\")\n    # ... existing code\nelse:\n    rprint(\"[cyan]Using API handler[/]\")\n    # ... existing code\n```\n\nThe OpenRouter option is the most practical for testing since it's a real service that supports the OpenAI format and you already have a token for it.", "created_at": "2025-11-12 23:33:50+00:00", "updated_at": "2025-11-13 00:04:33+00:00"}
    '''

    # 1. The httpx library first parses the entire response into a dictionary.
    # This step succeeds because the outer string is valid JSON.
    resp_dict = json.loads(full_api_response_str)
    print("Step 1: Successfully parsed the full API response into a Python dictionary.")

    # 2. The code in `llm_invoker.py` then extracts the 'assistant_response' field.
    assistant_resp_str = resp_dict.get('assistant_response')
    print("\nStep 2: Extracted the 'assistant_response' field. Its content is:")
    print("--------------------------------------------------")
    print(assistant_resp_str)
    print("--------------------------------------------------")

    # 3. The code then attempts to parse THIS extracted string as JSON.
    # This is where the error occurs.
    print("\nStep 3: Attempting to run `json.loads()` on the extracted string...")
    try:
        json.loads(assistant_resp_str)
        print("[green]This should not have succeeded.[/green]")
    except json.JSONDecodeError as e:
        print(f"\n[bold red]SUCCESSFULLY REPRODUCED ERROR:[/] {e}")
        print("\n--- EXPLANATION ---")
        print("The error 'Expecting value: line 1 column 1 (char 0)' occurs because the string being parsed is not valid JSON.")
        print("The `llm_invoker.py` script expects the 'assistant_response' field to contain a JSON-formatted string, like:")
        print("  '{\"answer_summary\": \"...\", \"source_files\": []}'")
        print("Instead, it contains plain text that starts with 'To test the local...'.")
        print("The JSON parser sees the 'T' at the beginning and immediately fails, as it's not a valid start for a JSON document (which must be {, [, \", number, true, false, or null).")

def main():
    """
    Driver to test the JSON parsing issue.
    """
    test_json_parsing_issue()


if __name__ == '__main__':
    main()

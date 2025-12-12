"""API client for the Aye chat service.

Provides functions to invoke the LLM, fetch plugin manifests, and send feedback.
"""
import json
import os
import time
from typing import Any, Dict, Optional

import httpx
from rich import print as rprint

from aye.model.auth import get_token, get_user_config

# -------------------------------------------------
# 👉  EDIT THIS TO POINT TO YOUR SERVICE
# -------------------------------------------------
api_url = os.environ.get("AYE_CHAT_API_URL")

if api_url:
    rprint(f"[bold cyan]Using custom AYE_CHAT_API_URL: {api_url}[/bold cyan]")

BASE_URL = api_url if api_url else "https://api.ayechat.ai"
TIMEOUT = 900.0

def _is_debug():
    return get_user_config("debug", "off").lower() == "on"


def _auth_headers() -> Dict[str, str]:
    token = get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}


def _check_response(resp: httpx.Response) -> Dict[str, Any]:
    """Validate an HTTP response.

    * Raises for non-2xx status codes.
    * If the response body is JSON and contains an ``error`` key, prints
      the error message and raises ``RuntimeError`` with that message.
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
        except (json.JSONDecodeError, ValueError):
            err_msg = resp.text
        print(f"Error: {err_msg}")
        raise RuntimeError(err_msg) from exc

    # Successful status - still check for an error field in the payload.
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        # Not JSON - return empty dict.
        return {}

    if isinstance(payload, dict) and "error" in payload:
        err_msg = payload["error"]
        print(f"Error: {err_msg}")
        raise RuntimeError(err_msg)
    return payload


def cli_invoke(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
    chat_id: int = -1,
    message: str = "",
    source_files: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    dry_run: bool = False,
    poll_interval: float = 2.0,
    poll_timeout: float = TIMEOUT
) -> Dict[str, Any]:
    """Invoke the CLI endpoint to send a message to the LLM.

    Args:
        chat_id: The chat session ID (-1 for new chat)
        message: The user message to send
        source_files: Dictionary of source files to include
        model: Optional model ID to use
        system_prompt: Optional custom system prompt
        dry_run: If True, don't actually invoke the LLM
        poll_interval: Seconds between polling attempts
        poll_timeout: Maximum seconds to wait for response

    Returns:
        The LLM response as a dictionary
    """
    if source_files is None:
        source_files = {}

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message": message,
        "source_files": source_files,
        "dry_run": dry_run
    }
    if model:
        payload["model"] = model
    if system_prompt:
        payload["system_prompt"] = system_prompt
    url = f"{BASE_URL}/invoke_cli"

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print("[DEBUG] Headers: {'Authorization': 'Bearer <token>'}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        if _is_debug():
            print(f"[DEBUG] Initial response status: {resp.status_code}")
        data = _check_response(resp)
        if _is_debug():
            print(f"[DEBUG] Initial response data: {data}")

    # Poll the presigned GET URL until the object exists, then download+return it
    response_url = data["response_url"]
    if _is_debug():
        print(f"[DEBUG] Polling response URL: {response_url}")
    deadline = time.time() + poll_timeout
    last_status = None
    poll_count = 0

    while time.time() < deadline:
        try:
            poll_count += 1
            if _is_debug():
                print(f"[DEBUG] Poll attempt {poll_count}, status: {last_status}")
            r = httpx.get(response_url, timeout=TIMEOUT)
            last_status = r.status_code
            if _is_debug():
                print(f"[DEBUG] Poll response status: {r.status_code}")
            if r.status_code == 200:
                if _is_debug():
                    print(f"[DEBUG] Response body length: {len(r.text)} bytes")
                    print(f"[DEBUG] Response body preview: {r.text[:200]}")
                try:
                    result = r.json()
                    if _is_debug():
                        print("[DEBUG] Successfully parsed JSON response")
                    return result
                except json.JSONDecodeError as e:
                    if _is_debug():
                        print(f"[DEBUG] JSON decode error: {e}")
                        print(f"[DEBUG] Full response text: {r.text}")
                    raise
            if r.status_code in (403, 404):
                time.sleep(poll_interval)
                continue
            r.raise_for_status()
        except httpx.RequestError as e:
            # transient network issue; retry
            if _is_debug():
                print(f"[DEBUG] Network error: {e}")
            time.sleep(poll_interval)
            continue

    raise TimeoutError("Timed out waiting for response object from LLM")


def fetch_plugin_manifest(dry_run: bool = False) -> Dict[str, Any]:
    """Fetch the plugin manifest from the server."""
    url = f"{BASE_URL}/plugins"
    payload = {"dry_run": dry_run}

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print("[DEBUG] Headers: {'Authorization': 'Bearer <token>'}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        if _is_debug():
            print(f"[DEBUG] Response status: {resp.status_code}")
        _check_response(resp)  # will raise on error and print the message
        return resp.json()


def fetch_server_time(dry_run: bool = False) -> int:
    """Fetch the current server timestamp."""
    url = f"{BASE_URL}/time"
    params = {"dry_run": dry_run}

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Query params: {json.dumps(params, indent=2)}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.get(url, params=params)
        if _is_debug():
            print(f"[DEBUG] Response status: {resp.status_code}")
        # _check_response will raise on error
        payload = _check_response(resp)
        return payload['timestamp']

def send_feedback(feedback_text: str, chat_id: int = 0) -> None:
    """Send user feedback to the feedback endpoint.

    Includes the current chat ID (or 0 if not available).
    """
    url = f"{BASE_URL}/feedback"
    payload = {"feedback": feedback_text, "chat_id": chat_id}

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print("[DEBUG] Headers: {'Authorization': 'Bearer <token>'}")

    try:
        with httpx.Client(timeout=10.0, verify=True) as client:
            # Fire-and-forget call. Errors are ignored to not block exit.
            resp = client.post(url, json=payload, headers=_auth_headers())
            if _is_debug():
                print(f"[DEBUG] Response status: {resp.status_code}")
    except (httpx.RequestError, RuntimeError) as e:
        # Silently ignore all errors, but log in debug mode.
        if _is_debug():
            print(f"[DEBUG] Error sending feedback: {e}")

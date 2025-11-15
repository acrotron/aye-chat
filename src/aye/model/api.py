import os
import json
import time
from typing import Any, Dict, Optional

import httpx
from aye.model.auth import get_token

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

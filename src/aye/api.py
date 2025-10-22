import os
import json
import time
from typing import Any, Dict

import httpx
from .auth import get_token

# -------------------------------------------------
# 👉  EDIT THIS TO POINT TO YOUR SERVICE
# -------------------------------------------------
api_url = os.environ.get("AYE_CHAT_API_URL")
BASE_URL = api_url if api_url else "https://api.ayechat.ai"
TIMEOUT = 300.0


def _auth_headers() -> Dict[str, str]:
    token = get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}


def cli_invoke(chat_id=-1, message="", source_files={},
               model: str | None = None,
               poll_interval=2.0, poll_timeout=120):
    payload = {"chat_id": chat_id, "message": message, "source_files": source_files}
    if model:
        payload["model"] = model
    url = f"{BASE_URL}/invoke_cli"

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        payload = resp.json()
        error_text = payload.get("error")
        if error_text:
            if "Reason: Message must be shorter than" in error_text:
                error_text = "Source code base is too large. Switch to a subfolder and try to run `aye chat` again."
            raise Exception(error_text)
        resp.raise_for_status()
        data = resp.json()

    # If server already returned the final payload, just return it
    #if resp.status_code != 202 or "response_url" not in data:
    #    return data

    # Otherwise poll the presigned GET URL until the object exists, then download+return it
    response_url = data["response_url"]
    deadline = time.time() + poll_timeout
    last_status = None

    while time.time() < deadline:
        try:
            r = httpx.get(response_url, timeout=TIMEOUT)  # default verify=True
            last_status = r.status_code
            if r.status_code == 200:
                print(r.json())
                return r.json()  # same shape as original resp.json()
            if r.status_code in (403, 404):
                time.sleep(poll_interval)
                continue
            r.raise_for_status()  # other non-2xx errors are unexpected
        except httpx.RequestError:
            # transient network issue; retry
            time.sleep(poll_interval)
            continue

    raise TimeoutError(f"Timed out waiting for response object from LLM")


def fetch_plugin_manifest():
    """Fetch the plugin manifest from the server."""
    url = f"{BASE_URL}/plugins"
    
    # Enforce SSL verification for security
    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


def fetch_server_time() -> int:
    """Fetch the current server timestamp."""
    url = f"{BASE_URL}/time"
    
    # Enforce SSL verification for security
    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()['timestamp']

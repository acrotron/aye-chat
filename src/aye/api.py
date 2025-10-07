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
TIMEOUT = 120.0


def _auth_headers() -> Dict[str, str]:
    token = get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}


def cli_invoke_simple(user_id="v@acrotron.com", chat_id=-1, message="", source_files={}):
    payload = {"user_id": user_id, "chat_id": chat_id, "message": message, "source_files": source_files}

    url = f"{BASE_URL}/invoke_cli"

    with httpx.Client(timeout=TIMEOUT, verify=False) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        #print(resp.text)
        return resp.json()


def cli_invoke(user_id="v@acrotron.com", chat_id=-1, message="", source_files={},
               poll_interval=2.0, poll_timeout=120):
    payload = {"user_id": user_id, "chat_id": chat_id, "message": message, "source_files": source_files}
    url = f"{BASE_URL}/invoke_cli"

    with httpx.Client(timeout=TIMEOUT, verify=False) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        #print(resp.text)
        data = resp.json()

    # If server already returned the final payload, just return it
    #if resp.status_code != 202 or "response_url" not in data:
    #    return data

    # Otherwise poll the presigned GET URL until the object exists, then download+return it
    response_url = json.loads(data["body"])["response_url"]
    deadline = time.time() + poll_timeout
    last_status = None

    while time.time() < deadline:
        try:
            r = httpx.get(response_url, timeout=TIMEOUT)  # default verify=True
            last_status = r.status_code
            if r.status_code == 200:
                return r.json()  # same shape as original resp.json()
            if r.status_code in (403, 404):
                time.sleep(poll_interval)
                continue
            r.raise_for_status()  # other non-2xx errors are unexpected
        except httpx.RequestError:
            # transient network issue; retry
            time.sleep(poll_interval)
            continue

    raise TimeoutError(f"Timed out waiting for response object from LLM (last status={last_status})")


def fetch_plugin_manifest():
    """Fetch the plugin manifest from the server."""
    url = f"{BASE_URL}/plugins"
    
    with httpx.Client(timeout=TIMEOUT, verify=False) as client:
        resp = client.post(url, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


def fetch_server_time() -> int:
    """Fetch the current server timestamp."""
    url = f"{BASE_URL}/time"
    
    with httpx.Client(timeout=TIMEOUT, verify=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()['timestamp']

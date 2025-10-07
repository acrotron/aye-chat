import os
import json
from typing import Any, Dict

import httpx
from .auth import get_token

# -------------------------------------------------
# 👉  EDIT THIS TO POINT TO YOUR SERVICE
# -------------------------------------------------
api_url = os.environ.get("AYE_CHAT_API_URL")
BASE_URL = api_url if api_url else "https://api.ayechat.ai"
TIMEOUT = 30.0


def _auth_headers() -> Dict[str, str]:
    token = get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}


def cli_invoke(user_id="v@acrotron.com", chat_id=-1, message="", source_files={}):
    payload = {"user_id": user_id, "chat_id": chat_id, "message": message, "source_files": source_files}

    url = f"{BASE_URL}/invoke_cli"

    with httpx.Client(timeout=TIMEOUT, verify=False) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        #print(resp.text)
        return resp.json()


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

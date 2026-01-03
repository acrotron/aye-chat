import os
import json
import time
import sys
from typing import Any, Dict, Optional, Callable
from rich import print as rprint

import httpx
from aye.model.auth import get_token, get_user_config
from aye.model.config import DEFAULT_MAX_OUTPUT_TOKENS

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


def _is_stream_debug():
    """Check if streaming debug mode is enabled via environment variable."""
    return os.environ.get("AYE_STREAM_DEBUG", "").lower() in ("1", "true", "on")


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


def _extract_answer_summary_from_assistant_response(resp: Dict[str, Any]) -> str:
    """Best-effort extraction of answer_summary from the final response payload."""
    assistant_resp_str = resp.get("assistant_response")
    if assistant_resp_str is None:
        return ""

    # assistant_response is expected to be a JSON string.
    if isinstance(assistant_resp_str, (dict, list)):
        try:
            # If backend ever switches to embedding the JSON directly
            if isinstance(assistant_resp_str, dict):
                return str(assistant_resp_str.get("answer_summary", ""))
            return ""
        except Exception:
            return ""

    try:
        parsed = json.loads(assistant_resp_str)
        if isinstance(parsed, dict):
            return str(parsed.get("answer_summary", ""))
    except Exception:
        return ""

    return ""


def _call_stream_update(on_stream_update: Optional[Callable[..., None]], content: str, *, is_final: bool) -> None:
    """Call the provided streaming callback.

    Backwards compatible:
    - Prefer calling with `is_final` keyword (new API)
    - Fall back to positional, then to legacy single-arg callbacks.
    """
    if on_stream_update is None:
        return

    try:
        on_stream_update(content, is_final=is_final)
        return
    except TypeError:
        pass

    try:
        on_stream_update(content, is_final)
        return
    except TypeError:
        pass

    on_stream_update(content)



def cli_invoke(
    chat_id=-1,
    message="",
    source_files={},
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    dry_run: bool = False,
    telemetry: Optional[Dict[str, Any]] = None,
    poll_interval=2.0,
    poll_timeout=TIMEOUT,
    on_stream_update: Optional[Callable[..., None]] = None,
):
    """
    Invoke the CLI API endpoint.

    Args:
        chat_id: The chat session ID (-1 for new chat)
        message: The user's message/prompt
        source_files: Dictionary of filename -> content
        model: Model ID to use
        system_prompt: Custom system prompt
        max_output_tokens: Maximum tokens in response
        dry_run: If True, don't actually invoke
        telemetry: Optional telemetry data to piggyback
        poll_interval: Seconds between polling attempts
        poll_timeout: Maximum seconds to wait for response
        on_stream_update: Optional callback for streaming updates.
                          Called with the current partial content string.
                          If the callback supports it, it will additionally
                          receive `is_final=True` when the final response is ready.

    Returns:
        The API response dictionary
    """
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message": message,
        "source_files": source_files,
        "dry_run": dry_run,
        # Flag for streaming support
        "streaming": True
    }
    if model:
        payload["model"] = model
    if system_prompt:
        payload["system_prompt"] = system_prompt
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens

    # Piggyback telemetry to avoid extra HTTP calls.
    if telemetry is not None:
        payload["telemetry"] = telemetry

    url = f"{BASE_URL}/invoke_cli"

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        if _is_debug():
            print(f"[DEBUG] Initial response status: {resp.status_code}")
        data = _check_response(resp)
        if _is_debug():
            print(f"[DEBUG] Initial response data: {data}")

    # Poll the presigned GET URL until the object exists
    response_url = data["response_url"]
    if _is_debug():
        print(f"[DEBUG] Polling response URL: {response_url}")

    deadline = time.time() + poll_timeout
    last_status = None
    poll_count = 0

    # Streaming state
    streamed_content = ""
    has_streamed = False

    # Faster polling while streaming is active
    streaming_poll_interval = min(poll_interval, 0.25)

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
                except json.JSONDecodeError as e:
                    if _is_debug():
                        print(f"[DEBUG] JSON decode error while polling: {e}")
                        print(f"[DEBUG] Full response text: {r.text[:200]}")
                    time.sleep(streaming_poll_interval if has_streamed else poll_interval)
                    continue

                if _is_debug():
                    print(f"[DEBUG] Successfully parsed JSON response")

                # --- Streaming support ---
                if isinstance(result, dict) and result.get("streaming") is True:
                    partial = result.get("partial_content")
                    if isinstance(partial, str) and partial:
                        # Debug: show the raw partial content on first receipt
                        if _is_stream_debug() and not streamed_content:
                            print(f"\n[STREAM_DEBUG] First partial_content repr: {repr(partial[:200])}...\n", file=sys.stderr)

                        # Check if content has changed
                        if partial != streamed_content:
                            streamed_content = partial
                            has_streamed = True

                            # Call the streaming callback if provided
                            _call_stream_update(on_stream_update, streamed_content, is_final=False)

                    # Keep polling for updates until streaming becomes false
                    time.sleep(streaming_poll_interval)
                    continue

                # Final response reached
                if has_streamed:
                    # IMPORTANT: as soon as final response is ready, force a final render.
                    # This allows the UI layer to stop any per-word animation immediately.
                    final_summary = _extract_answer_summary_from_assistant_response(result)
                    final_to_render = final_summary or streamed_content

                    _call_stream_update(on_stream_update, final_to_render, is_final=True)

                    # Mark so upstream can avoid printing the summary twice
                    result["_streamed_summary"] = True

                return result

            if r.status_code in (403, 404):
                time.sleep(streaming_poll_interval if has_streamed else poll_interval)
                continue

            r.raise_for_status()

        except httpx.RequestError as e:
            if _is_debug():
                print(f"[DEBUG] Network error: {e}")
            time.sleep(streaming_poll_interval if has_streamed else poll_interval)
            continue

    raise TimeoutError(f"Timed out waiting for response object from LLM")



def fetch_plugin_manifest(dry_run: bool = False):
    """Fetch the plugin manifest from the server."""
    url = f"{BASE_URL}/plugins"
    payload = {"dry_run": dry_run}

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")

    with httpx.Client(timeout=TIMEOUT, verify=True) as client:
        resp = client.post(url, json=payload, headers=_auth_headers())
        if _is_debug():
            print(f"[DEBUG] Response status: {resp.status_code}")
        _check_response(resp)
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
        if not resp.ok:
            try:
                _check_response(resp)
            except Exception:
                raise
        else:
            payload = _check_response(resp)
            return payload['timestamp']



def send_feedback(feedback_text: str, chat_id: int = 0, telemetry: Optional[Dict[str, Any]] = None):
    """Send user feedback to the feedback endpoint.
    Includes the current chat ID (or 0 if not available).

    Telemetry is piggybacked here as well (if provided), so we can send telemetry
    on exit without introducing extra network calls.
    """
    url = f"{BASE_URL}/feedback"
    payload: Dict[str, Any] = {"feedback": feedback_text, "chat_id": chat_id}

    if telemetry is not None:
        payload["telemetry"] = telemetry

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")

    try:
        with httpx.Client(timeout=10.0, verify=True) as client:
            resp = client.post(url, json=payload, headers=_auth_headers())
            if _is_debug():
                print(f"[DEBUG] Response status: {resp.status_code}")
    except Exception as e:
        if _is_debug():
            print(f"[DEBUG] Error sending feedback: {e}")
        pass

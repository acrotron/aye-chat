import os
import json
import time
import sys
from typing import Any, Dict, List, Optional, Callable
from rich import print as rprint

import httpx
from aye.model.auth import get_token, get_user_config, refresh_demo_token, DemoTokenError
from aye.model.config import DEFAULT_MAX_OUTPUT_TOKENS


class ApiError(Exception):
    """API error with HTTP status code context for actionable error messages."""

    def __init__(self, message: str, status_code: Optional[int] = None, error_code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class InvalidDemoTokenError(ApiError):
    """Raised when the server returns INVALID_DEMO_TOKEN error."""
    pass


# -------------------------------------------------
# 
#  EDIT THIS TO POINT TO YOUR SERVICE
# -------------------------------------------------
api_url = os.environ.get("AYE_CHAT_API_URL")

if api_url:
    rprint(f"[bold cyan]Using custom AYE_CHAT_API_URL: {api_url}[/bold cyan]")

BASE_URL = api_url if api_url else "https://api.ayechat.ai"
TIMEOUT = 900.0

# Per-request ceiling for a single poll of the presigned response URL.
#
# The poll loop's own deadline is TIMEOUT, so reusing TIMEOUT here let one
# hung GET consume the entire budget and fail the request outright instead of
# retrying. A poll is a small object fetch: if it has not answered in this
# long, dropping it and re-polling is strictly better than waiting.
POLL_REQUEST_TIMEOUT = 30.0


def _is_debug():
    return get_user_config("debug", "off").lower() == "on"


def _is_stream_debug():
    """Check if streaming debug mode is enabled via environment variable."""
    return os.environ.get("AYE_STREAM_DEBUG", "").lower() in ("1", "true", "on")


def _ssl_verify() -> bool:
    """Undocumented: control TLS certificate verification for API calls.

    Sources (in priority order):
      1) env var AYE_SSLVERIFY (via get_user_config)
      2) ~/.ayecfg [default] sslverify=on|off

    Defaults to True.
    """
    raw = get_user_config("sslverify", "on")
    val = str(raw).strip().lower()

    if val in ("0", "false", "off", "no"):
        return False
    if val in ("1", "true", "on", "yes"):
        return True

    # Be conservative: default to verify enabled.
    return True


def _auth_headers(token_override: Optional[str] = None) -> Dict[str, str]:
    """Build authorization headers.

    Args:
        token_override: If provided, use this token instead of the stored one.
            This is used during login before the new token is persisted.

    Raises:
        DemoTokenError: If no token is available and demo token acquisition fails.
    """
    token = token_override or get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}


def _redact_payload_for_debug(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of ``payload`` safe for debug logging.

    Image bytes (``data_b64``) must never be printed or logged
    (see issue.md Section 8). This helper replaces ``data_b64`` in each
    attachment with a short placeholder while preserving all other
    metadata so the developer can still see attachment shape and counts.
    """
    if not isinstance(payload, dict):
        return payload

    attachments = payload.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return payload

    redacted_attachments: List[Dict[str, Any]] = []
    for att in attachments:
        if isinstance(att, dict):
            copy = dict(att)
            if "data_b64" in copy:
                original = copy["data_b64"]
                length = len(original) if isinstance(original, str) else 0
                copy["data_b64"] = f"<redacted: {length} chars>"
            redacted_attachments.append(copy)
        else:
            redacted_attachments.append(att)

    redacted = dict(payload)
    redacted["attachments"] = redacted_attachments
    return redacted


def _check_response(resp: httpx.Response) -> Dict[str, Any]:
    """Validate an HTTP response.

    * Raises for non‑2xx status codes.
    * If the response body is JSON and contains an ``error`` key, prints
      the error message and raises ``ApiError`` with that message.
    * Detects INVALID_DEMO_TOKEN and raises InvalidDemoTokenError.
    * If parsing JSON fails, falls back to raw text for the error message.
    Returns the parsed JSON payload for successful calls.
    """
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = resp.status_code
        # Try to extract a JSON error message, otherwise use text.
        error_code = None
        try:
            err_json = resp.json()
            err_msg = err_json.get("error") or resp.text
            error_code = err_json.get("error_code") or err_json.get("code")
        except Exception:
            err_msg = resp.text

        # Check for INVALID_DEMO_TOKEN error
        if error_code == "INVALID_DEMO_TOKEN" or "INVALID_DEMO_TOKEN" in err_msg:
            raise InvalidDemoTokenError(err_msg, status_code=status, error_code="INVALID_DEMO_TOKEN") from exc

        raise ApiError(err_msg, status_code=status, error_code=error_code) from exc

    # Successful status – still check for an error field in the payload.
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        # Not JSON – return empty dict.
        return {}

    if isinstance(payload, dict) and "error" in payload:
        err_msg = payload["error"]
        error_code = payload.get("error_code") or payload.get("code")

        if error_code == "INVALID_DEMO_TOKEN" or "INVALID_DEMO_TOKEN" in err_msg:
            raise InvalidDemoTokenError(err_msg, error_code="INVALID_DEMO_TOKEN")

        raise ApiError(err_msg, error_code=error_code)
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


def _strip_streaming_json_fence(content: str) -> str:
    """Remove a leading Markdown JSON fence from streamed content if present."""
    text = (content or "").lstrip()
    if not text.startswith("```"):
        return text

    first_newline = text.find("\n")
    if first_newline == -1:
        return text

    fence_header = text[:first_newline].strip().lower()
    if fence_header not in {"```", "```json"}:
        return text

    body = text[first_newline + 1:]
    closing = body.rfind("```")
    if closing != -1:
        body = body[:closing]
    return body.lstrip()


def _loads_json_object(value: Any) -> Optional[Dict[str, Any]]:
    """Return a dict parsed from *value* when possible, otherwise None."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = json.loads(_strip_streaming_json_fence(value))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None

    return parsed if isinstance(parsed, dict) else None


def _extract_tool_calls_from_payload(payload: Any) -> Any:
    """Return tool_calls/tool_call from a parsed assistant payload, if present."""
    parsed = _loads_json_object(payload)
    if not parsed:
        return None
    return parsed.get("tool_calls") or parsed.get("tool_call")


def _extract_tool_calls_from_response(resp: Dict[str, Any]) -> Any:
    """Best-effort extraction of tool calls from the final response payload."""
    if not isinstance(resp, dict):
        return None

    direct = resp.get("tool_calls") or resp.get("tool_call")
    if direct:
        return direct

    return _extract_tool_calls_from_payload(resp.get("assistant_response"))


def _normalize_tool_calls_into_assistant_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize top-level tool calls into assistant_response for downstream parsing.

    Some backend/model paths can return ``tool_calls`` beside
    ``assistant_response`` instead of inside it. The controller parses tool
    calls from ``assistant_response``, so preserve the final result shape it
    expects before returning from the API layer.
    """
    if not isinstance(resp, dict):
        return resp

    tool_calls = resp.get("tool_calls") or resp.get("tool_call")
    if not tool_calls:
        return resp

    assistant_payload = _loads_json_object(resp.get("assistant_response")) or {}
    assistant_payload.setdefault("answer_summary", "")
    assistant_payload.setdefault("source_files", [])
    assistant_payload["tool_calls"] = tool_calls

    normalized = dict(resp)
    normalized["assistant_response"] = json.dumps(assistant_payload)
    return normalized


def _stream_content_looks_like_protocol_json(content: str) -> bool:
    """True for streamed protocol JSON that should not be rendered live.

    The backend can stream the assistant protocol object itself while the final
    tool-call round is still being produced. Rendering those partial chunks is
    what causes raw ``{"tool_calls": ...}`` JSON to flash in the terminal.

    We suppress JSON-shaped partial streams here, then either render the final
    answer summary or skip rendering entirely when the final payload is a tool
    call. This may disable live streaming for answers that intentionally begin
    with a raw JSON object, but it avoids exposing protocol/tool JSON to users.
    """
    text = _strip_streaming_json_fence(content).lstrip()
    return text.startswith("{")


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
    attachments: Optional[List[Dict[str, Any]]] = None,
    _retry_on_invalid_demo: bool = True,
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
        attachments: Optional list of image attachment dicts. When non-empty,
                     each dict must contain the keys ``file_name``,
                     ``mime_type``, ``data_b64``, and ``bytes_size``
                     (see issue.md Section 4). When None or empty, the
                     request body is unchanged from the text-only case.
        _retry_on_invalid_demo: Internal flag to prevent infinite retry loops.

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

    # Image attachments: include only when non-empty so text-only requests
    # produce the same body as before this change.
    if attachments:
        payload["attachments"] = attachments

    url = f"{BASE_URL}/invoke_cli"

    if _is_debug():
        debug_payload = _redact_payload_for_debug(payload)
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(debug_payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")

    verify = _ssl_verify()

    try:
        with httpx.Client(timeout=TIMEOUT, verify=verify) as client:
            resp = client.post(url, json=payload, headers=_auth_headers())
            if _is_debug():
                print(f"[DEBUG] Initial response status: {resp.status_code}")
            data = _check_response(resp)
            if _is_debug():
                print(f"[DEBUG] Initial response data: {data}")

    except InvalidDemoTokenError:
        # Handle INVALID_DEMO_TOKEN: refresh token and retry once
        if _retry_on_invalid_demo:
            if _is_debug():
                print("[DEBUG] Received INVALID_DEMO_TOKEN, attempting to refresh...")
            new_token = refresh_demo_token()
            if new_token:
                if _is_debug():
                    print("[DEBUG] Demo token refreshed, retrying request...")
                # Retry the request with the new token (but don't retry again)
                return cli_invoke(
                    chat_id=chat_id,
                    message=message,
                    source_files=source_files,
                    model=model,
                    system_prompt=system_prompt,
                    max_output_tokens=max_output_tokens,
                    dry_run=dry_run,
                    telemetry=telemetry,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    on_stream_update=on_stream_update,
                    attachments=attachments,
                    _retry_on_invalid_demo=False,
                )
        # If retry is disabled or refresh failed, re-raise
        raise ApiError(
            "Your demo session has expired. Please run 'aye auth login' to continue.",
            status_code=401,
            error_code="INVALID_DEMO_TOKEN"
        )

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
    has_rendered_stream = False

    # Faster polling while streaming is active
    streaming_poll_interval = min(poll_interval, 0.25)

    while time.time() < deadline:
        try:
            poll_count += 1
            if _is_debug():
                print(f"[DEBUG] Poll attempt {poll_count}, status: {last_status}")
            r = httpx.get(response_url, timeout=POLL_REQUEST_TIMEOUT, verify=verify)
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

                            # Do not render streamed protocol JSON. Tool-call rounds
                            # arrive this way, and rendering partial JSON is what
                            # causes the raw tool_calls object to flash in the UI.
                            if not _stream_content_looks_like_protocol_json(streamed_content):
                                _call_stream_update(on_stream_update, streamed_content, is_final=False)
                                has_rendered_stream = True

                    # Keep polling for updates until streaming becomes false
                    time.sleep(streaming_poll_interval)
                    continue

                # Final response reached
                if isinstance(result, dict):
                    result = _normalize_tool_calls_into_assistant_response(result)

                final_tool_calls = _extract_tool_calls_from_response(result) if isinstance(result, dict) else None

                if has_streamed:
                    if final_tool_calls:
                        # This streamed round was a tool request, not user-facing
                        # assistant prose. Do not send a final render to the
                        # streaming UI, otherwise raw tool JSON may be printed.
                        result["_streamed_summary"] = False
                    else:
                        # IMPORTANT: as soon as final response is ready, force a final render.
                        # This allows the UI layer to stop any per-word animation immediately.
                        final_summary = _extract_answer_summary_from_assistant_response(result)
                        final_to_render = final_summary or streamed_content

                        if final_to_render:
                            _call_stream_update(on_stream_update, final_to_render, is_final=True)
                            # Mark so upstream can avoid printing the summary twice
                            result["_streamed_summary"] = True
                        else:
                            result["_streamed_summary"] = has_rendered_stream

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



def fetch_plugin_manifest(
    dry_run: bool = False,
    *,
    token_override: Optional[str] = None,
    previous_token: Optional[str] = None,
):
    """Fetch the plugin manifest from the server.

    Args:
        dry_run: If True, request a dry-run manifest.
        token_override: Optional token to use in the Authorization header.
            This is used during login before the new token is stored locally.
        previous_token: Optional previously configured token. Sent to the
            backend so it can associate the replacement with the same user.
    """
    url = f"{BASE_URL}/plugins"
    payload: Dict[str, Any] = {"dry_run": dry_run}

    # Include previous_token only when it is present and different from
    # the token being used to authenticate.
    if previous_token and previous_token != token_override:
        payload["previous_token"] = previous_token

    if _is_debug():
        print(f"[DEBUG] Sending request to {url}")
        print(f"[DEBUG] Full payload: {json.dumps(payload, indent=2)}")
        print(f"[DEBUG] Headers: {{'Authorization': 'Bearer <token>'}}")
        print(f"[DEBUG] previous_token included: {bool(payload.get('previous_token'))}")

    verify = _ssl_verify()

    with httpx.Client(timeout=TIMEOUT, verify=verify) as client:
        resp = client.post(
            url,
            json=payload,
            headers=_auth_headers(token_override=token_override),
        )
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

    verify = _ssl_verify()

    with httpx.Client(timeout=TIMEOUT, verify=verify) as client:
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

    verify = _ssl_verify()

    try:
        with httpx.Client(timeout=10.0, verify=verify) as client:
            resp = client.post(url, json=payload, headers=_auth_headers())
            if _is_debug():
                print(f"[DEBUG] Response status: {resp.status_code}")
    except Exception as e:
        if _is_debug():
            print(f"[DEBUG] Error sending feedback: {e}")
        pass

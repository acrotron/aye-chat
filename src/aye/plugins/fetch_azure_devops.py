"""Azure DevOps work item fetcher plugin.

Fetches Azure DevOps work items by URL or work item ID and returns
structured data for use in the Aye Chat context pipeline.

Supported URL formats:
- https://dev.azure.com/{org}/{project}/_workitems/edit/{id}
- https://dev.azure.com/{org}/{project}/_boards/board/...?workitem={id}
- https://dev.azure.com/{org}/{project}/_backlogs/backlog/...?workitem={id}
- https://{org}.visualstudio.com/{project}/_workitems/edit/{id}  (legacy)

Configuration (env var or ~/.ayecfg):
- AYE_ADO_TOKEN / ado_token  - Personal Access Token (REQUIRED)
- AYE_ADO_TIMEOUT / (default: 30) - Request timeout in seconds
- AYE_SSLVERIFY / sslverify - SSL certificate verification (on/off)
"""

import base64
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from rich import print as rprint

from aye.model.auth import get_user_config
from aye.plugins.plugin_base import Plugin


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------

# Matches canonical dev.azure.com and legacy visualstudio.com work item edit URLs
AZURE_DEVOPS_RE = re.compile(
    r"^https?://"
    r"(?:dev\.azure\.com/([^/]+)|([^\.]+)\.visualstudio\.com)"
    r"/([^/]+)/_workitems/edit/(\d+)",
    re.IGNORECASE,
)

# Matches board and backlog URLs with ?workitem= query param
_BOARD_OR_BACKLOG_URL_RE = re.compile(
    r"^https?://"
    r"(?:dev\.azure\.com/([^/]+)|([^\.]+)\.visualstudio\.com)"
    r"/([^/]+)/_(boards|backlogs)/",
    re.IGNORECASE,
)

# Matches legacy visualstudio.com hostname to extract org name
_LEGACY_HOST_RE = re.compile(r"^([^\.]+)\.visualstudio\.com$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _get_config(env_key: str, cfg_key: str) -> Optional[str]:
    val = os.environ.get(env_key)
    if val:
        return val.strip()
    cfg_val = get_user_config(cfg_key, None)
    if cfg_val:
        return str(cfg_val).strip()
    return None


def _get_timeout() -> float:
    """Get request timeout from config or environment variable."""
    timeout_str = _get_config("AYE_ADO_TIMEOUT", "ado_timeout")
    if timeout_str:
        try:
            return float(timeout_str)
        except ValueError:
            pass
    return 30.0


def _ssl_verify() -> bool:
    """Control TLS certificate verification for ADO API calls.

    Sources (in priority order):
      1) env var AYE_SSLVERIFY
      2) ~/.ayecfg sslverify=on|off

    Defaults to True (verify enabled).
    """
    env_val = os.environ.get("AYE_SSLVERIFY")
    if env_val is not None:
        return env_val.strip().lower() not in ("off", "false", "0", "no")
    cfg_val = get_user_config("sslverify", "on") or "on"
    return str(cfg_val).strip().lower() not in ("off", "false", "0", "no")


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def _normalize_ado_url(url: str) -> str:
    """Normalize an Azure DevOps URL to the canonical work item edit form.

    Handles:
    - Board URLs with ``?workitem=<id>`` query param
    - Backlog URLs with ``?workitem=<id>`` query param
    - Legacy ``{org}.visualstudio.com`` URLs

    Args:
        url: Raw URL string.

    Returns:
        Normalized URL if convertible, otherwise the original URL unchanged.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Extract org name from legacy hostname
    legacy_match = _LEGACY_HOST_RE.match(hostname)
    org_from_host = legacy_match.group(1).lower() if legacy_match else None

    # Handle board and backlog URLs with ?workitem=<id>
    if _BOARD_OR_BACKLOG_URL_RE.match(url):
        qs = parse_qs(parsed.query)
        workitem_ids = qs.get("workitem")
        if workitem_ids:
            work_item_id = workitem_ids[0]
            path_parts = [p for p in parsed.path.split("/") if p]
            
            # Legacy visualstudio.com: path is /{project}/_boards/ or /_backlogs/...
            # org comes from hostname
            if org_from_host and len(path_parts) >= 1:
                project = path_parts[0]
                return (
                    f"https://dev.azure.com/{org_from_host}/{project}"
                    f"/_workitems/edit/{work_item_id}"
                )
            
            # dev.azure.com: path is /{org}/{project}/_boards/ or /_backlogs/...
            if len(path_parts) >= 2:
                org = path_parts[0]
                project = path_parts[1]
                return (
                    f"https://dev.azure.com/{org}/{project}"
                    f"/_workitems/edit/{work_item_id}"
                )
        return url

    # Handle legacy visualstudio.com edit URLs -> convert to dev.azure.com
    if org_from_host:
        path_parts = [p for p in parsed.path.split("/") if p]
        # Path: /{project}/_workitems/edit/{id}
        if len(path_parts) >= 4 and path_parts[1] == "_workitems":
            project = path_parts[0]
            work_item_id = path_parts[3]
            return (
                f"https://dev.azure.com/{org_from_host}/{project}"
                f"/_workitems/edit/{work_item_id}"
            )

    return url


# ---------------------------------------------------------------------------
# Retry logic for connection issues
# ---------------------------------------------------------------------------

def _fetch_with_retry(
    url: str,
    headers: Dict[str, str],
    timeout: float,
    verify: bool,
    max_retries: int = 5,
    verbose: bool = False,
) -> httpx.Response:
    """Fetch URL with retry logic for connection resets.
    
    Windows can forcibly close connections (WinError 10054) due to:
    - HTTP/2 protocol issues
    - Antivirus/firewall interference  
    - Connection pooling problems
    
    This uses aggressive retry with fresh connections each time.
    
    Args:
        url: URL to fetch
        headers: Request headers
        timeout: Request timeout
        verify: SSL certificate verification
        max_retries: Maximum number of retry attempts
        verbose: Print retry information
        
    Returns:
        httpx.Response object
        
    Raises:
        httpx.HTTPStatusError: On auth failure or non-2xx response
        httpx.RequestError: On network error after all retries
    """
    for attempt in range(max_retries):
        try:
            # IMPORTANT: Create a FRESH client for each attempt
            # This prevents connection pool reuse issues on Windows
            # Force HTTP/1.1 to avoid HTTP/2 negotiation problems
            with httpx.Client(
                timeout=timeout,
                verify=verify,
                follow_redirects=False,
                http2=False,  # Force HTTP/1.1 - more reliable on Windows
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),  # No pooling
            ) as client:
                response = client.get(url, headers=headers)
                
                # Check for redirect (302) - this means authentication failed
                if response.status_code in (301, 302, 303, 307, 308):
                    raise httpx.HTTPStatusError(
                        f"Authentication required (redirected to sign-in). "
                        f"Verify your PAT token has 'Work Items (Read)' permission.",
                        request=response.request,
                        response=response
                    )
                
                response.raise_for_status()
                return response
                
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
            # Windows connection reset (WinError 10054) or protocol errors
            if attempt < max_retries - 1:
                delay = min(2 ** attempt, 8)  # Cap at 8s
                if verbose:
                    rprint(f"[yellow]Connection reset (attempt {attempt + 1}/{max_retries}), retrying in {delay}s...[/]")
                    rprint(f"[dim]Error: {e}[/]")
                time.sleep(delay)
                continue
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise httpx.HTTPStatusError(
                    f"Authentication failed (HTTP {e.response.status_code}). "
                    f"Verify your PAT token is valid and has 'Work Items (Read)' permission.",
                    request=e.request,
                    response=e.response
                )
            raise
    
    raise httpx.RequestError(f"Failed after {max_retries} attempts")


# ---------------------------------------------------------------------------
# Core fetch function
# ---------------------------------------------------------------------------

def fetch_azure_devops_item(
    url: str,
    verbose: bool = False,
    *,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Fetch an Azure DevOps work item and return structured data.

    Args:
        url: Canonical ``https://dev.azure.com/{org}/{project}/_workitems/edit/{id}`` URL.
        verbose: Print debug information to stdout.
        timeout: HTTP request timeout in seconds (default: from config or 30s).

    Returns:
        Dict with keys: url, id, title, description, state, type, assignee,
        priority, area, iteration, tags, comments.

    Raises:
        ValueError: If the URL does not match the expected pattern.
        httpx.HTTPStatusError: On non-2xx HTTP responses (including auth failures).
        httpx.RequestError: On network-level errors.
    """
    match = AZURE_DEVOPS_RE.match(url)
    if not match:
        raise ValueError(f"URL does not match Azure DevOps work item pattern: {url}")

    # Extract org, project, and work item id from the URL
    org = (match.group(1) or match.group(2)).lower()
    project = match.group(3)
    work_item_id = match.group(4)

    if verbose:
        rprint(f"[green]Fetching ADO work item from {org}/{project}[/]")

    # Build auth header (Basic auth: empty username + PAT as password)
    pat = _get_config("AYE_ADO_TOKEN","ado_token")
    if not pat:
        raise ValueError(
            "Azure DevOps PAT token is required. "
            "Set AYE_ADO_TOKEN environment variable or ado_token in ~/.ayecfg. "
            "Get a token from: https://dev.azure.com/{org}/_usersSettings/tokens"
        )
    
    headers: Dict[str, str] = {
        "Accept": "application/json",
    }
    token_b64 = base64.b64encode(f":{pat}".encode()).decode()
    headers["Authorization"] = f"Basic {token_b64}"

    if timeout is None:
        timeout = _get_timeout()
    
    # Use shorter timeout per request for retry logic
    request_timeout = min(timeout, 10.0)

    verify = _ssl_verify()
    if verbose and not verify:
        rprint("[yellow]Warning: SSL certificate verification is disabled[/]")

    api_base = f"https://dev.azure.com/{org}/{project}/_apis"
    item_url = (
        f"{api_base}/wit/workItems/{work_item_id}"
        "?$expand=all&api-version=7.0"
    )
    
    # Fetch work item with retry logic (fresh client each time)
    response = _fetch_with_retry(
        url=item_url,
        headers=headers,
        timeout=request_timeout,
        verify=verify,
        verbose=verbose
    )
    item_data = response.json()

    if verbose:
        rprint(f"[green]✓ Fetched ADO work item {work_item_id}[/]")
    
    fields: Dict[str, Any] = item_data.get("fields", {})

    # Parse the AssignedTo field (can be a dict or None)
    assigned_to_raw = fields.get("System.AssignedTo")
    if isinstance(assigned_to_raw, dict):
        assignee = assigned_to_raw.get("displayName")
    else:
        assignee = assigned_to_raw

    # Tags are semicolon-separated strings in ADO
    tags_raw: str = fields.get("System.Tags", "") or ""
    tags: List[str] = [
        t.strip() for t in tags_raw.split(";") if t.strip()
    ]

    result: Dict[str, Any] = {
        "url": url,
        "id": work_item_id,
        "title": fields.get("System.Title"),
        "description": fields.get("System.Description"),
        "state": fields.get("System.State"),
        "type": fields.get("System.WorkItemType"),
        "assignee": assignee,
        "priority": fields.get("Microsoft.VSTS.Common.Priority"),
        "area": fields.get("System.AreaPath"),
        "iteration": fields.get("System.IterationPath"),
        "tags": tags,
        "comments": [],
    }

    # Fetch comments (secondary call - also with fresh client)
    comments_url = (
        f"{api_base}/wit/workItems/{work_item_id}/comments"
        "?api-version=7.0-preview.3"
    )
    
    try:
        comments_response = _fetch_with_retry(
            url=comments_url,
            headers=headers,
            timeout=request_timeout,
            verify=verify,
            verbose=verbose
        )

        if comments_response.status_code == 200:
            comments_data = comments_response.json()
            for c in comments_data.get("comments", []):
                author_raw = c.get("createdBy", {})
                author = (
                    author_raw.get("displayName")
                    if isinstance(author_raw, dict)
                    else author_raw
                )
                result["comments"].append({
                    "author": author,
                    "body": c.get("text"),
                    "created": c.get("createdDate"),
                })
    except Exception as e:
        # Don't fail the entire request if comments fail
        if verbose:
            rprint(f"[yellow]Warning: Could not fetch comments: {e}[/]")

    return result


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

class FetchAzureDevOpsPlugin(Plugin):
    """Plugin that handles Azure DevOps work item URLs in the process_url pipeline."""

    name = "process_url"
    version = "1.0.0"
    premium = "free"

    def on_command(
        self, command_name: str, params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Handle the 'process_url' command for Azure DevOps URLs.

        Args:
            command_name: Must be ``'process_url'`` to be handled.
            params: Dict with at least a ``'url'`` key.

        Returns:
            ``{"status": "success", "data": {...}}`` on success, ``None`` if the
            URL is not an Azure DevOps work item URL or if an error occurs.
        """
        if command_name != "process_url":
            return None

        raw_url: str = params.get("url", "")
        if not raw_url:
            return None

        normalized = _normalize_ado_url(raw_url)

        if not AZURE_DEVOPS_RE.match(normalized):
            return None

        try:
            data = fetch_azure_devops_item(
                normalized,
                verbose=self.verbose,
            )
            return {"status": "success", "data": data}
        except ValueError as e:
            if self.debug:
                rprint(f"[red]ADO configuration error:[/] {e}")
            return None
        except httpx.HTTPStatusError as exc:
            if self.verbose:
                status = exc.response.status_code if hasattr(exc.response, 'status_code') else '?'
                rprint(f"[red]ADO HTTP error {status}:[/] {exc}")
            return None
        except httpx.RequestError as exc:
            if self.verbose:
                rprint(f"[red]ADO network error for {normalized}:[/] {exc}")
            return None

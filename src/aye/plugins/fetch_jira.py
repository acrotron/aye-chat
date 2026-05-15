import re
import os
import httpx
from typing import Any, Dict, Optional
import rich
import urllib.parse

try:
    # Preferred import path (matches other plugins in this package).
    from aye.model.auth import get_user_config
except Exception:  # pragma: no cover - fallback if package layout differs
    try:
        from ..model.auth import get_user_config  # type: ignore
    except Exception:  # pragma: no cover
        def get_user_config(key: str, default: Optional[str] = None) -> Optional[str]:
            return default
        
from aye.plugins.plugin_base import Plugin


DEFAULT_TIMEOUT = 30.0
JIRA_TICKET_PATTERN = re.compile(
    r'^https?://([^/]+)\.atlassian\.net/browse/([A-Z0-9]+-\d+)/?$'
)

# Direct module-level reference — cleanly patchable by unittest.mock.patch
rprint = rich.print

class FetchJiraPlugin(Plugin):
    name = "process_url"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        super().init(cfg)

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "process_url":
            url = params.get("url")

            normalized = _normalize_jira_url(url)
            
            if normalized and JIRA_TICKET_PATTERN.match(normalized):
                try: 
                    data = fetch_jira_ticket(normalized, self.verbose)
                    return {"status": "success", "data": data}
                except ValueError:
                    return None
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404 and self.debug:
                        rprint("✗ Jira Ticket not found (404)")
                    return None
                except httpx.RequestError:
                    return None
        return None
    
def _normalize_jira_url(url: str) -> Optional[str]:
    """Convert board URLs to browse URLs."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    if 'selectedIssue' in params:
        issue_key = params['selectedIssue'][0]
        return f"https://{parsed.netloc}/browse/{issue_key}"
    return url

def _get_config(env_key: str, cfg_key: str) -> Optional[str]:
    val = os.environ.get(env_key)
    if val:
        return val.strip()
    cfg_val = get_user_config(cfg_key, None)
    if cfg_val:
        return str(cfg_val).strip()
    return None

def fetch_jira_ticket(url: str, verbose: bool, *, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Fetch a Jira ticket via the REST API.

    Args:
        url: Jira ticket URL (e.g., https://acrotron.atlassian.net/browse/ACM-115)
        verbose: Enable verbose output.
        timeout: Request timeout in seconds.

    Returns:
        Parsed ticket data as a dictionary.

    Raises:
        ValueError: If URL is not a valid Jira ticket URL.
        httpx.HTTPStatusError: If the API returns an error status.
        httpx.RequestError: If a network error occurs.
    """
    match = JIRA_TICKET_PATTERN.match(url) 
    if not match:
        return None
        
    domain, issue_key = match.groups()

    # Jira REST API v2 endpoint
    api_url = f"https://{domain}.atlassian.net/rest/api/2/issue/{issue_key}"

    # Retrieve auth credentials from ~/.ayecfg or environment variables
    email = _get_config("AYE_JIRA_EMAIL" , "jira_email")
    token = _get_config("AYE_JIRA_TOKEN", "jira_token")
    auth = (email, token) if email and token else None

    with httpx.Client(timeout=timeout) as client:
        headers = {
            "Accept": "application/json",
            "User-Agent": "jira-ticket-fetcher/1.0",
        }

        response = client.get(api_url, headers=headers, auth=auth)
        response.raise_for_status()

        issue = response.json()

        if response.status_code == 200 and verbose:
            rprint(f"[green]✓ Fetched Jira Ticket {issue_key}[/]")
        
        fields = issue.get("fields", {})

        return {
            "url": url,
            "key": issue_key,
            "summary": fields.get("summary"),
            "description": fields.get("description"),
            "status": fields.get("status", {}).get("name"),
            "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else "Unassigned",
            "reporter": fields.get("reporter", {}).get("displayName"),
            "priority": fields.get("priority", {}).get("name"),
            "labels": fields.get("labels", []),
            "comments": [
            {
                "author": c.get("author", {}).get("displayName"),
                "body": c.get("body"),
                "created": c.get("created"),
            }
            for c in fields.get("comment", {}).get("comments", [])
        ],
        }

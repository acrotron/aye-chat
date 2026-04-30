import re
import httpx
from typing import Any, Dict, Optional
import rich

from aye.model.auth import get_user_config
from aye.plugins.plugin_base import Plugin

import json

DEFAULT_TIMEOUT = 30.0
JIRA_TICKET_PATTERN = re.compile(
    r'^https?://([^/]+)\.atlassian\.net/browse/([A-Z]+-\d+)/?$'
)

# Direct module-level reference — cleanly patchable by unittest.mock.patch
rprint = rich.print

class FetchJiraPlugin(Plugin):
    name = "process_url"

    def init(self, cfg: Dict[str, Any]) -> None:
        super().init(cfg)

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "process_url":

            url = params.get("url")
            verbose = params.get("verbose", False)

            if url and JIRA_TICKET_PATTERN.match(url):
                try: 
                    data = fetch_jira_ticket(url, verbose)
                    return {"status": "success", "data": data}
                except ValueError:
                    return None
                except httpx.HTTPStatusError:
                    return None
                except httpx.RequestError:
                    return None
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
        rprint(f"[red]in NOT match")
        return None
        
    domain, issue_key = match.groups()

    # Jira REST API v2 endpoint
    api_url = f"https://{domain}.atlassian.net/rest/api/2/issue/{issue_key}"

    # Retrieve auth credentials from ~/.ayecfg or environment variables
    email = get_user_config("JIRA_EMAIL")
    token = get_user_config("JIRA_TOKEN")

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
        elif verbose:
            rprint(f"[yellow]⚠ Could not fetch {url}[/]")

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
        }

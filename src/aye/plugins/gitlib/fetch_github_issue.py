import re
import sys
from typing import Any

import httpx
from rich import print as rprint, print_json
from rich.console import Console
from rich.json import JSON
from rich.theme import Theme

_JSON_PRINT_THEME = Theme({
    "json.key": "bold turquoise2",
    "json.str": "steel_blue",
    "json.number": "steel_blue",
    "json.bool_true": "bold sea_green2",
    "json.bool_false": "bold indian_red1",
    "json.null": "bold khaki1",
})
DEFAULT_TIMEOUT = 30.0

# Regex to extract owner, repo, issue number from GitHub URL
GITHUB_ISSUE_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$"
)

def fetch_github_issue(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch a GitHub issue via the REST API.

    Args:
        url: GitHub issue URL (e.g., https://github.com/owner/repo/issues/123)
        timeout: Request timeout in seconds.

    Returns:
        Parsed issue data as a dictionary.

    Raises:
        ValueError: If URL is not a valid GitHub issue URL.
        httpx.HTTPStatusError: If the API returns an error status.
        httpx.RequestError: If a network error occurs.
    """
    # Parse the URL
    match = GITHUB_ISSUE_PATTERN.match(url)
    if not match:
        raise ValueError(f"Not a valid GitHub issue URL: {url}")

    owner, repo, issue_num = match.groups()

    # Build API URLs
    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_num}"
    comments_url = f"{api_url}/timeline"

    # Make API requests
    with httpx.Client(timeout=timeout) as client:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-issue-fetcher/1.0",
        }

        # Fetch issue data
        response = client.get(api_url, headers=headers)
        response.raise_for_status()
        issue = response.json()

        # Fetch comments
        # Mockingbird required for timeline endpoint!
        comments_header = {
        "Accept": "application/vnd.github.mockingbird-preview+json", 
        "User-Agent": "github-issue-fetcher/1.0",
        }

        timeline_response = client.get(comments_url, headers=comments_header)
        comments = []
        if timeline_response.status_code == 200:
                for e in timeline_response.json():
                     if e.get("user", {}).get("login") and e.get("body"):
                          comments.append({
                               "author" : e.get("user", {}).get("login") ,
                               "body" : e.get("body")
                          })

        # Return structured data
        return {
            "url": url,
            "number": issue.get("number"),
            "title": issue.get("title"),
            "author": issue.get("user", {}).get("login"),
            "state": issue.get("state"),
            "body": issue.get("body"),
            "labels": [l.get("name") for l in issue.get("labels", [])],
            "comments": comments,
        }


def driver() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        rprint("[yellow]Try: python -m aye.model.fetch_github_issue <github_issue_url>[/]")
        sys.exit(1)

    url = sys.argv[1]

    try:
        data = fetch_github_issue(url)
        console = Console(theme =_JSON_PRINT_THEME)
        console.print(JSON.from_data(data, indent=2))
    except ValueError as e:
        rprint(f"[red]Invalid URL:[/] {e}")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        rprint(f"[red]API error:[/] {e.response.status_code}")
        sys.exit(1)
    except httpx.RequestError as e:
        rprint(f"[red]Network error:[/] {e}")
        sys.exit(1)


if __name__ == "__main__":
    driver()
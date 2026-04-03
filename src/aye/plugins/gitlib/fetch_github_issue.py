import re
import sys
import httpx

from typing import Any, Dict, Optional
from rich import print as rprint
from rich.console import Console
from rich.json import JSON
from rich.theme import Theme

from aye.plugins.plugin_base import Plugin

_JSON_PRINT_THEME = Theme({
    "json.key": "bold turquoise2",
    "json.str": "steel_blue",
    "json.number": "steel_blue",
    "json.bool_true": "bold sea_green2",
    "json.bool_false": "bold indian_red1",
    "json.null": "bold khaki1",
})

DEFAULT_TIMEOUT = 30.0

GITHUB_ISSUE_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$"
)


class FetchGithubIssuePlugin(Plugin):
    name = "fetch_github_issue"

    def init(self, cfg: Dict[str, Any]) -> None:
        super().init(cfg)

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "gitlib":
            url = params.get("url")
            if not url:
                rprint("[red]Usage:[/] gitlib <github-issue-url>")
                return {"status": "error", "summary": "No URL provided"}
            try:
                data = fetch_github_issue(url)
                return {"status": "success", "data": data}
            except ValueError as e:
                rprint(f"[red]Invalid URL:[/] {e}")
                return {"status": "error", "summary": str(e)}
            except httpx.HTTPStatusError as e:
                rprint(f"[red]API error:[/] {e.response.status_code}")
                return {"status": "error", "summary": str(e)}
            except httpx.RequestError as e:
                rprint(f"[red]Network error:[/] {e}")
                return {"status": "error", "summary": str(e)}
        return None


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
    match = GITHUB_ISSUE_PATTERN.match(url)
    if not match:
        raise ValueError(f"Not a valid GitHub issue URL: {url}")

    owner, repo, issue_num = match.groups()

    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_num}"
    comments_url = f"{api_url}/timeline"

    with httpx.Client(timeout=timeout) as client:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-issue-fetcher/1.0",
        }

        response = client.get(api_url, headers=headers)
        response.raise_for_status()
        issue = response.json()

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
                        "author": e.get("user", {}).get("login"),
                        "body": e.get("body")
                    })

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


#TODO: remove driver?

# def driver() -> None:
#     """CLI entry point."""
#     if len(sys.argv) < 2:
#         rprint("[yellow]Usage: python -m aye.plugins.gitlib.fetch_github_issue <github_issue_url>[/]")
#         sys.exit(1)

#     url = sys.argv[1]

#     try:
#         data = fetch_github_issue(url)
#         console = Console(theme=_JSON_PRINT_THEME)
#         console.print(JSON.from_data(data, indent=2))
#     except ValueError as e:
#         rprint(f"[red]Invalid URL:[/] {e}")
#         sys.exit(1)
#     except httpx.HTTPStatusError as e:
#         rprint(f"[red]API error:[/] {e.response.status_code}")
#         sys.exit(1)
#     except httpx.RequestError as e:
#         rprint(f"[red]Network error:[/] {e}")
#         sys.exit(1)


# if __name__ == "__main__":
#     driver()

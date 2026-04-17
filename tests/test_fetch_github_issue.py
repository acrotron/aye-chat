import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import aye.plugins.fetch_github_issue as fetch_module
from aye.plugins.fetch_github_issue import (
    FetchGithubIssuePlugin,
    fetch_github_issue,
    GITHUB_ISSUE_PATTERN,
)


@pytest.fixture
def mock_issue_response():
    return {
        "number": 42,
        "title": "Test Issue",
        "user": {"login": "testuser"},
        "state": "open",
        "body": "This is the issue body.",
        "labels": [{"name": "bug"}, {"name": "help wanted"}],
    }


@pytest.fixture
def mock_timeline_response():
    return [
        {"event": "commented", "user": {"login": "commenter1"}, "body": "First comment"},
        {"event": "labeled", "label": {"name": "bug"}},
        {"event": "commented", "user": {"login": "commenter2"}, "body": "Second comment"},
    ]


class TestFetchGitHubIssue:
    def test_fetch_verbose_mode_prints_messages(self, mock_issue_response, mock_timeline_response):
        """Test that verbose mode prints status messages."""
        url = "https://github.com/owner/repo/issues/42"

        with patch.object(fetch_module, "rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class:

            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            issue_response = MagicMock()
            issue_response.json.return_value = mock_issue_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = mock_timeline_response

            mock_client.get.side_effect = [issue_response, timeline_response]

            fetch_github_issue(url, verbose=True)

            # Verify verbose messages were printed
            assert mock_rprint.call_count >= 2

"""Unit tests for fetch_github_issue module."""

import pytest
from unittest.mock import patch, MagicMock
import httpx

from aye.plugins.gitlib.fetch_github_issue import (
    fetch_github_issue,
    driver,
    GITHUB_ISSUE_PATTERN,
    DEFAULT_TIMEOUT,
)


class TestGitHubIssuePattern:
    """Tests for the GitHub issue URL regex pattern."""

    @pytest.mark.parametrize("url,expected", [
        ("https://github.com/owner/repo/issues/123", ("owner", "repo", "123")),
        ("https://github.com/my-org/my-repo/issues/1", ("my-org", "my-repo", "1")),
        ("https://www.github.com/owner/repo/issues/456", ("owner", "repo", "456")),
        ("http://github.com/owner/repo/issues/789", ("owner", "repo", "789")),
        ("https://github.com/owner/repo/issues/123/", ("owner", "repo", "123")),
    ])
    def test_valid_urls(self, url: str, expected: tuple):
        """Test that valid GitHub issue URLs are parsed correctly."""
        match = GITHUB_ISSUE_PATTERN.match(url)
        assert match is not None
        assert match.groups() == expected

    @pytest.mark.parametrize("url", [
        "https://github.com/owner/repo/pull/123",
        "https://github.com/owner/repo/issues",
        "https://github.com/owner/repo",
        "https://gitlab.com/owner/repo/issues/123",
        "not-a-url",
        "",
        "https://github.com/owner/repo/issues/abc",
        "https://github.com//repo/issues/123",
    ])
    def test_invalid_urls(self, url: str):
        """Test that invalid URLs do not match."""
        match = GITHUB_ISSUE_PATTERN.match(url)
        assert match is None


class TestFetchGitHubIssue:
    """Tests for the fetch_github_issue function."""

    @pytest.fixture
    def mock_issue_response(self) -> dict:
        """Sample GitHub issue API response."""
        return {
            "number": 42,
            "title": "Test Issue Title",
            "user": {"login": "testuser"},
            "state": "open",
            "body": "This is the issue body.",
            "labels": [
                {"name": "bug"},
                {"name": "help wanted"},
            ],
        }

    @pytest.fixture
    def mock_timeline_response(self) -> list:
        """Sample GitHub timeline API response."""
        return [
            {
                "event": "commented",
                "user": {"login": "commenter1"},
                "body": "First comment",
            },
            {
                "event": "labeled",
                "label": {"name": "bug"},
            },
            {
                "event": "commented",
                "user": {"login": "commenter2"},
                "body": "Second comment",
            },
        ]

    def test_fetch_success(self, mock_issue_response, mock_timeline_response):
        """Test successful fetch of a GitHub issue."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            # Mock issue response
            issue_response = MagicMock()
            issue_response.json.return_value = mock_issue_response
            issue_response.raise_for_status = MagicMock()

            # Mock timeline response
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = mock_timeline_response

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url)

            assert result["url"] == url
            assert result["number"] == 42
            assert result["title"] == "Test Issue Title"
            assert result["author"] == "testuser"
            assert result["state"] == "open"
            assert result["body"] == "This is the issue body."
            assert result["labels"] == ["bug", "help wanted"]
            assert len(result["comments"]) == 2
            assert result["comments"][0] == {"author": "commenter1", "body": "First comment"}
            assert result["comments"][1] == {"author": "commenter2", "body": "Second comment"}

    def test_fetch_no_comments(self, mock_issue_response):
        """Test fetch when timeline endpoint returns non-200."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            # Mock issue response
            issue_response = MagicMock()
            issue_response.json.return_value = mock_issue_response
            issue_response.raise_for_status = MagicMock()

            # Mock timeline response with 403 (rate limited or no access)
            timeline_response = MagicMock()
            timeline_response.status_code = 403

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url)

            assert result["comments"] == []

    def test_fetch_empty_labels(self):
        """Test fetch when issue has no labels."""
        url = "https://github.com/owner/repo/issues/1"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            issue_response = MagicMock()
            issue_response.json.return_value = {
                "number": 1,
                "title": "No labels",
                "user": {"login": "user"},
                "state": "closed",
                "body": None,
                "labels": [],
            }
            issue_response.raise_for_status = MagicMock()

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url)

            assert result["labels"] == []
            assert result["body"] is None

    def test_fetch_invalid_url_raises_value_error(self):
        """Test that invalid URL raises ValueError."""
        with pytest.raises(ValueError, match="Not a valid GitHub issue URL"):
            fetch_github_issue("https://github.com/owner/repo/pull/123")

    def test_fetch_invalid_url_not_github(self):
        """Test that non-GitHub URL raises ValueError."""
        with pytest.raises(ValueError, match="Not a valid GitHub issue URL"):
            fetch_github_issue("https://gitlab.com/owner/repo/issues/123")

    def test_fetch_http_404_error(self):
        """Test that 404 error is propagated."""
        url = "https://github.com/owner/repo/issues/99999"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            response = MagicMock()
            response.status_code = 404
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=response,
            )

            mock_client.get.return_value = response

            with pytest.raises(httpx.HTTPStatusError):
                fetch_github_issue(url)

    def test_fetch_http_403_rate_limit(self):
        """Test that 403 rate limit error is propagated."""
        url = "https://github.com/owner/repo/issues/1"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            response = MagicMock()
            response.status_code = 403
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Forbidden",
                request=MagicMock(),
                response=response,
            )

            mock_client.get.return_value = response

            with pytest.raises(httpx.HTTPStatusError):
                fetch_github_issue(url)

    def test_fetch_network_error(self):
        """Test that network errors are propagated."""
        url = "https://github.com/owner/repo/issues/1"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            mock_client.get.side_effect = httpx.ConnectError("Connection failed")

            with pytest.raises(httpx.ConnectError):
                fetch_github_issue(url)

    def test_fetch_custom_timeout(self, mock_issue_response):
        """Test that custom timeout is passed to client."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            issue_response = MagicMock()
            issue_response.json.return_value = mock_issue_response
            issue_response.raise_for_status = MagicMock()

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []

            mock_client.get.side_effect = [issue_response, timeline_response]

            fetch_github_issue(url, timeout=60.0)

            mock_client_class.assert_called_once_with(timeout=60.0)

    def test_fetch_uses_default_timeout(self, mock_issue_response):
        """Test that default timeout is used when not specified."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            issue_response = MagicMock()
            issue_response.json.return_value = mock_issue_response
            issue_response.raise_for_status = MagicMock()

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []

            mock_client.get.side_effect = [issue_response, timeline_response]

            fetch_github_issue(url)

            mock_client_class.assert_called_once_with(timeout=DEFAULT_TIMEOUT)


class TestDriver:
    """Tests for the driver CLI function."""

    def test_driver_no_args(self):
        """Test driver exits with error when no URL provided."""
        with patch("sys.argv", ["fetch_github_issue"]):
            with pytest.raises(SystemExit) as exc_info:
                driver()
            assert exc_info.value.code == 1

    def test_driver_invalid_url(self):
        """Test driver exits with error for invalid URL."""
        with patch("sys.argv", ["fetch_github_issue", "not-a-valid-url"]):
            with pytest.raises(SystemExit) as exc_info:
                driver()
            assert exc_info.value.code == 1

    def test_driver_success(self):
        """Test driver prints JSON on success."""
        mock_data = {
            "url": "https://github.com/owner/repo/issues/1",
            "number": 1,
            "title": "Test",
            "author": "user",
            "state": "open",
            "body": "body",
            "labels": [],
            "comments": [],
        }

        with patch("sys.argv", ["fetch_github_issue", "https://github.com/owner/repo/issues/1"]):
            with patch(
                "aye.plugins.gitlib.fetch_github_issue.fetch_github_issue",
                return_value=mock_data,
            ):
                with patch(
                    "aye.plugins.gitlib.fetch_github_issue.Console"
                ) as mock_console_class:
                    mock_console = MagicMock()
                    mock_console_class.return_value = mock_console

                    driver()

                    mock_console.print.assert_called_once()

    def test_driver_http_error(self):
        """Test driver exits with error on HTTP error."""
        with patch("sys.argv", ["fetch_github_issue", "https://github.com/owner/repo/issues/1"]):
            mock_response = MagicMock()
            mock_response.status_code = 404

            with patch(
                "aye.plugins.gitlib.fetch_github_issue.fetch_github_issue",
                side_effect=httpx.HTTPStatusError(
                    "Not Found",
                    request=MagicMock(),
                    response=mock_response,
                ),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    driver()
                assert exc_info.value.code == 1

    def test_driver_network_error(self):
        """Test driver exits with error on network error."""
        with patch("sys.argv", ["fetch_github_issue", "https://github.com/owner/repo/issues/1"]):
            with patch(
                "aye.plugins.gitlib.fetch_github_issue.fetch_github_issue",
                side_effect=httpx.ConnectError("Connection failed"),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    driver()
                assert exc_info.value.code == 1


class TestTimelineFiltering:
    """Tests for timeline event filtering logic."""

    def test_filters_events_without_user(self):
        """Test that events without user are filtered out."""
        url = "https://github.com/owner/repo/issues/1"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            issue_response = MagicMock()
            issue_response.json.return_value = {
                "number": 1,
                "title": "Test",
                "user": {"login": "author"},
                "state": "open",
                "body": "body",
                "labels": [],
            }
            issue_response.raise_for_status = MagicMock()

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = [
                {"event": "labeled", "label": {"name": "bug"}},  # No user, no body
                {"event": "commented", "user": {"login": "user1"}, "body": "comment"},
                {"event": "assigned", "assignee": {"login": "user2"}},  # No body
            ]

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url)

            assert len(result["comments"]) == 1
            assert result["comments"][0]["author"] == "user1"

    def test_filters_events_without_body(self):
        """Test that events without body are filtered out."""
        url = "https://github.com/owner/repo/issues/1"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            issue_response = MagicMock()
            issue_response.json.return_value = {
                "number": 1,
                "title": "Test",
                "user": {"login": "author"},
                "state": "open",
                "body": "body",
                "labels": [],
            }
            issue_response.raise_for_status = MagicMock()

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = [
                {"event": "commented", "user": {"login": "user1"}},  # No body
                {"event": "commented", "user": {"login": "user2"}, "body": ""},  # Empty body
                {"event": "commented", "user": {"login": "user3"}, "body": "real comment"},
            ]

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url)

            # Only the third comment should be included (non-empty body)
            assert len(result["comments"]) == 1
            assert result["comments"][0]["author"] == "user3"
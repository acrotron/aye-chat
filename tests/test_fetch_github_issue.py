"""Unit tests for fetch_github_issue module."""

import pytest
from unittest.mock import patch, MagicMock
import httpx

from aye.plugins.fetch_github_issue import (
    FetchGithubIssuePlugin,
    fetch_github_issue,
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
            issue_response.status_code = 200

            # Mock timeline response
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = mock_timeline_response

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url, verbose=False)

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

    def test_fetch_verbose_mode_prints_messages(self, mock_issue_response, mock_timeline_response):
        """Test that verbose mode prints status messages."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("httpx.Client") as mock_client_class, \
             patch("aye.plugins.fetch_github_issue.rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.print") as mock_print:
            mock_print.side_effect = lambda *args, **kwargs: mock_rprint(*args, **kwargs)

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
            calls = [str(c) for c in mock_rprint.call_args_list]
            assert any("fetching GitHub Issue" in c for c in calls)
            assert any("Fetched Issue #42" in c for c in calls)

    def test_fetch_non_verbose_mode_no_prints(self, mock_issue_response, mock_timeline_response):
        """Test that non-verbose mode doesn't print messages."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("httpx.Client") as mock_client_class, \
             patch("aye.plugins.fetch_github_issue.rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.print") as mock_print:
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

            fetch_github_issue(url, verbose=False)

            # Verify no messages were printed
            mock_rprint.assert_not_called()
            mock_print.assert_not_called()

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
            issue_response.status_code = 200

            # Mock timeline response with 403 (rate limited or no access)
            timeline_response = MagicMock()
            timeline_response.status_code = 403

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url, verbose=False)

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
            issue_response.status_code = 200

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url, verbose=False)

            assert result["labels"] == []
            assert result["body"] is None

    def test_fetch_invalid_url_raises_value_error(self):
        """Test that invalid URL raises ValueError."""
        with pytest.raises(ValueError, match="Not a valid GitHub issue URL"):
            fetch_github_issue("https://github.com/owner/repo/pull/123", verbose=False)

    def test_fetch_invalid_url_not_github(self):
        """Test that non-GitHub URL raises ValueError."""
        with pytest.raises(ValueError, match="Not a valid GitHub issue URL"):
            fetch_github_issue("https://gitlab.com/owner/repo/issues/123", verbose=False)

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
                fetch_github_issue(url, verbose=False)

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
                fetch_github_issue(url, verbose=False)

    def test_fetch_network_error(self):
        """Test that network errors are propagated."""
        url = "https://github.com/owner/repo/issues/1"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            mock_client.get.side_effect = httpx.ConnectError("Connection failed")

            with pytest.raises(httpx.ConnectError):
                fetch_github_issue(url, verbose=False)

    def test_fetch_custom_timeout(self, mock_issue_response):
        """Test that custom timeout is passed to client."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client

            issue_response = MagicMock()
            issue_response.json.return_value = mock_issue_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []

            mock_client.get.side_effect = [issue_response, timeline_response]

            fetch_github_issue(url, verbose=False, timeout=60.0)

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
            issue_response.status_code = 200

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []

            mock_client.get.side_effect = [issue_response, timeline_response]

            fetch_github_issue(url, verbose=False)

            mock_client_class.assert_called_once_with(timeout=DEFAULT_TIMEOUT)


class TestFetchGithubIssuePlugin:
    """Tests for the FetchGithubIssuePlugin class."""

    @pytest.fixture
    def plugin(self):
        """Create a plugin instance."""
        return FetchGithubIssuePlugin()

    def test_plugin_name(self, plugin):
        """Test that plugin has correct name."""
        assert plugin.name == "fetch_github_issue"

    def test_on_command_no_url(self, plugin):
        """Test that missing URL returns error."""
        with patch("aye.plugins.fetch_github_issue.rprint"), \
             patch("aye.plugins.fetch_github_issue.print"):
            result = plugin.on_command("fetch_github_issue", {})

        assert result["status"] == "error"
        assert "No URL provided" in result["summary"]

    def test_on_command_invalid_url(self, plugin):
        """Test that invalid URL returns error dict."""
        result = plugin.on_command("fetch_github_issue", {
            "url": "https://github.com/owner/repo/pull/123",
            "verbose": False
        })

        assert result["status"] == "error"
        assert "Not a valid GitHub issue URL" in result["summary"]

    def test_on_command_invalid_url_verbose(self, plugin):
        """Test that invalid URL prints error in verbose mode."""
        with patch("aye.plugins.fetch_github_issue.rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.print") as mock_print:
            mock_print.side_effect = lambda *args, **kwargs: mock_rprint(*args, **kwargs)

            result = plugin.on_command("fetch_github_issue", {
                "url": "https://github.com/owner/repo/pull/123",
                "verbose": True
            })

        assert result["status"] == "error"
        mock_rprint.assert_called_once()
        assert "Invalid URL" in str(mock_rprint.call_args)

    def test_on_command_http_error(self, plugin):
        """Test that HTTP errors return error dict."""
        with patch("aye.plugins.fetch_github_issue.fetch_github_issue") as mock_fetch:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_fetch.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=mock_response,
            )

            result = plugin.on_command("fetch_github_issue", {
                "url": "https://github.com/owner/repo/issues/99999",
                "verbose": False
            })

        assert result["status"] == "error"
        assert "Not Found" in result["summary"]

    def test_on_command_http_error_verbose(self, plugin):
        """Test that HTTP errors print in verbose mode."""
        with patch("aye.plugins.fetch_github_issue.fetch_github_issue") as mock_fetch, \
             patch("aye.plugins.fetch_github_issue.rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.print") as mock_print:
            mock_print.side_effect = lambda *args, **kwargs: mock_rprint(*args, **kwargs)

            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_fetch.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=mock_response,
            )

            result = plugin.on_command("fetch_github_issue", {
                "url": "https://github.com/owner/repo/issues/99999",
                "verbose": True
            })

        assert result["status"] == "error"
        mock_rprint.assert_called_once()
        assert "API error" in str(mock_rprint.call_args)
        assert "404" in str(mock_rprint.call_args)

    def test_on_command_network_error(self, plugin):
        """Test that network errors return error dict."""
        with patch("aye.plugins.fetch_github_issue.fetch_github_issue") as mock_fetch:
            mock_fetch.side_effect = httpx.ConnectError("Connection failed")

            result = plugin.on_command("fetch_github_issue", {
                "url": "https://github.com/owner/repo/issues/1",
                "verbose": False
            })

        assert result["status"] == "error"
        assert "Connection failed" in result["summary"]

    def test_on_command_network_error_verbose(self, plugin):
        """Test that network errors print in verbose mode."""
        with patch("aye.plugins.fetch_github_issue.fetch_github_issue") as mock_fetch, \
             patch("aye.plugins.fetch_github_issue.rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.print") as mock_print:
            mock_print.side_effect = lambda *args, **kwargs: mock_rprint(*args, **kwargs)

            mock_fetch.side_effect = httpx.ConnectError("Connection failed")

            result = plugin.on_command("fetch_github_issue", {
                "url": "https://github.com/owner/repo/issues/1",
                "verbose": True
            })

        assert result["status"] == "error"
        mock_rprint.assert_called_once()
        assert "Network error" in str(mock_rprint.call_args)

    def test_on_command_success(self, plugin):
        """Test successful fetch returns success dict."""
        mock_data = {
            "url": "https://github.com/owner/repo/issues/1",
            "number": 1,
            "title": "Test Issue",
            "author": "testuser",
            "state": "open",
            "body": "Test body",
            "labels": ["bug"],
            "comments": [],
        }

        with patch("aye.plugins.fetch_github_issue.fetch_github_issue") as mock_fetch:
            mock_fetch.return_value = mock_data

            result = plugin.on_command("fetch_github_issue", {
                "url": "https://github.com/owner/repo/issues/1",
                "verbose": False
            })

        assert result["status"] == "success"
        assert result["data"] == mock_data

    def test_on_command_wrong_command(self, plugin):
        """Test that wrong command name returns None."""
        result = plugin.on_command("different_command", {"url": "test"})
        assert result is None


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
            issue_response.status_code = 200

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = [
                {"event": "labeled", "label": {"name": "bug"}},  # No user, no body
                {"event": "commented", "user": {"login": "user1"}, "body": "comment"},
                {"event": "assigned", "assignee": {"login": "user2"}},  # No body
            ]

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url, verbose=False)

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
            issue_response.status_code = 200

            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = [
                {"event": "commented", "user": {"login": "user1"}},  # No body
                {"event": "commented", "user": {"login": "user2"}, "body": ""},  # Empty body
                {"event": "commented", "user": {"login": "user3"}, "body": "real comment"},
            ]

            mock_client.get.side_effect = [issue_response, timeline_response]

            result = fetch_github_issue(url, verbose=False)

            # Only the third comment should be included (non-empty body)
            assert len(result["comments"]) == 1
            assert result["comments"][0]["author"] == "user3"

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import aye.plugins.fetch_github_issue as fetch_module
from aye.plugins.fetch_github_issue import (
    FetchGithubIssuePlugin,
    fetch_github_issue,
    GITHUB_ISSUE_PATTERN,
    _get_config,
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


def _make_mock_client(issue_response_data, timeline_response_data, issue_status=200, timeline_status=200):
    """Helper to build a mock httpx.Client context manager."""
    mock_client = MagicMock()

    issue_response = MagicMock()
    issue_response.json.return_value = issue_response_data
    issue_response.raise_for_status = MagicMock()
    issue_response.status_code = issue_status

    timeline_response = MagicMock()
    timeline_response.status_code = timeline_status
    timeline_response.json.return_value = timeline_response_data

    mock_client.get.side_effect = [issue_response, timeline_response]
    return mock_client


class TestGetConfig:
    """Tests for the _get_config helper."""

    def test_env_var_takes_priority(self):
        with patch.dict(os.environ, {"AYE_GITHUB_TOKEN": "env-token"}):
            with patch.object(fetch_module, "get_user_config", return_value="cfg-token"):
                assert _get_config("AYE_GITHUB_TOKEN", "github_token") == "env-token"

    def test_falls_back_to_user_config(self):
        env = {k: v for k, v in os.environ.items() if k != "AYE_GITHUB_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(fetch_module, "get_user_config", return_value="cfg-token"):
                assert _get_config("AYE_GITHUB_TOKEN", "github_token") == "cfg-token"

    def test_returns_none_when_not_set(self):
        env = {k: v for k, v in os.environ.items() if k != "AYE_GITHUB_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(fetch_module, "get_user_config", return_value=None):
                assert _get_config("AYE_GITHUB_TOKEN", "github_token") is None

    def test_env_var_is_stripped(self):
        with patch.dict(os.environ, {"AYE_GITHUB_TOKEN": "  token-with-spaces  "}):
            assert _get_config("AYE_GITHUB_TOKEN", "github_token") == "token-with-spaces"


class TestFetchGitHubIssue:
    def test_fetch_verbose_mode_prints_messages(self, mock_issue_response, mock_timeline_response):
        """Test that verbose mode prints status messages."""
        url = "https://github.com/owner/repo/issues/42"

        with patch.object(fetch_module, "rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class, \
             patch.object(fetch_module, "_get_config", return_value=None):

            mock_client_class.return_value.__enter__.return_value = _make_mock_client(
                mock_issue_response, mock_timeline_response
            )

            fetch_github_issue(url, verbose=True)

            assert mock_rprint.call_count == 1

    def test_fetch_without_token_no_auth_header(self, mock_issue_response, mock_timeline_response):
        """When no token is configured, Authorization header must NOT be sent."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class, \
             patch.object(fetch_module, "_get_config", return_value=None):

            mock_client = _make_mock_client(mock_issue_response, mock_timeline_response)
            mock_client_class.return_value.__enter__.return_value = mock_client

            fetch_github_issue(url, verbose=False)

            for call_args in mock_client.get.call_args_list:
                headers_used = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
                assert "Authorization" not in headers_used

    def test_fetch_with_token_sends_auth_header(self, mock_issue_response, mock_timeline_response):
        """When a token is configured, Authorization: token <token> must be sent on all requests."""
        url = "https://github.com/owner/repo/issues/42"
        token = "ghp_mySecretToken"

        with patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class, \
             patch.object(fetch_module, "_get_config", return_value=token):

            mock_client = _make_mock_client(mock_issue_response, mock_timeline_response)
            mock_client_class.return_value.__enter__.return_value = mock_client

            fetch_github_issue(url, verbose=False)

            assert mock_client.get.call_count == 2
            for call_args in mock_client.get.call_args_list:
                headers_used = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
                assert headers_used.get("Authorization") == f"token {token}"

    def test_fetch_token_read_from_env_var(self, mock_issue_response, mock_timeline_response):
        """Token is read from AYE_GITHUB_TOKEN environment variable."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class, \
             patch.dict(os.environ, {"AYE_GITHUB_TOKEN": "env-token-123"}), \
             patch.object(fetch_module, "get_user_config", return_value=None):

            mock_client = _make_mock_client(mock_issue_response, mock_timeline_response)
            mock_client_class.return_value.__enter__.return_value = mock_client

            fetch_github_issue(url, verbose=False)

            for call_args in mock_client.get.call_args_list:
                headers_used = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
                assert headers_used.get("Authorization") == "token env-token-123"

    def test_fetch_token_read_from_user_config(self, mock_issue_response, mock_timeline_response):
        """Token is read from github_token in ~/.ayecfg when env var is absent."""
        url = "https://github.com/owner/repo/issues/42"

        env = {k: v for k, v in os.environ.items() if k != "AYE_GITHUB_TOKEN"}
        with patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class, \
             patch.dict(os.environ, env, clear=True), \
             patch.object(fetch_module, "get_user_config", return_value="cfg-token-456"):

            mock_client = _make_mock_client(mock_issue_response, mock_timeline_response)
            mock_client_class.return_value.__enter__.return_value = mock_client

            fetch_github_issue(url, verbose=False)

            for call_args in mock_client.get.call_args_list:
                headers_used = call_args.kwargs.get("headers") or call_args[1].get("headers", {})
                assert headers_used.get("Authorization") == "token cfg-token-456"

    def test_fetch_returns_correct_shape(self, mock_issue_response, mock_timeline_response):
        """Fetched data has expected keys and values."""
        url = "https://github.com/owner/repo/issues/42"

        with patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class, \
             patch.object(fetch_module, "_get_config", return_value=None):

            mock_client = _make_mock_client(mock_issue_response, mock_timeline_response)
            mock_client_class.return_value.__enter__.return_value = mock_client

            result = fetch_github_issue(url, verbose=False)

        assert result["number"] == 42
        assert result["title"] == "Test Issue"
        assert result["state"] == "open"
        assert result["author"] == "testuser"
        assert set(result["labels"]) == {"bug", "help wanted"}
        assert len(result["comments"]) == 2

    def test_token_not_logged_or_printed(self, mock_issue_response, mock_timeline_response):
        """The token value must never be passed to rprint."""
        url = "https://github.com/owner/repo/issues/42"
        secret = "super-secret-token"

        with patch.object(fetch_module, "rprint") as mock_rprint, \
             patch("aye.plugins.fetch_github_issue.httpx.Client") as mock_client_class, \
             patch.object(fetch_module, "_get_config", return_value=secret):

            mock_client = _make_mock_client(mock_issue_response, mock_timeline_response)
            mock_client_class.return_value.__enter__.return_value = mock_client

            fetch_github_issue(url, verbose=True)

            for c in mock_rprint.call_args_list:
                assert secret not in str(c)

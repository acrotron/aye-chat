import sys
import pytest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import aye.plugins.fetch_jira as fetch_module
from aye.plugins.fetch_jira import (
    FetchJiraPlugin,
    fetch_jira_ticket,
    _normalize_jira_url,
    _get_config,
)


class TestNormalizeJiraUrl:
    """Test URL normalization (board \ u2192 browse)."""

    def test_board_url_with_selected_issue(self):
        """Test converting board URL to browse URL."""
        board_url = "https://acrotron.atlassian.net/jira/software/c/projects/ACM/boards/1?selectedIssue=ACM-115"
        expected = "https://acrotron.atlassian.net/browse/ACM-115"
        
        result = _normalize_jira_url(board_url)
        
        assert result == expected

    def test_already_browse_url(self):
        """Test that browse URLs are returned unchanged."""
        browse_url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        result = _normalize_jira_url(browse_url)
        
        assert result == browse_url

    def test_url_without_selected_issue(self):
        """Test URL without selectedIssue param is returned unchanged."""
        url = "https://acrotron.atlassian.net/jira/software/c/projects/ACM/boards/1"
        
        result = _normalize_jira_url(url)
        
        assert result == url


class TestGetConfig:
    """Test configuration retrieval."""
    
    # Environment variable keys we need to control
    ENV_KEYS = ["AYE_JIRA_EMAIL", "AYE_JIRA_TOKEN"]
    
    def setup_method(self):
        """Save and clear environment variables before each test."""
        self._saved_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        
        # Clear env vars for test isolation
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)
    
    def teardown_method(self):
        """Restore original environment variables after each test."""
        for key in self.ENV_KEYS:
            original_value = self._saved_env.get(key)
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value

    def test_env_var_takes_precedence(self):
        """Test that environment variables override config file."""
        os.environ['AYE_JIRA_EMAIL'] = 'env@example.com'
        
        with patch('aye.plugins.fetch_jira.get_user_config', return_value='config@example.com'):
            result = _get_config('AYE_JIRA_EMAIL', 'jira_email')
            
            assert result == 'env@example.com'

    def test_config_file_fallback(self):
        """Test fallback to config file when env var not set."""
        # Ensure env var is not set
        assert 'AYE_JIRA_EMAIL' not in os.environ
        
        with patch('aye.plugins.fetch_jira.get_user_config', return_value='config@example.com'):
            result = _get_config('AYE_JIRA_EMAIL', 'jira_email')
            
            assert result == 'config@example.com'

    def test_returns_none_when_not_found(self):
        """Test returns None when config not found."""
        # Ensure env var is not set
        assert 'AYE_JIRA_EMAIL' not in os.environ
        
        with patch('aye.plugins.fetch_jira.get_user_config', return_value=None):
            result = _get_config('AYE_JIRA_EMAIL', 'jira_email')
            
            assert result is None

    def test_strips_whitespace(self):
        """Test that whitespace is stripped from config values."""
        os.environ['AYE_JIRA_TOKEN'] = '  token123  '
        
        result = _get_config('AYE_JIRA_TOKEN', 'jira_token')
        
        assert result == 'token123'


class TestFetchJiraTicket:
    """Test Jira ticket fetching."""

    def test_successful_fetch_with_auth(self):
        """Test successful ticket fetch with authentication."""
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        mock_response = {
            "key": "ACM-115",
            "fields": {
                "summary": "Test Issue",
                "description": "Test description",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "John Doe"},
                "reporter": {"displayName": "Jane Smith"},
                "priority": {"name": "High"},
                "labels": ["bug", "urgent"],
                "comment": {
                    "comments": [
                        {
                            "author": {"displayName": "Commenter"},
                            "body": "Test comment",
                            "created": "2024-01-01T12:00:00.000+0000",
                        }
                    ]
                },
            },
        }
        
        with patch('aye.plugins.fetch_jira._get_config') as mock_config, \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_config.side_effect = lambda env, cfg: 'test@example.com' if 'EMAIL' in env else 'token123'
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            issue_response = MagicMock()
            issue_response.json.return_value = mock_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200
            
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []
            
            mock_client.get.side_effect = [issue_response, timeline_response]
            
            result = fetch_jira_ticket(url, verbose=False)
        
        assert result["key"] == "ACM-115"
        assert result["summary"] == "Test Issue"
        assert result["status"] == "In Progress"
        assert result["assignee"] == "John Doe"
        assert result["reporter"] == "Jane Smith"
        assert result["priority"] == "High"
        assert result["labels"] == ["bug", "urgent"]
        assert len(result["comments"]) == 1
        assert result["comments"][0]["author"] == "Commenter"

    def test_verbose_output(self):
        """Test verbose output on successful fetch."""
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        mock_response = {
            "key": "ACM-115",
            "fields": {"summary": "Test"},
        }
        
        with patch.object(fetch_module, 'rprint') as mock_rprint, \
             patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            issue_response = MagicMock()
            issue_response.json.return_value = mock_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200
            
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []
            
            mock_client.get.side_effect = [issue_response, timeline_response]
            
            fetch_jira_ticket(url, verbose=True)
            
            assert mock_rprint.call_count == 1

    def test_invalid_url_format(self):
        """Test that invalid URLs return None."""
        invalid_url = "https://example.com/not-a-jira-url"
        
        result = fetch_jira_ticket(invalid_url, verbose=False)
        
        assert result is None

    def test_http_404_error(self):
        """Test handling of 404 errors."""
        import httpx
        
        url = "https://acrotron.atlassian.net/browse/ACM-999"
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            mock_response = MagicMock(status_code=404)
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=mock_response
            )
            
            mock_client.get.return_value = mock_response
            
            with pytest.raises(httpx.HTTPStatusError):
                fetch_jira_ticket(url, verbose=False)

    def test_http_500_error(self):
        """Test handling of server errors."""
        import httpx
        
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            mock_response = MagicMock(status_code=500)
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Internal Server Error", request=MagicMock(), response=mock_response
            )
            
            mock_client.get.return_value = mock_response
            
            with pytest.raises(httpx.HTTPStatusError):
                fetch_jira_ticket(url, verbose=False)

    def test_network_error(self):
        """Test handling of network errors."""
        import httpx
        
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            mock_client.get.side_effect = httpx.ConnectError("Connection failed")
            
            with pytest.raises(httpx.RequestError):
                fetch_jira_ticket(url, verbose=False)

    def test_missing_fields(self):
        """Test handling of missing optional fields."""
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        mock_response = {
            "key": "ACM-115",
            "fields": {
                "summary": "Test",
                # Missing: assignee, reporter, priority, labels, comment
            },
        }
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            issue_response = MagicMock()
            issue_response.json.return_value = mock_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200
            
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []
            
            mock_client.get.side_effect = [issue_response, timeline_response]
            
            result = fetch_jira_ticket(url, verbose=False)
        
        assert result["assignee"] == "Unassigned"
        assert result["labels"] == []
        assert result["comments"] == []

    def test_custom_timeout(self):
        """Test custom timeout parameter."""
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        mock_response = {"key": "ACM-115", "fields": {"summary": "Test"}}
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            issue_response = MagicMock()
            issue_response.json.return_value = mock_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200
            
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []
            
            mock_client.get.side_effect = [issue_response, timeline_response]
            
            result = fetch_jira_ticket(url, verbose=False, timeout=5.0)
            
            assert result["key"] == "ACM-115"


class TestFetchJiraPlugin:
    """Test plugin integration."""

    def test_plugin_metadata(self):
        """Test plugin metadata is correct."""
        plugin = FetchJiraPlugin()
        
        assert plugin.name == "process_url"
        assert plugin.version == "1.0.0"
        assert plugin.premium == "free"

    def test_on_command_success(self):
        """Test plugin command handling with successful fetch."""
        plugin = FetchJiraPlugin()
        plugin.init({"verbose": False, "debug": False})
        
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        mock_response = {
            "key": "ACM-115",
            "fields": {"summary": "Test"},
        }
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            issue_response = MagicMock()
            issue_response.json.return_value = mock_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200
            
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []
            
            mock_client.get.side_effect = [issue_response, timeline_response]
            
            result = plugin.on_command("process_url", {"url": url})
        
        assert result["status"] == "success"
        assert result["data"]["key"] == "ACM-115"

    def test_on_command_invalid_url(self):
        """Test plugin returns None for non-Jira URLs."""
        plugin = FetchJiraPlugin()
        plugin.init({"verbose": False, "debug": False})
        
        result = plugin.on_command("process_url", {"url": "https://example.com"})
        
        assert result is None

    def test_on_command_404_error(self):
        """Test plugin handles 404 gracefully."""
        import httpx
        
        plugin = FetchJiraPlugin()
        plugin.init({"verbose": False, "debug": False})
        
        url = "https://acrotron.atlassian.net/browse/ACM-999"
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            mock_response = MagicMock(status_code=404)
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=mock_response
            )
            
            mock_client.get.return_value = mock_response
            
            result = plugin.on_command("process_url", {"url": url})
        
        assert result is None

    def test_on_command_network_error(self):
        """Test plugin handles network errors gracefully."""
        import httpx
        
        plugin = FetchJiraPlugin()
        plugin.init({"verbose": False, "debug": False})
        
        url = "https://acrotron.atlassian.net/browse/ACM-115"
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            mock_client.get.side_effect = httpx.ConnectError("Connection failed")
            
            result = plugin.on_command("process_url", {"url": url})
        
        assert result is None

    def test_on_command_wrong_command(self):
        """Test plugin ignores non-process_url commands."""
        plugin = FetchJiraPlugin()
        plugin.init({"verbose": False, "debug": False})
        
        result = plugin.on_command("other_command", {"url": "https://example.com"})
        
        assert result is None

    def test_board_url_normalization_in_plugin(self):
        """Test plugin normalizes board URLs before fetching."""
        plugin = FetchJiraPlugin()
        plugin.init({"verbose": False, "debug": False})
        
        board_url = "https://acrotron.atlassian.net/jira/software/c/projects/ACM/boards/1?selectedIssue=ACM-115"
        
        mock_response = {
            "key": "ACM-115",
            "fields": {"summary": "Test"},
        }
        
        with patch('aye.plugins.fetch_jira._get_config', return_value=None), \
             patch('aye.plugins.fetch_jira.httpx.Client') as mock_client_class:
            
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            issue_response = MagicMock()
            issue_response.json.return_value = mock_response
            issue_response.raise_for_status = MagicMock()
            issue_response.status_code = 200
            
            timeline_response = MagicMock()
            timeline_response.status_code = 200
            timeline_response.json.return_value = []
            
            mock_client.get.side_effect = [issue_response, timeline_response]
            
            result = plugin.on_command("process_url", {"url": board_url})
        
        assert result["status"] == "success"
        assert result["data"]["key"] == "ACM-115"

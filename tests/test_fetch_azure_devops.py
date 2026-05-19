"""Tests for the Azure DevOps fetch plugin."""

import base64
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import httpx
import pytest

from aye.plugins.fetch_azure_devops import (
    AZURE_DEVOPS_RE,
    FetchAzureDevOpsPlugin,
    _get_config,
    _normalize_ado_url,
    fetch_azure_devops_item,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_work_item_response(
    work_item_id: str = "42",
    title: str = "Fix login bug",
    state: str = "Active",
    work_item_type: str = "Bug",
    description: str = "<p>Steps to reproduce</p>",
    assignee: str = "Jane Dev",
    priority: int = 1,
    area: str = "MyProject\\Auth",
    iteration: str = "MyProject\\Sprint 5",
    tags: str = "login; security",
) -> dict:
    return {
        "id": int(work_item_id),
        "fields": {
            "System.Title": title,
            "System.State": state,
            "System.WorkItemType": work_item_type,
            "System.Description": description,
            "System.AssignedTo": {"displayName": assignee},
            "Microsoft.VSTS.Common.Priority": priority,
            "System.AreaPath": area,
            "System.IterationPath": iteration,
            "System.Tags": tags,
        },
    }


def _make_comments_response(comments: list | None = None) -> dict:
    if comments is None:
        comments = [
            {
                "createdBy": {"displayName": "Reviewer"},
                "text": "LGTM!",
                "createdDate": "2024-01-15T10:00:00Z",
            }
        ]
    return {"comments": comments}


_CANONICAL_EDIT_URL = (
    "https://dev.azure.com/myorg/myproject/_workitems/edit/42"
)


def _mock_httpx_get(item_response: dict, comments_response: dict | None = None):
    """Return a context-manager mock for httpx.Client that answers sequential GETs."""
    item_resp = MagicMock()
    item_resp.status_code = 200
    item_resp.json.return_value = item_response
    item_resp.raise_for_status = MagicMock()

    comments_resp = MagicMock()
    comments_resp.status_code = 200 if comments_response is not None else 404
    comments_resp.json.return_value = comments_response or {}

    client_instance = MagicMock()
    # Each call to client.get() returns the next response in sequence
    client_instance.get.side_effect = [item_resp, comments_resp]
    client_ctx = MagicMock()
    client_ctx.__enter__.return_value = client_instance
    client_ctx.__exit__.return_value = False
    return client_ctx


# ---------------------------------------------------------------------------
# TestNormalizeAdoUrl
# ---------------------------------------------------------------------------

class TestNormalizeAdoUrl(TestCase):
    """Tests for _normalize_ado_url."""

    def test_canonical_edit_url_unchanged(self):
        url = "https://dev.azure.com/myorg/myproject/_workitems/edit/42"
        self.assertEqual(_normalize_ado_url(url), url)

    def test_board_url_with_workitem_param(self):
        url = (
            "https://dev.azure.com/myorg/myproject/_boards/board/t/"
            "MyTeam/Stories?workitem=42"
        )
        result = _normalize_ado_url(url)
        self.assertIn("_workitems/edit/42", result)
        self.assertIn("myorg", result)
        self.assertIn("myproject", result)

    def test_board_url_without_workitem_param_unchanged(self):
        url = "https://dev.azure.com/myorg/myproject/_boards/board/"
        result = _normalize_ado_url(url)
        # No workitem id to extract, URL is returned as-is
        self.assertEqual(result, url)

    def test_legacy_visualstudio_edit_url_normalized(self):
        url = "https://myorg.visualstudio.com/myproject/_workitems/edit/99"
        result = _normalize_ado_url(url)
        self.assertEqual(
            result,
            "https://dev.azure.com/myorg/myproject/_workitems/edit/99",
        )

    def test_unrelated_url_unchanged(self):
        url = "https://github.com/owner/repo/issues/1"
        self.assertEqual(_normalize_ado_url(url), url)

    def test_empty_string_unchanged(self):
        self.assertEqual(_normalize_ado_url(""), "")


# ---------------------------------------------------------------------------
# TestGetConfig
# ---------------------------------------------------------------------------

class TestGetConfig(TestCase):
    """Tests for _get_config."""

    def test_env_var_takes_precedence(self):
        with patch.dict("os.environ", {"AYE_ADO_TOKEN": "env_token"}):
            with patch("aye.plugins.fetch_azure_devops.get_user_config", return_value="cfg_token"):
                self.assertEqual(_get_config("ado_token"), "env_token")

    def test_config_file_fallback(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("aye.plugins.fetch_azure_devops.get_user_config", return_value="cfg_token"):
                # Ensure the env var key is absent
                import os
                os.environ.pop("AYE_ADO_TOKEN", None)
                result = _get_config("ado_token")
                self.assertEqual(result, "cfg_token")

    def test_returns_none_when_not_found(self):
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("AYE_ADO_TOKEN", None)
            with patch("aye.plugins.fetch_azure_devops.get_user_config", return_value=None):
                self.assertIsNone(_get_config("ado_token"))

    def test_strips_whitespace(self):
        with patch.dict("os.environ", {"AYE_ADO_TOKEN": "  my_token  "}):
            self.assertEqual(_get_config("ado_token"), "my_token")

    def test_empty_string_returns_none(self):
        with patch.dict("os.environ", {"AYE_ADO_TOKEN": "   "}):
            self.assertIsNone(_get_config("ado_token"))


# ---------------------------------------------------------------------------
# TestFetchAzureDevOpsItem
# ---------------------------------------------------------------------------

class TestFetchAzureDevOpsItem(TestCase):
    """Tests for fetch_azure_devops_item."""

    def test_successful_fetch_with_auth(self):
        item_resp = _make_work_item_response()
        comments_resp = _make_comments_response()

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value="mytoken"):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        self.assertEqual(result["id"], "42")
        self.assertEqual(result["title"], "Fix login bug")
        self.assertEqual(result["state"], "Active")
        self.assertEqual(result["type"], "Bug")
        self.assertEqual(result["assignee"], "Jane Dev")
        self.assertEqual(result["priority"], 1)
        self.assertEqual(result["area"], "MyProject\\Auth")
        self.assertEqual(result["iteration"], "MyProject\\Sprint 5")
        self.assertEqual(result["tags"], ["login", "security"])
        self.assertEqual(result["url"], _CANONICAL_EDIT_URL)

    def test_successful_fetch_no_auth(self):
        item_resp = _make_work_item_response()
        comments_resp = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        self.assertEqual(result["id"], "42")
        self.assertEqual(result["comments"], [])

    def test_invalid_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            fetch_azure_devops_item("https://github.com/owner/repo/issues/1")

    def test_http_404_raises(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_resp
        )
        client_instance = MagicMock()
        client_instance.get.return_value = mock_resp
        client_ctx = MagicMock()
        client_ctx.__enter__.return_value = client_instance
        client_ctx.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=client_ctx):
                with self.assertRaises(httpx.HTTPStatusError):
                    fetch_azure_devops_item(_CANONICAL_EDIT_URL)

    def test_http_401_raises(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=mock_resp
        )
        client_instance = MagicMock()
        client_instance.get.return_value = mock_resp
        client_ctx = MagicMock()
        client_ctx.__enter__.return_value = client_instance
        client_ctx.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=client_ctx):
                with self.assertRaises(httpx.HTTPStatusError):
                    fetch_azure_devops_item(_CANONICAL_EDIT_URL)

    def test_network_error_raises(self):
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.RequestError("timeout")
        client_ctx = MagicMock()
        client_ctx.__enter__.return_value = client_instance
        client_ctx.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=client_ctx):
                with self.assertRaises(httpx.RequestError):
                    fetch_azure_devops_item(_CANONICAL_EDIT_URL)

    def test_missing_optional_fields(self):
        """Work item without assignee, tags, description should not crash."""
        item_resp = {
            "id": 42,
            "fields": {
                "System.Title": "Minimal item",
                "System.State": "New",
                "System.WorkItemType": "Task",
                # No assignee, tags, description, priority, area, iteration
            },
        }
        comments_resp = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        self.assertIsNone(result["assignee"])
        self.assertIsNone(result["description"])
        self.assertEqual(result["tags"], [])

    def test_tags_parsed_as_list(self):
        item_resp = _make_work_item_response(tags="alpha; beta; gamma")
        comments_resp = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        self.assertEqual(result["tags"], ["alpha", "beta", "gamma"])

    def test_empty_tags_returns_empty_list(self):
        item_resp = _make_work_item_response(tags="")
        comments_resp = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        self.assertEqual(result["tags"], [])

    def test_comments_fetched_on_success(self):
        item_resp = _make_work_item_response()
        comments_resp = _make_comments_response([
            {
                "createdBy": {"displayName": "Alice"},
                "text": "Looks good!",
                "createdDate": "2024-02-01T09:00:00Z",
            },
            {
                "createdBy": {"displayName": "Bob"},
                "text": "Needs more tests.",
                "createdDate": "2024-02-02T11:00:00Z",
            },
        ])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        self.assertEqual(len(result["comments"]), 2)
        self.assertEqual(result["comments"][0]["author"], "Alice")
        self.assertEqual(result["comments"][0]["body"], "Looks good!")
        self.assertEqual(result["comments"][1]["author"], "Bob")

    def test_comments_network_error_raises(self):
        """A RequestError on the comments call propagates to the caller."""
        item_resp = _make_work_item_response()

        item_http_resp = MagicMock()
        item_http_resp.status_code = 200
        item_http_resp.json.return_value = item_resp
        item_http_resp.raise_for_status = MagicMock()

        # First client call succeeds, second raises network error
        client1 = MagicMock()
        client1.get.return_value = item_http_resp
        ctx1 = MagicMock()
        ctx1.__enter__.return_value = client1
        ctx1.__exit__.return_value = False

        client2 = MagicMock()
        client2.get.side_effect = httpx.RequestError("timeout")
        ctx2 = MagicMock()
        ctx2.__enter__.return_value = client2
        ctx2.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", side_effect=[ctx1, ctx2]):
                with self.assertRaises(httpx.RequestError):
                    fetch_azure_devops_item(_CANONICAL_EDIT_URL)

    def test_auth_header_set_when_pat_configured(self):
        """When a PAT is present, Authorization: Basic header is sent."""
        item_resp = _make_work_item_response()
        comments_resp = _make_comments_response([])

        captured_headers = []

        def fake_get(url, headers=None):
            captured_headers.append(headers or {})
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = item_resp if "workItems/" in url and "comments" not in url else {"comments": []}
            resp.raise_for_status = MagicMock()
            return resp

        client_instance = MagicMock()
        client_instance.get.side_effect = fake_get
        ctx = MagicMock()
        ctx.__enter__.return_value = client_instance
        ctx.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value="secret_pat"):
            with patch("httpx.Client", return_value=ctx):
                fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        expected_b64 = "Basic " + base64.b64encode(b":secret_pat").decode()
        self.assertIn("Authorization", captured_headers[0])
        self.assertEqual(captured_headers[0]["Authorization"], expected_b64)

    def test_verbose_output(self, capsys=None):
        """Verbose mode should not raise; just exercises the code path."""
        item_resp = _make_work_item_response()
        comments_resp = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL, verbose=True)

        self.assertEqual(result["id"], "42")

    def test_custom_timeout(self):
        """Custom timeout value is accepted without error."""
        item_resp = _make_work_item_response()
        comments_resp = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL, timeout=60.0)

        self.assertEqual(result["id"], "42")

    def test_assignee_as_string_field(self):
        """If AssignedTo is a plain string, it is used directly."""
        item_resp = {
            "id": 42,
            "fields": {
                "System.Title": "String assignee test",
                "System.State": "Active",
                "System.WorkItemType": "Task",
                "System.AssignedTo": "John Plain",
            },
        }
        comments_resp = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_resp, comments_resp)):
                result = fetch_azure_devops_item(_CANONICAL_EDIT_URL)

        self.assertEqual(result["assignee"], "John Plain")


# ---------------------------------------------------------------------------
# TestFetchAzureDevOpsPlugin
# ---------------------------------------------------------------------------

class TestFetchAzureDevOpsPlugin(TestCase):
    """Tests for FetchAzureDevOpsPlugin."""

    def setUp(self):
        self.plugin = FetchAzureDevOpsPlugin()
        self.plugin.init({"verbose": False, "debug": False})

    # --- Metadata ---

    def test_plugin_name(self):
        self.assertEqual(self.plugin.name, "process_url")

    def test_plugin_version(self):
        self.assertIsInstance(self.plugin.version, str)
        self.assertTrue(len(self.plugin.version) > 0)

    def test_plugin_premium(self):
        self.assertEqual(self.plugin.premium, "free")

    # --- Success path ---

    def test_on_command_success(self):
        item_data = _make_work_item_response()
        comments_data = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_data, comments_data)):
                result = self.plugin.on_command(
                    "process_url", {"url": _CANONICAL_EDIT_URL}
                )

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        self.assertIn("data", result)
        self.assertEqual(result["data"]["id"], "42")

    # --- Non-matching URLs return None ---

    def test_on_command_non_ado_url_returns_none(self):
        result = self.plugin.on_command(
            "process_url", {"url": "https://github.com/owner/repo/issues/1"}
        )
        self.assertIsNone(result)

    def test_on_command_invalid_url_returns_none(self):
        result = self.plugin.on_command(
            "process_url", {"url": "not-a-url"}
        )
        self.assertIsNone(result)

    def test_on_command_empty_url_returns_none(self):
        result = self.plugin.on_command("process_url", {"url": ""})
        self.assertIsNone(result)

    # --- Error handling ---

    def test_on_command_404_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_resp
        )
        client_instance = MagicMock()
        client_instance.get.return_value = mock_resp
        ctx = MagicMock()
        ctx.__enter__.return_value = client_instance
        ctx.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=ctx):
                result = self.plugin.on_command(
                    "process_url", {"url": _CANONICAL_EDIT_URL}
                )

        self.assertIsNone(result)

    def test_on_command_401_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=mock_resp
        )
        client_instance = MagicMock()
        client_instance.get.return_value = mock_resp
        ctx = MagicMock()
        ctx.__enter__.return_value = client_instance
        ctx.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=ctx):
                result = self.plugin.on_command(
                    "process_url", {"url": _CANONICAL_EDIT_URL}
                )

        self.assertIsNone(result)

    def test_on_command_network_error_returns_none(self):
        client_instance = MagicMock()
        client_instance.get.side_effect = httpx.RequestError("Connection refused")
        ctx = MagicMock()
        ctx.__enter__.return_value = client_instance
        ctx.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=ctx):
                result = self.plugin.on_command(
                    "process_url", {"url": _CANONICAL_EDIT_URL}
                )

        self.assertIsNone(result)

    def test_on_command_comments_network_error_returns_none(self):
        """Network error on the comments call propagates through the plugin and returns None."""
        item_resp = _make_work_item_response()

        item_http_resp = MagicMock()
        item_http_resp.status_code = 200
        item_http_resp.json.return_value = item_resp
        item_http_resp.raise_for_status = MagicMock()

        client1 = MagicMock()
        client1.get.return_value = item_http_resp
        ctx1 = MagicMock()
        ctx1.__enter__.return_value = client1
        ctx1.__exit__.return_value = False

        client2 = MagicMock()
        client2.get.side_effect = httpx.RequestError("timeout on comments")
        ctx2 = MagicMock()
        ctx2.__enter__.return_value = client2
        ctx2.__exit__.return_value = False

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", side_effect=[ctx1, ctx2]):
                result = self.plugin.on_command(
                    "process_url", {"url": _CANONICAL_EDIT_URL}
                )

        self.assertIsNone(result)

    def test_on_command_wrong_command_returns_none(self):
        result = self.plugin.on_command(
            "some_other_command", {"url": _CANONICAL_EDIT_URL}
        )
        self.assertIsNone(result)

    # --- URL normalization integration ---

    def test_board_url_normalization_in_plugin(self):
        board_url = (
            "https://dev.azure.com/myorg/myproject/_boards/board/t/"
            "MyTeam/Stories?workitem=42"
        )
        item_data = _make_work_item_response()
        comments_data = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_data, comments_data)):
                result = self.plugin.on_command("process_url", {"url": board_url})

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")

    def test_legacy_visualstudio_url_in_plugin(self):
        legacy_url = "https://myorg.visualstudio.com/myproject/_workitems/edit/42"
        item_data = _make_work_item_response()
        comments_data = _make_comments_response([])

        with patch("aye.plugins.fetch_azure_devops._get_config", return_value=None):
            with patch("httpx.Client", return_value=_mock_httpx_get(item_data, comments_data)):
                result = self.plugin.on_command("process_url", {"url": legacy_url})

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["id"], "42")

    # --- Regex smoke tests ---

    def test_azure_devops_re_matches_canonical(self):
        self.assertIsNotNone(AZURE_DEVOPS_RE.match(_CANONICAL_EDIT_URL))

    def test_azure_devops_re_rejects_github(self):
        self.assertIsNone(
            AZURE_DEVOPS_RE.match("https://github.com/owner/repo/issues/1")
        )

    def test_azure_devops_re_rejects_boards_url(self):
        """Board URLs without ?workitem= should not match the regex directly."""
        self.assertIsNone(
            AZURE_DEVOPS_RE.match(
                "https://dev.azure.com/myorg/myproject/_boards/board/"
            )
        )

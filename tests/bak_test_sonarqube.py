"""Tests for the SonarQube findings fetch plugin (`sq`).

Follows the test plan in src/aye/sq.md and the conventions used by
tests/test_fetch_github_issue.py. Uses httpx.MockTransport to avoid real
network calls, without adding any new dependencies.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest

import aye.plugins.sonarqube as sq_module
from aye.plugins.sonarqube import (
    FetchSonarQubeFindingsPlugin,
    fetch_sonarqube_findings,
    _parse_issues_url,
    _parse_cli_flags,
    _derive_file,
    _normalize_issue,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class _RequestRecorder:
    """Tiny helper that captures all httpx requests made through a MockTransport."""

    def __init__(self, responder):
        self.calls: List[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._responder(request, len(self.calls))


def _make_issue(key: str, file_rel: str = "src/foo.py", project: str = "my_project") -> Dict[str, Any]:
    return {
        "key": key,
        "rule": "python:S1234",
        "severity": "CRITICAL",
        "type": "BUG",
        "status": "OPEN",
        "component": f"{project}:{file_rel}",
        "line": 42,
        "message": "Fix me",
        "effort": "10min",
        "tags": ["suspicious"],
        "creationDate": "2024-01-01T00:00:00+0000",
        "updateDate": "2024-01-02T00:00:00+0000",
    }


def _install_mock_transport(monkeypatch, responder):
    """Patch httpx.Client so fetch_sonarqube_findings uses our mock transport.

    We patch httpx.Client inside the plugin module to inject a MockTransport.
    """
    recorder = _RequestRecorder(responder)
    transport = httpx.MockTransport(recorder)

    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(sq_module.httpx, "Client", _client_factory)
    return recorder


@pytest.fixture(autouse=True)
_def_env_cleanup = None  # sentinel so linters don't complain if fixture unused

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure SonarQube-related env vars don't bleed across tests."""
    for key in (
        "AYE_SONARQUBE_URL",
        "AYE_SONARQUBE_TOKEN",
        "AYE_SSLVERIFY",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------

class TestParseIssuesUrl:
    def test_parses_sonarcloud_url_with_filters(self):
        url = (
            "https://sonarcloud.io/project/issues"
            "?id=my_project&severities=CRITICAL&types=BUG&statuses=OPEN"
            "&resolved=false&branch=main&pullRequest=42&open=ABC123"
        )
        origin, extracted = _parse_issues_url(url)
        assert origin == "https://sonarcloud.io"
        assert extracted["project_key"] == "my_project"
        assert extracted["severities"] == "CRITICAL"
        assert extracted["types"] == "BUG"
        assert extracted["statuses"] == "OPEN"
        assert extracted["resolved"] is False
        assert extracted["branch"] == "main"
        assert extracted["pull_request"] == "42"
        # `open=` is intentionally ignored in v1
        assert "open" not in extracted

    def test_parses_self_hosted_sonarqube_url(self):
        url = "https://sonar.example.com/project/issues?id=acme_proj"
        origin, extracted = _parse_issues_url(url)
        assert origin == "https://sonar.example.com"
        assert extracted == {"project_key": "acme_proj"}

    def test_non_issues_url_returns_none_origin(self):
        url = "https://sonarcloud.io/dashboard?id=my_project"
        origin, extracted = _parse_issues_url(url)
        assert origin is None
        assert extracted == {}

    def test_malformed_url_returns_none(self):
        origin, extracted = _parse_issues_url("not a url")
        assert origin is None
        assert extracted == {}

    def test_resolved_true_value_preserved(self):
        url = "https://sonarcloud.io/project/issues?id=p&resolved=true"
        _, extracted = _parse_issues_url(url)
        assert extracted["resolved"] is True


class TestParseCliFlags:
    def test_splits_positional_and_flags(self):
        positional, flags = _parse_cli_flags([
            "my_project",
            "--severities=CRITICAL,BLOCKER",
            "--types=BUG",
            "--verbose",
        ])
        assert positional == ["my_project"]
        assert flags == {
            "severities": "CRITICAL,BLOCKER",
            "types": "BUG",
            "verbose": "true",
        }

    def test_ignores_non_strings(self):
        positional, flags = _parse_cli_flags(["key", 123, None, "--a=1"])  # type: ignore[list-item]
        assert positional == ["key"]
        assert flags == {"a": "1"}


class TestDeriveFileAndNormalize:
    def test_derive_file_with_colon(self):
        assert _derive_file("my_project:src/foo.py") == "src/foo.py"

    def test_derive_file_without_colon(self):
        assert _derive_file("src/foo.py") == "src/foo.py"

    def test_derive_file_none(self):
        assert _derive_file(None) is None
        assert _derive_file("") is None

    def test_normalize_issue_derives_file(self):
        raw = _make_issue("AX1", file_rel="pkg/mod.py", project="proj")
        out = _normalize_issue(raw)
        assert out["key"] == "AX1"
        assert out["component"] == "proj:pkg/mod.py"
        assert out["file"] == "pkg/mod.py"
        assert out["tags"] == ["suspicious"]

    def test_normalize_issue_defaults_tags_to_empty_list(self):
        raw = {"key": "K", "component": "p:f.py"}
        out = _normalize_issue(raw)
        assert out["tags"] == []
        assert out["file"] == "f.py"


# ---------------------------------------------------------------------------
# fetch_sonarqube_findings: core API-level behavior
# ---------------------------------------------------------------------------

class TestFetchSonarqubeFindings:
    def test_project_key_input_sends_expected_query(self, monkeypatch):
        captured: Dict[str, Any] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["params"] = dict(request.url.params)
            captured["headers"] = dict(request.headers)
            body = {
                "total": 1,
                "issues": [_make_issue("I1")],
            }
            return httpx.Response(200, json=body)

        _install_mock_transport(monkeypatch, responder)

        data = fetch_sonarqube_findings(
            project_key="my_project",
            server_url="https://sonar.example.com",
        )

        assert captured["params"]["componentKeys"] == "my_project"
        assert captured["params"]["resolved"] == "false"
        assert captured["params"]["p"] == "1"
        assert captured["params"]["ps"] == str(50)  # default page_size
        assert data["total"] == 1
        assert len(data["issues"]) == 1
        assert data["issues"][0]["file"] == "src/foo.py"
        assert data["server"] == "https://sonar.example.com"
        assert data["project"] == {"key": "my_project"}

    def test_server_url_trailing_slash_stripped(self, monkeypatch):
        captured: Dict[str, Any] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)

        fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com/",
        )
        assert captured["url"].startswith("https://sonar.example.com/api/issues/search")

    def test_authentication_applied_when_token_present(self, monkeypatch):
        captured_headers: Dict[str, str] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)

        fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com",
            token="my-token",
        )
        # httpx BasicAuth sends an Authorization: Basic ... header
        assert "authorization" in {k.lower() for k in captured_headers.keys()}
        auth_val = captured_headers.get("authorization") or captured_headers.get("Authorization")
        assert auth_val is not None
        assert auth_val.lower().startswith("basic ")

    def test_no_authorization_header_when_no_token(self, monkeypatch):
        captured_headers: Dict[str, str] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)

        fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com",
            token=None,
        )
        lowercased = {k.lower() for k in captured_headers.keys()}
        assert "authorization" not in lowercased

    def test_http_error_raises(self, monkeypatch):
        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            return httpx.Response(401, json={"errors": [{"msg": "unauthorized"}]})

        _install_mock_transport(monkeypatch, responder)

        with pytest.raises(httpx.HTTPStatusError):
            fetch_sonarqube_findings(
                project_key="p",
                server_url="https://sonar.example.com",
            )

    def test_filters_severities_types_statuses_branch_pr(self, monkeypatch):
        captured: Dict[str, Any] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)

        fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com",
            severities=["CRITICAL", "BLOCKER"],
            types=["BUG"],
            statuses=["OPEN", "CONFIRMED"],
            branch="main",
            pull_request="42",
            resolved=True,
        )
        p = captured["params"]
        assert p["severities"] == "CRITICAL,BLOCKER"
        assert p["types"] == "BUG"
        assert p["statuses"] == "OPEN,CONFIRMED"
        assert p["branch"] == "main"
        assert p["pullRequest"] == "42"
        assert p["resolved"] == "true"

    def test_page_size_clamped_to_server_max(self, monkeypatch):
        captured: Dict[str, Any] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)

        fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com",
            page_size=10_000,
        )
        assert captured["params"]["ps"] == str(500)  # SONAR_MAX_PAGE_SIZE

    def test_missing_project_key_raises(self):
        with pytest.raises(ValueError):
            fetch_sonarqube_findings(project_key=None, server_url="https://x")

    def test_missing_server_url_raises(self):
        with pytest.raises(ValueError):
            fetch_sonarqube_findings(project_key="p", server_url=None)

    def test_pagination_max_pages(self, monkeypatch):
        pages_seen: List[str] = []

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            pages_seen.append(dict(request.url.params).get("p"))
            # Always report lots more available to force continuation.
            return httpx.Response(
                200,
                json={
                    "total": 10_000,
                    "issues": [_make_issue(f"K{call_num}-{i}") for i in range(50)],
                },
            )

        _install_mock_transport(monkeypatch, responder)

        data = fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com",
            page_size=50,
            max_pages=2,
            max_total=10_000,
        )
        assert pages_seen == ["1", "2"]
        assert len(data["issues"]) == 100  # 2 pages x 50

    def test_pagination_max_total_caps_results(self, monkeypatch):
        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "total": 10_000,
                    "issues": [_make_issue(f"K{call_num}-{i}") for i in range(50)],
                },
            )

        _install_mock_transport(monkeypatch, responder)

        data = fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com",
            page_size=50,
            max_pages=10,
            max_total=30,
        )
        assert len(data["issues"]) == 30

    def test_stops_when_server_exhausted(self, monkeypatch):
        """When the server returns fewer than ps and total<=fetched, we stop."""
        call_counter = {"n": 0}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            call_counter["n"] += 1
            # Only one page total.
            return httpx.Response(
                200,
                json={
                    "total": 3,
                    "issues": [_make_issue(f"K{i}") for i in range(3)],
                },
            )

        _install_mock_transport(monkeypatch, responder)

        data = fetch_sonarqube_findings(
            project_key="p",
            server_url="https://sonar.example.com",
            page_size=50,
            max_pages=5,
        )
        assert call_counter["n"] == 1
        assert len(data["issues"]) == 3


# ---------------------------------------------------------------------------
# Plugin on_command behavior
# ---------------------------------------------------------------------------

class TestPluginOnCommand:
    def _make_plugin(self) -> FetchSonarQubeFindingsPlugin:
        p = FetchSonarQubeFindingsPlugin()
        p.init({"verbose": False})
        return p

    def test_ignores_unknown_command(self):
        p = self._make_plugin()
        assert p.on_command("something_else", {}) is None

    def test_missing_project_key_returns_error(self, monkeypatch):
        p = self._make_plugin()
        # No input, no configured server URL either.
        result = p.on_command("sq", {})
        assert result is not None
        assert result["status"] == "error"
        assert "project key is required" in result["summary"].lower()

    def test_missing_server_url_returns_error(self, monkeypatch):
        p = self._make_plugin()
        result = p.on_command("sq", {"input": "my_project"})
        assert result is not None
        assert result["status"] == "error"
        assert "server url" in result["summary"].lower()

    def test_project_key_input_success(self, monkeypatch):
        captured: Dict[str, Any] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"total": 1, "issues": [_make_issue("K1")]})

        _install_mock_transport(monkeypatch, responder)
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://sonar.example.com")

        p = self._make_plugin()
        result = p.on_command("sq", {"input": "my_project"})

        assert result is not None
        assert result["status"] == "success"
        assert captured["params"]["componentKeys"] == "my_project"
        assert captured["params"]["resolved"] == "false"
        assert result["data"]["project"] == {"key": "my_project"}
        assert "Fetched 1 SonarQube issue(s)" in result["summary"]

    def test_url_input_extracts_server_and_filters(self, monkeypatch):
        captured: Dict[str, Any] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured["host"] = request.url.host
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)
        # Even if env says something else, URL-derived origin should win.
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://will-be-overridden.example")

        p = self._make_plugin()
        url = (
            "https://sonarcloud.io/project/issues"
            "?id=my_project&severities=CRITICAL"
        )
        result = p.on_command("sq", {"input": url})

        assert result["status"] == "success"
        assert captured["host"] == "sonarcloud.io"
        assert captured["params"]["componentKeys"] == "my_project"
        assert captured["params"]["severities"] == "CRITICAL"

    def test_cli_flags_via_tokens(self, monkeypatch):
        captured: Dict[str, Any] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://sonar.example.com")

        p = self._make_plugin()
        result = p.on_command(
            "sq",
            {
                "tokens": [
                    "my_project",
                    "--severities=CRITICAL,BLOCKER",
                    "--types=BUG,VULNERABILITY",
                    "--statuses=OPEN,CONFIRMED",
                ]
            },
        )

        assert result["status"] == "success"
        p_params = captured["params"]
        assert p_params["componentKeys"] == "my_project"
        assert p_params["severities"] == "CRITICAL,BLOCKER"
        assert p_params["types"] == "BUG,VULNERABILITY"
        assert p_params["statuses"] == "OPEN,CONFIRMED"

    def test_token_from_env_is_used(self, monkeypatch):
        captured_headers: Dict[str, str] = {}

        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://sonar.example.com")
        monkeypatch.setenv("AYE_SONARQUBE_TOKEN", "env-token")

        p = self._make_plugin()
        result = p.on_command("sq", {"input": "my_project"})
        assert result["status"] == "success"
        lowered = {k.lower() for k in captured_headers.keys()}
        assert "authorization" in lowered

    def test_http_401_returns_friendly_auth_error(self, monkeypatch):
        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            return httpx.Response(401, json={"errors": [{"msg": "unauthorized"}]})

        _install_mock_transport(monkeypatch, responder)
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://sonar.example.com")

        p = self._make_plugin()
        result = p.on_command("sq", {"input": "my_project"})

        assert result["status"] == "error"
        assert "401" in result["summary"]
        assert "authentication" in result["summary"].lower()

    def test_http_403_returns_friendly_auth_error(self, monkeypatch):
        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            return httpx.Response(403, json={"errors": [{"msg": "forbidden"}]})

        _install_mock_transport(monkeypatch, responder)
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://sonar.example.com")

        p = self._make_plugin()
        result = p.on_command("sq", {"input": "my_project"})

        assert result["status"] == "error"
        assert "403" in result["summary"]
        assert "authentication" in result["summary"].lower()

    def test_http_500_returns_generic_http_error(self, monkeypatch):
        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            return httpx.Response(500, text="boom")

        _install_mock_transport(monkeypatch, responder)
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://sonar.example.com")

        p = self._make_plugin()
        result = p.on_command("sq", {"input": "my_project"})
        assert result["status"] == "error"
        assert "500" in result["summary"]

    def test_network_error_returns_friendly_error(self, monkeypatch):
        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        _install_mock_transport(monkeypatch, responder)
        monkeypatch.setenv("AYE_SONARQUBE_URL", "https://sonar.example.com")

        p = self._make_plugin()
        result = p.on_command("sq", {"input": "my_project"})
        assert result["status"] == "error"
        assert "network" in result["summary"].lower()

    def test_value_error_from_core_is_reported(self, monkeypatch):
        """If core raises ValueError (missing project key / url), plugin surfaces it."""
        # Simulate by passing only a URL that doesn't contain `id=` and no configured URL.
        def responder(request: httpx.Request, call_num: int) -> httpx.Response:
            return httpx.Response(200, json={"total": 0, "issues": []})

        _install_mock_transport(monkeypatch, responder)

        p = self._make_plugin()
        result = p.on_command(
            "sq",
            {"input": "https://sonarcloud.io/project/issues"},  # no id= param
        )
        assert result["status"] == "error"
        # Server URL is derivable, but project key is not  
        assert "project key" in result["summary"].lower()

    def test_sslverify_config_respected(self, monkeypatch):
        monkeypatch.setenv("AYE_SSLVERIFY", "off")
        assert sq_module._ssl_verify() is False

        monkeypatch.setenv("AYE_SSLVERIFY", "on")
        assert sq_module._ssl_verify() is True

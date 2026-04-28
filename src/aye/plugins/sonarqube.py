"""SonarQube findings fetch plugin.

Fetches issues (\"findings\") from a SonarQube or SonarCloud instance via the
SonarQube Web API (`/api/issues/search`) and returns structured JSON suitable
for injecting into the chat context.

Command name: ``sq``

Usage (from the REPL):
    sq <projectKey>
    sq <sonarqube-issues-url>
    sq <projectKey> --severities=CRITICAL --types=BUG,VULNERABILITY
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from rich import print as _rich_print

try:
    # Preferred import path (matches other plugins in this package).
    from aye.model.auth import get_user_config
except Exception:  # pragma: no cover - fallback if package layout differs
    try:
        from ..model.auth import get_user_config  # type: ignore
    except Exception:  # pragma: no cover
        def get_user_config(key: str, default: Optional[str] = None) -> Optional[str]:
            return default

from .plugin_base import Plugin

# Module-level alias so tests can monkey-patch output easily.
rprint = _rich_print

DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 50
SONAR_MAX_PAGE_SIZE = 500
DEFAULT_MAX_PAGES = 1
DEFAULT_MAX_TOTAL = 500

_ISSUES_URL_RE = re.compile(r"^https?://[^/]+/project/issues(?:\?.*)?$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ssl_verify() -> bool:
    """Return the SSL verification flag, honoring env and user config."""
    env_val = os.environ.get("AYE_SSLVERIFY")
    if env_val is not None:
        return env_val.strip().lower() not in ("off", "false", "0", "no")
    cfg_val = get_user_config("sslverify", "on") or "on"
    return str(cfg_val).strip().lower() not in ("off", "false", "0", "no")


def _get_config(env_key: str, cfg_key: str) -> Optional[str]:
    val = os.environ.get(env_key)
    if val:
        return val.strip()
    cfg_val = get_user_config(cfg_key, None)
    if cfg_val:
        return str(cfg_val).strip()
    return None


def _as_list(value: Any) -> Optional[List[str]]:
    """Normalize a list-or-comma-string param to a list of non-empty strings."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value if str(v).strip()]
        return items or None
    if isinstance(value, str):
        items = [s.strip() for s in value.split(",") if s.strip()]
        return items or None
    return [str(value).strip()]


def _as_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_url(s: str) -> bool:
    return isinstance(s, str) and s.strip().lower().startswith(("http://", "https://"))


def _parse_issues_url(url: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Parse a SonarQube issues URL.

    Returns a tuple ``(server_origin, extracted_params)``. ``server_origin``
    is ``scheme://host[:port]`` or ``None`` if the URL is not a recognized
    issues URL. ``extracted_params`` may contain keys: ``project_key``,
    ``severities``, ``types``, ``statuses``, ``resolved``, ``branch``,
    ``pull_request``.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None, {}
    if not parsed.scheme or not parsed.netloc:
        return None, {}
    # Accept any `/project/issues` path; the query string carries the filters.
    if not parsed.path.rstrip("/").endswith("/project/issues"):
        return None, {}

    origin = f"{parsed.scheme}://{parsed.netloc}"
    qs = parse_qs(parsed.query or "", keep_blank_values=False)

    def _first(key: str) -> Optional[str]:
        vals = qs.get(key)
        if not vals:
            return None
        return vals[0]

    extracted: Dict[str, Any] = {}
    pkey = _first("id")
    if pkey:
        extracted["project_key"] = pkey

    for src_key, dst_key in (("severities", "severities"), ("types", "types"), ("statuses", "statuses")):
        val = _first(src_key)
        if val:
            extracted[dst_key] = val  # comma string; normalized later

    resolved = _first("resolved")
    if resolved is not None:
        b = _as_bool(resolved)
        if b is not None:
            extracted["resolved"] = b

    branch = _first("branch")
    if branch:
        extracted["branch"] = branch

    pr = _first("pullRequest")
    if pr:
        extracted["pull_request"] = pr

    # `open=<issueKey>` is intentionally ignored in v1.
    return origin, extracted


def _parse_cli_flags(tokens: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """Split a list of tokens into positional args and ``--key=value`` flags."""
    positional: List[str] = []
    flags: Dict[str, str] = {}
    for tok in tokens:
        if not isinstance(tok, str):
            continue
        if tok.startswith("--") and "=" in tok:
            k, _, v = tok[2:].partition("=")
            k = k.strip()
            if k:
                flags[k] = v.strip()
        elif tok.startswith("--"):
            flags[tok[2:].strip()] = "true"
        else:
            positional.append(tok)
    return positional, flags


def _derive_file(component: Optional[str]) -> Optional[str]:
    if not component or not isinstance(component, str):
        return None
    # SonarQube components commonly look like ``<projectKey>:path/to/file``.
    if ":" in component:
        return component.split(":", 1)[1] or None
    return component


def _normalize_issue(raw: Dict[str, Any]) -> Dict[str, Any]:
    component = raw.get("component")
    return {
        "key": raw.get("key"),
        "rule": raw.get("rule"),
        "severity": raw.get("severity"),
        "type": raw.get("type"),
        "status": raw.get("status"),
        "component": component,
        "file": _derive_file(component),
        "line": raw.get("line"),
        "message": raw.get("message"),
        "effort": raw.get("effort"),
        "tags": raw.get("tags") or [],
        "creationDate": raw.get("creationDate"),
        "updateDate": raw.get("updateDate"),
    }


# ---------------------------------------------------------------------------
# Core fetch
# ---------------------------------------------------------------------------
def fetch_sonarqube_findings(
    *,
    project_key: Optional[str] = None,
    server_url: Optional[str] = None,
    token: Optional[str] = None,
    severities: Optional[List[str]] = None,
    types: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    resolved: bool = False,
    branch: Optional[str] = None,
    pull_request: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total: int = DEFAULT_MAX_TOTAL,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Fetch issues from the SonarQube Web API.

    Raises:
        ValueError: If required inputs (server URL or project key) are missing.
        httpx.HTTPStatusError: On non-2xx responses.
        httpx.RequestError: On network/transport errors.
    """
    if not project_key:
        raise ValueError("SonarQube project key is required.")
    if not server_url:
        raise ValueError(
            "SonarQube server URL is not configured. Set AYE_SONARQUBE_URL or "
            "'sonarqube_url' in ~/.ayecfg, or pass a full SonarQube issues URL."
        )

    server_url = server_url.rstrip("/")
    ps = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), SONAR_MAX_PAGE_SIZE))
    mp = max(1, int(max_pages or DEFAULT_MAX_PAGES))
    mt = max(1, int(max_total or DEFAULT_MAX_TOTAL))

    base_params: Dict[str, Any] = {
        "componentKeys": project_key,
        "resolved": "true" if resolved else "false",
        "ps": ps,
    }
    if severities:
        base_params["severities"] = ",".join(severities)
    if types:
        base_params["types"] = ",".join(types)
    if statuses:
        base_params["statuses"] = ",".join(statuses)
    if branch:
        base_params["branch"] = branch
    if pull_request:
        base_params["pullRequest"] = str(pull_request)

    auth = (token, "") if token else None
    verify = _ssl_verify()

    if verbose:
        rprint(f"[cyan]SonarQube[/] server: {server_url}")
        rprint(f"[cyan]SonarQube[/] project: {project_key}")
        safe_filters = {k: v for k, v in base_params.items() if k != "componentKeys"}
        rprint(f"[cyan]SonarQube[/] filters: {safe_filters}")

    collected: List[Dict[str, Any]] = []
    total: Optional[int] = None
    page = 1
    endpoint = f"{server_url}/api/issues/search"

    with httpx.Client(timeout=DEFAULT_TIMEOUT, verify=verify, auth=auth) as client:
        while page <= mp and len(collected) < mt:
            params = dict(base_params)
            params["p"] = page
            resp = client.get(endpoint, params=params)
            resp.raise_for_status()
            payload = resp.json() or {}

            if total is None:
                total = int(payload.get("total", 0) or 0)

            raw_issues = payload.get("issues") or []
            for raw in raw_issues:
                if len(collected) >= mt:
                    break
                collected.append(_normalize_issue(raw))

            # Stop if we've exhausted server-side results.
            fetched_so_far = (page - 1) * ps + len(raw_issues)
            if not raw_issues or (total is not None and fetched_so_far >= total):
                break
            page += 1

    if verbose:
        rprint(f"[cyan]SonarQube[/] fetched {len(collected)} issue(s) (total={total})")

    query_echo: Dict[str, Any] = {"resolved": resolved}
    if severities:
        query_echo["severities"] = list(severities)
    if types:
        query_echo["types"] = list(types)
    if statuses:
        query_echo["statuses"] = list(statuses)
    if branch:
        query_echo["branch"] = branch
    if pull_request:
        query_echo["pullRequest"] = str(pull_request)

    return {
        "server": server_url,
        "project": {"key": project_key},
        "query": query_echo,
        "total": total if total is not None else len(collected),
        "page": 1,
        "page_size": ps,
        "issues": collected,
    }


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class FetchSonarQubeFindingsPlugin(Plugin):
    """Aye Chat plugin: fetch SonarQube findings for a project."""

    name = "sq"
    version = "1.0.0"
    premium = "free"

    def on_command(
        self, command_name: str, params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if command_name != self.name:
            return None

        params = dict(params or {})

        # ------------------------------------------------------------------
        # 1) Collect the raw textual input (either a project key or URL).
        # ------------------------------------------------------------------
        raw_input: Optional[str] = None
        if isinstance(params.get("input"), str) and params["input"].strip():
            raw_input = params["input"].strip()
        elif isinstance(params.get("url"), str) and params["url"].strip():
            raw_input = params["url"].strip()

        # Some dispatchers pass tokens/args lists. Use them to recover
        # positional input and --key=value flags the user typed.
        extra_tokens: List[str] = []
        for key in ("tokens", "args", "argv"):
            val = params.get(key)
            if isinstance(val, (list, tuple)):
                extra_tokens.extend([str(t) for t in val])

        if extra_tokens:
            positional, flags = _parse_cli_flags(extra_tokens)
            if raw_input is None and positional:
                raw_input = positional[0]
            # Promote flags into params if the caller didn't already set them.
            for fk, fv in flags.items():
                params.setdefault(fk, fv)

        # ------------------------------------------------------------------
        # 2) Resolve project key and server URL.
        # ------------------------------------------------------------------
        server_url: Optional[str] = None
        project_key: Optional[str] = params.get("project_key") or None

        if raw_input and _looks_like_url(raw_input):
            origin, extracted = _parse_issues_url(raw_input)
            if origin:
                server_url = origin
            if extracted.get("project_key") and not project_key:
                project_key = extracted["project_key"]
            # Only fill in filters the caller didn't already set explicitly.
            for k in ("severities", "types", "statuses", "resolved", "branch", "pull_request"):
                if k in extracted and params.get(k) in (None, ""):
                    params[k] = extracted[k]
        elif raw_input and not project_key:
            project_key = raw_input

        if not server_url:
            server_url = _get_config("AYE_SONARQUBE_URL", "sonarqube_url")

        token = _get_config("AYE_SONARQUBE_TOKEN", "sonarqube_token")

        # ------------------------------------------------------------------
        # 3) Normalize remaining params.
        # ------------------------------------------------------------------
        severities = _as_list(params.get("severities"))
        types_ = _as_list(params.get("types"))
        statuses = _as_list(params.get("statuses"))

        resolved_val = _as_bool(params.get("resolved"))
        resolved = False if resolved_val is None else resolved_val

        branch = params.get("branch") or None
        if branch is not None:
            branch = str(branch).strip() or None

        pull_request = params.get("pull_request") or None
        if pull_request is not None:
            pull_request = str(pull_request).strip() or None

        page_size = _as_int(params.get("page_size")) or DEFAULT_PAGE_SIZE
        max_pages = _as_int(params.get("max_pages")) or DEFAULT_MAX_PAGES
        max_total = _as_int(params.get("max_total")) or DEFAULT_MAX_TOTAL

        verbose_val = _as_bool(params.get("verbose"))
        verbose = bool(verbose_val) if verbose_val is not None else False

        # ------------------------------------------------------------------
        # 4) Validate + fetch.
        # ------------------------------------------------------------------
        if not project_key:
            return {
                "status": "error",
                "summary": (
                    "SonarQube project key is required. "
                    "Usage: sq <projectKey> or sq <sonarqube-issues-url>"
                ),
            }
        if not server_url:
            return {
                "status": "error",
                "summary": (
                    "SonarQube server URL is not configured. Set AYE_SONARQUBE_URL "
                    "or 'sonarqube_url' in ~/.ayecfg, or pass a full SonarQube "
                    "issues URL."
                ),
            }

        try:
            data = fetch_sonarqube_findings(
                project_key=project_key,
                server_url=server_url,
                token=token,
                severities=severities,
                types=types_,
                statuses=statuses,
                resolved=resolved,
                branch=branch,
                pull_request=pull_request,
                page_size=page_size,
                max_pages=max_pages,
                max_total=max_total,
                verbose=verbose,
            )
        except ValueError as e:
            return {"status": "error", "summary": str(e)}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else "?"
            if status in (401, 403):
                summary = (
                    f"SonarQube authentication failed (HTTP {status}). "
                    "Provide a token via AYE_SONARQUBE_TOKEN or 'sonarqube_token' "
                    "in ~/.ayecfg, or ensure the project allows anonymous read."
                )
            else:
                summary = f"SonarQube API returned HTTP {status}."
            return {"status": "error", "summary": summary}
        except httpx.RequestError as e:
            return {
                "status": "error",
                "summary": f"Network error contacting SonarQube: {e}",
            }
        except Exception as e:  # defensive: surface unexpected failures cleanly
            return {
                "status": "error",
                "summary": f"Unexpected error fetching SonarQube findings: {e}",
            }

        issues = data.get("issues") or []
        total = data.get("total", len(issues))
        summary = (
            f"Fetched {len(issues)} SonarQube issue(s) for '{project_key}' "
            f"(total available: {total})."
        )
        return {"status": "success", "summary": summary, "data": data}

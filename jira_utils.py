"""Shared Jira helpers used across export and validation scripts."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import requests

from jira_auth import is_jira_cloud_url


def create_jira_session(
    jira_url: str,
    api_token: str,
    email: str = "",
) -> requests.Session:
    """Create a requests session with Jira auth headers (Cloud or Server)."""
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    if is_jira_cloud_url(jira_url) and email:
        session.auth = (email, api_token)
    else:
        session.headers["Authorization"] = f"Bearer {api_token}"
    return session


def jira_api_version(jira_url: str) -> str:
    """Return '3' for Cloud, '2' for Server/Data Center."""
    return "3" if is_jira_cloud_url(jira_url) else "2"


def jira_search_paginated(
    session: requests.Session,
    jira_url: str,
    jql: str,
    fields_param: str,
    *,
    max_results: int = 50,
    timeout: int = 60,
) -> List[Dict[str, Any]]:
    """Paginate a JQL search across Cloud v3 and Server v2."""
    issues: List[Dict[str, Any]] = []
    is_cloud = is_jira_cloud_url(jira_url)
    base = jira_url.rstrip("/")

    if is_cloud:
        url = f"{base}/rest/api/3/search/jql"
        token: Optional[str] = None
        while True:
            params: Dict[str, Any] = {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields_param,
            }
            if token:
                params["nextPageToken"] = token
            r = session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            issues.extend(data.get("issues") or [])
            if data.get("isLast", True):
                break
            token = data.get("nextPageToken")
            if not token:
                break
    else:
        api_version = "2"
        url = f"{base}/rest/api/{api_version}/search"
        start_at = 0
        while True:
            r = session.get(
                url,
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "fields": fields_param,
                },
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
            chunk = data.get("issues") or []
            issues.extend(chunk)
            if len(chunk) < max_results:
                break
            start_at += max_results

    return issues


def flatten_value(val: Any) -> str:
    """Convert a Jira field value to a CSV-safe string."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return val.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    if isinstance(val, dict):
        for k in ("name", "displayName", "key", "value"):
            if k in val and val[k] is not None:
                return str(val[k]).replace("\n", " ")
        return json.dumps(val)[:500]
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, dict):
                p = item.get("name") or item.get("displayName") or item.get("key")
                parts.append(str(p) if p is not None else str(item))
            else:
                parts.append(str(item))
        return " | ".join(str(p) for p in parts if p)
    return str(val)


def extract_linked_keys(issuelinks: List[Dict], prefix: str) -> List[str]:
    """Extract linked issue keys matching a project prefix (e.g. 'CIPOE', 'ROX', 'RFE')."""
    keys = []
    for link in issuelinks or []:
        for direction in ("inwardIssue", "outwardIssue"):
            issue = link.get(direction)
            if issue and isinstance(issue, dict):
                key = issue.get("key", "")
                if key and key.upper().startswith(prefix.upper()):
                    keys.append(key)
    return list(dict.fromkeys(keys))


def fetch_cipoe_summary(
    cipoe_key: str,
    jira_url: str,
    session: requests.Session,
    api_version: str,
    cache: Dict[str, str],
) -> str:
    """Fetch CIPOE issue summary (customer name) with caching."""
    if cipoe_key in cache:
        return cache[cipoe_key]
    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{cipoe_key}",
            params={"fields": "summary"},
            timeout=15,
        )
        if resp.status_code == 200:
            summary = resp.json().get("fields", {}).get("summary", "")
            cache[cipoe_key] = summary or cipoe_key
            return cache[cipoe_key]
    except Exception:
        pass
    cache[cipoe_key] = cipoe_key
    return cipoe_key

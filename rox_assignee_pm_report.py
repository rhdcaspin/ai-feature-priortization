#!/usr/bin/env python3
"""
Export ROX issues where Assignee OR Product Manager matches a person and status is not Done/Closed.

Uses JIRA_TOKEN (or JIRA_API_TOKEN) / JIRA_BASE_URL / JIRA_EMAIL from .env (same as jira_feature_validator.py).
Resolves the person via Jira user search (display name), then runs two JQL queries and merges
(OR semantics) so JQL user-picker quirks are avoided.

Usage:
    python3 rox_assignee_pm_report.py --name "Anjali Talang"
    python3 rox_assignee_pm_report.py --name "Anjali Talang" -o report.csv
    python3 rox_assignee_pm_report.py --account-id "557058:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from jira_auth import is_jira_cloud_url, jira_api_token_from_env  # noqa: E402
from jira_feature_validator import JiraFeatureValidator  # noqa: E402


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def jql_quoted_user(uid: str) -> str:
    """Quote accountId / username for JQL string literal."""
    esc = (uid or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{esc}"'


def _display_name(user: Optional[Dict]) -> str:
    if not user or not isinstance(user, dict):
        return ""
    return (
        user.get("displayName")
        or user.get("name")
        or user.get("emailAddress")
        or ""
    )


def search_users(
    session: requests.Session,
    jira_url: str,
    api_version: str,
    query: str,
) -> List[Dict[str, Any]]:
    """Return users from /user/search (Cloud) or compatible."""
    q = (query or "").strip()
    if not q:
        return []
    url = f"{jira_url.rstrip('/')}/rest/api/{api_version}/user/search"
    resp = session.get(url, params={"query": q, "maxResults": 50}, timeout=30)
    if resp.status_code != 200:
        print(f"⚠️  user/search returned {resp.status_code}: {resp.text[:200]}")
        return []
    data = resp.json()
    return data if isinstance(data, list) else []


def _name_fold(s: str) -> str:
    """Normalize minor spelling variants (e.g. Talang vs Telang)."""
    n = _norm(s)
    return n.replace("talang", "telang")


def _names_match(wanted: str, display: str) -> bool:
    w, d = _norm(wanted), _norm(display)
    if w == d:
        return True
    if _name_fold(w) == _name_fold(d):
        return True
    return False


def pick_account_id(
    users: List[Dict[str, Any]],
    wanted_name: str,
) -> Optional[str]:
    """Pick accountId matching display name (case-insensitive, light typo fold)."""
    if not users:
        return None
    matches = [
        u for u in users
        if _names_match(wanted_name, _display_name(u))
    ]
    if len(matches) == 1:
        return matches[0].get("accountId") or matches[0].get("name")
    if len(matches) > 1:
        print(
            "❌ Several Jira users match that name (after normalizing Talang/Telang). "
            "Pick one --account-id:"
        )
        for u in matches:
            print(f"   {_display_name(u)!r}  accountId={u.get('accountId')!r}")
        return None
    if len(users) == 1 and _names_match(wanted_name, _display_name(users[0])):
        u = users[0]
        return u.get("accountId") or u.get("name")
    return None


def jira_search_all(
    session: requests.Session,
    jira_url: str,
    is_cloud: bool,
    api_version: str,
    jql: str,
    fields: str,
) -> List[Dict[str, Any]]:
    issues: List[Dict] = []
    max_results = 50
    if is_cloud:
        url = f"{jira_url.rstrip('/')}/rest/api/3/search/jql"
        token = None
        while True:
            params: Dict[str, Any] = {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields,
            }
            if token:
                params["nextPageToken"] = token
            r = session.get(url, params=params, timeout=120)
            if r.status_code != 200:
                print(f"❌ Search failed: {r.status_code}\n{r.text[:500]}")
                return []
            data = r.json()
            issues.extend(data.get("issues") or [])
            if data.get("isLast", True):
                break
            token = data.get("nextPageToken")
            if not token:
                break
    else:
        url = f"{jira_url.rstrip('/')}/rest/api/{api_version}/search"
        start = 0
        while True:
            r = session.get(
                url,
                params={
                    "jql": jql,
                    "startAt": start,
                    "maxResults": max_results,
                    "fields": fields,
                },
                timeout=120,
            )
            if r.status_code != 200:
                print(f"❌ Search failed: {r.status_code}\n{r.text[:500]}")
                return []
            data = r.json()
            chunk = data.get("issues") or []
            issues.extend(chunk)
            if len(chunk) < max_results:
                break
            start += max_results
    return issues


def _flatten_target_version(fields: Dict, tv_field_id: str) -> str:
    tv_val = fields.get(tv_field_id)
    if isinstance(tv_val, list):
        names = [x.get("name") for x in tv_val if isinstance(x, dict) and x.get("name")]
        return " | ".join(names)
    if isinstance(tv_val, dict):
        return tv_val.get("name", "") or ""
    if tv_val:
        return str(tv_val)
    return ""


def issue_to_row(
    issue: Dict,
    pm_field: str,
    tv_field_id: str,
    jira_url: str,
) -> Dict[str, str]:
    fields = issue.get("fields") or {}
    key = issue.get("key", "")
    st = (fields.get("status") or {}).get("name", "")
    itype = (fields.get("issuetype") or {}).get("name", "")
    summ = fields.get("summary") or ""
    assignee = _display_name(fields.get("assignee"))
    pm = _display_name(fields.get(pm_field))
    tv = _flatten_target_version(fields, tv_field_id)

    labels = fields.get("labels") or []
    labels_s = " | ".join(labels) if labels else ""
    created = fields.get("created") or ""
    updated = fields.get("updated") or ""
    browse = f"{jira_url.rstrip('/')}/browse/{key}"
    return {
        "Key": key,
        "Summary": summ,
        "Status": st,
        "Issue Type": itype,
        "Assignee": assignee,
        "Product Manager": pm,
        "Target Version": tv,
        "Labels": labels_s,
        "Created": created,
        "Updated": updated,
        "URL": browse,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ROX report: Assignee OR Product Manager = person, not Done status",
    )
    parser.add_argument(
        "--name",
        default="Anjali Talang",
        help='Display name to match (default: "Anjali Talang")',
    )
    parser.add_argument(
        "--account-id",
        default="",
        help="Jira accountId (skips user search if set)",
    )
    parser.add_argument(
        "--project",
        default="ROX",
        help="Project key (default: ROX)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output CSV path (default: output/rox_<project>_<name_slug>_<timestamp>.csv)",
    )
    parser.add_argument(
        "--jira-url",
        default=os.getenv("JIRA_BASE_URL", "https://issues.redhat.com"),
    )
    parser.add_argument("--email", default=os.getenv("JIRA_EMAIL", ""))
    parser.add_argument("--token", default=jira_api_token_from_env())
    args = parser.parse_args()

    if not args.token:
        print("❌ JIRA_TOKEN or JIRA_API_TOKEN not set")
        return 1

    v = JiraFeatureValidator(
        jira_url=args.jira_url,
        email=args.email,
        api_token=args.token,
        project_key=args.project,
        target_version="5.0.0",
    )
    if not v.test_connection():
        return 1

    is_cloud = getattr(v, "is_cloud", is_jira_cloud_url(args.jira_url))
    api_version = getattr(v, "api_version", "3" if is_cloud else "2")
    pm_field = v.product_manager_field()
    jira_url = v.jira_url
    tv_field = os.getenv("JIRA_TARGET_VERSION_FIELD", "").strip()
    if not tv_field:
        tv_field = "customfield_10855" if is_jira_cloud_url(jira_url) else "customfield_12319940"

    account_id = (args.account_id or "").strip()
    if not account_id:
        users = search_users(v.session, jira_url, api_version, args.name)
        if not users:
            print(f'❌ No users found for query "{args.name}". Try --account-id from Jira profile.')
            return 1
        account_id = pick_account_id(users, args.name)
        if not account_id:
            print("❌ Could not pick a unique user. Candidates from search (use --account-id):")
            for u in users[:20]:
                print(f"   {_display_name(u)!r}  accountId={u.get('accountId')!r}")
            return 1
        print(f"✅ Using account: {account_id!r} (search name: {args.name!r})")
    else:
        print(f"✅ Using --account-id: {account_id!r}")

    # Not in Closed / not Done: status category excludes completed work
    status_filter = "statusCategory != Done"
    fields_param = (
        f"summary,status,assignee,issuetype,labels,created,updated,{pm_field},{tv_field}"
    )

    pm_jql_name = os.getenv("JIRA_PRODUCT_MANAGER_JQL", "Product Manager").strip() or "Product Manager"

    jql_uid = jql_quoted_user(account_id)

    jql_assignee = (
        f"project = {args.project} AND {status_filter} AND assignee = {jql_uid}"
    )
    jql_pm = (
        f'project = {args.project} AND {status_filter} '
        f'AND "{pm_jql_name}" = {jql_uid}'
    )

    print(f"🔍 Query A (assignee): {jql_assignee}")
    issues_a = jira_search_all(
        v.session, jira_url, is_cloud, api_version, jql_assignee, fields_param,
    )
    print(f"   → {len(issues_a)} issues")

    print(f"🔍 Query B (Product Manager): {jql_pm}")
    issues_b = jira_search_all(
        v.session, jira_url, is_cloud, api_version, jql_pm, fields_param,
    )
    print(f"   → {len(issues_b)} issues")

    by_key: Dict[str, Dict] = {}
    for issue in issues_a + issues_b:
        k = issue.get("key")
        if k:
            by_key[k] = issue

    rows = [
        issue_to_row(issue, pm_field, tv_field, jira_url)
        for issue in by_key.values()
    ]
    rows.sort(key=lambda r: r["Key"])

    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    if args.output:
        out_path = args.output
    else:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", args.name.strip())[:40].strip("_")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"rox_{args.project}_{slug}_{ts}.csv"

    fieldnames = [
        "Key",
        "Summary",
        "Status",
        "Issue Type",
        "Assignee",
        "Product Manager",
        "Target Version",
        "Labels",
        "Created",
        "Updated",
        "URL",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\n📄 Report: {out_path}  ({len(rows)} issues)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

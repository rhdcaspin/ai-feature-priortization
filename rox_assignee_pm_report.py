#!/usr/bin/env python3
"""
Export ROX issues where Assignee OR Product Manager (and optionally Reporter) matches a person
and status is not Done/Closed.

Uses JIRA_TOKEN (or JIRA_API_TOKEN) / JIRA_BASE_URL / JIRA_EMAIL from .env (same as jira_feature_validator.py).
Resolves the person via Jira user search (display name), then runs JQL queries and merges
(OR semantics) so JQL user-picker quirks are avoided. Multiple Jira accounts with the same
display name (e.g. two \"Anjali Telang\" users) are all included.

Usage:
    python3 rox_assignee_pm_report.py --name "Anjali Talang"
    python3 rox_assignee_pm_report.py --name "Anjali Talang" -o report.csv
    python3 rox_assignee_pm_report.py --account-id "557058:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    python3 rox_assignee_pm_report.py --name "Anjali Telang" --include-reporter \\
        --google-sheet-title "ROX open - Anjali Telang"
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
from jira_feature_validator import (  # noqa: E402
    JiraFeatureValidator,
    _get_gcloud_access_token,
    upload_csv_to_google_sheet,
)


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


def resolve_account_ids(
    users: List[Dict[str, Any]],
    wanted_name: str,
) -> Optional[List[str]]:
    """Resolve one or more accountIds matching display name (case-insensitive, Talang/Telang fold)."""
    if not users:
        return None
    matches = [
        u for u in users
        if _names_match(wanted_name, _display_name(u))
    ]
    if not matches:
        return None
    if len(matches) == 1:
        uid = matches[0].get("accountId") or matches[0].get("name")
        return [uid] if uid else None
    # Same person may have multiple Atlassian accounts with identical displayName
    norm_names = {_norm(_display_name(u)) for u in matches}
    if len(norm_names) == 1:
        ids = [u.get("accountId") for u in matches if u.get("accountId")]
        return ids or None
    print(
        "❌ Several Jira users match that name with different display names. "
        "Pick one --account-id:"
    )
    for u in matches:
        print(f"   {_display_name(u)!r}  accountId={u.get('accountId')!r}")
    return None


def jql_user_in(field: str, account_ids: List[str]) -> str:
    """JQL fragment: field in (\"id1\", \"id2\") for user fields."""
    inner = ", ".join(jql_quoted_user(uid) for uid in account_ids if uid)
    return f"{field} in ({inner})" if inner else ""


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
    reporter = _display_name(fields.get("reporter"))
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
        "Reporter": reporter,
        "Assignee": assignee,
        "Product Manager": pm,
        "Target Version": tv,
        "Labels": labels_s,
        "Created": created,
        "Updated": updated,
        "URL": browse,
    }


def create_google_spreadsheet(title: str) -> Optional[tuple[str, str]]:
    """Create a new spreadsheet; returns (spreadsheet_id, spreadsheetUrl) or None."""
    token = _get_gcloud_access_token()
    if not token:
        print(
            "⚠️  Could not get gcloud access token.\n"
            "   Run: gcloud auth login --enable-gdrive-access"
        )
        return None
    r = requests.post(
        "https://sheets.googleapis.com/v4/spreadsheets",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"properties": {"title": (title or "Jira export")[:100]}},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"⚠️  Create spreadsheet failed: {r.status_code} {r.text[:300]}")
        return None
    data = r.json()
    sid = data.get("spreadsheetId")
    surl = data.get("spreadsheetUrl") or ""
    if not sid:
        return None
    return sid, surl


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ROX report: Assignee OR Product Manager (= person), optional Reporter, not Done",
    )
    parser.add_argument(
        "--name",
        default="Anjali Talang",
        help='Display name to match (default: "Anjali Talang")',
    )
    parser.add_argument(
        "--account-id",
        default="",
        help="Jira accountId(s), comma-separated (skips user search if set)",
    )
    parser.add_argument(
        "--include-reporter",
        action="store_true",
        help="Also include issues where the person is Reporter (creator)",
    )
    parser.add_argument(
        "--google-sheet-title",
        default="",
        metavar="TITLE",
        help="Create a new Google Sheet with this title and upload the CSV (Sheet1)",
    )
    parser.add_argument(
        "--update-sheet",
        action="store_true",
        help="Upload CSV to an existing spreadsheet (needs --sheet-id and --sheet-name)",
    )
    parser.add_argument(
        "--sheet-id",
        default=os.getenv("GOOGLE_SHEET_ID", ""),
        help="Spreadsheet ID for --update-sheet (default: GOOGLE_SHEET_ID)",
    )
    parser.add_argument(
        "--sheet-name",
        default=os.getenv("GOOGLE_SHEET_NAME", "Sheet1"),
        help='Tab name for --update-sheet (default: GOOGLE_SHEET_NAME or "Sheet1")',
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

    raw_id = (args.account_id or "").strip()
    if raw_id:
        account_ids = [x.strip() for x in raw_id.split(",") if x.strip()]
        if not account_ids:
            print("❌ --account-id was empty")
            return 1
        print(f"✅ Using --account-id: {account_ids!r}")
    else:
        users = search_users(v.session, jira_url, api_version, args.name)
        if not users:
            print(f'❌ No users found for query "{args.name}". Try --account-id from Jira profile.')
            return 1
        resolved = resolve_account_ids(users, args.name)
        if not resolved:
            print("❌ Could not resolve user. Candidates from search (use --account-id):")
            for u in users[:20]:
                print(f"   {_display_name(u)!r}  accountId={u.get('accountId')!r}")
            return 1
        account_ids = resolved
        print(f"✅ Using account(s): {account_ids!r} (search name: {args.name!r})")

    # Not in Closed / not Done: status category excludes completed work
    status_filter = "statusCategory != Done"
    fields_param = (
        f"summary,status,reporter,assignee,issuetype,labels,created,updated,"
        f"{pm_field},{tv_field}"
    )

    pm_jql_name = os.getenv("JIRA_PRODUCT_MANAGER_JQL", "Product Manager").strip() or "Product Manager"

    jql_assignee = (
        f"project = {args.project} AND {status_filter} AND "
        f"{jql_user_in('assignee', account_ids)}"
    )
    inner_pm = ", ".join(jql_quoted_user(uid) for uid in account_ids)
    jql_pm = (
        f'project = {args.project} AND {status_filter} '
        f'AND "{pm_jql_name}" in ({inner_pm})'
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

    merged: List[Dict] = list(issues_a) + list(issues_b)
    if args.include_reporter:
        jql_reporter = (
            f"project = {args.project} AND {status_filter} AND "
            f"{jql_user_in('reporter', account_ids)}"
        )
        print(f"🔍 Query C (reporter): {jql_reporter}")
        issues_c = jira_search_all(
            v.session, jira_url, is_cloud, api_version, jql_reporter, fields_param,
        )
        print(f"   → {len(issues_c)} issues")
        merged.extend(issues_c)

    by_key: Dict[str, Dict] = {}
    for issue in merged:
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
        "Reporter",
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

    title = (args.google_sheet_title or "").strip()
    if title:
        created = create_google_spreadsheet(title)
        if not created:
            return 1
        sid, surl = created
        print(f"📤 New spreadsheet: {surl}")
        ok = upload_csv_to_google_sheet(
            str(out_path),
            sid,
            "Sheet1",
            jira_base_url=jira_url.rstrip("/"),
        )
        return 0 if ok else 1

    if args.update_sheet:
        sid = (args.sheet_id or "").strip()
        if not sid:
            print("❌ --update-sheet requires --sheet-id or GOOGLE_SHEET_ID")
            return 1
        tab = (args.sheet_name or "Sheet1").strip() or "Sheet1"
        print(f"\n📤 Uploading to Google Sheets tab {tab!r}…")
        ok = upload_csv_to_google_sheet(
            str(out_path),
            sid,
            tab,
            jira_base_url=jira_url.rstrip("/"),
        )
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

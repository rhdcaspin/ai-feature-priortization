#!/usr/bin/env python3
"""
RFE / ROX Mismatch Report

Finds RHACS RFE items that are still open but have linked ROX features
that are already closed.  Helps identify feature requests that may need
to be closed or updated after the corresponding ROX work has shipped.

Usage:
    python3 rfe_rox_mismatch_report.py                  # default run
    python3 rfe_rox_mismatch_report.py --skip-upload    # CSV only
    python3 rfe_rox_mismatch_report.py -o report.csv    # custom output path
"""

import os
import sys
import csv
import json
import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from jira_auth import is_jira_cloud_url, jira_api_token_from_env  # noqa: E402
from notebooklm_upload import notebooklm_upload_available, upload_csvs_to_notebook  # noqa: E402

DEFAULT_JIRA_URL = "https://issues.redhat.com"
DEFAULT_NOTEBOOK_NAME = "The Big Notebook for RHACS Product Management"

RH_SSO_TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
RH_HYDRA_CASE_URL = "https://access.redhat.com/hydra/rest/cases"

RFE_FIELD_IDS = [
    "key", "summary", "description", "status", "assignee", "reporter",
    "issuetype", "priority", "labels", "components", "fixVersions",
    "created", "updated", "resolution", "resolutiondate",
    "issuelinks", "subtasks", "parent", "project",
    "customfield_12311940",   # Rank
    "customfield_12313440",   # SFDC Cases Counter
    "customfield_12313441",   # SFDC Cases Links
    "customfield_12322244",   # PX Impact Score
]

ROX_STATUS_FIELDS = "key,summary,status,resolution,resolutiondate,fixVersions"

CLOSED_STATUSES = frozenset([
    "closed", "done", "resolved", "verified", "release pending",
])


def flatten_value(val: Any) -> str:
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


def get_rh_access_token(offline_token: str) -> Optional[str]:
    try:
        resp = requests.post(
            RH_SSO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": "rhsm-api",
                "refresh_token": offline_token,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
        print(f"  Red Hat SSO token exchange failed: {resp.status_code}")
    except Exception as e:
        print(f"  Red Hat SSO error: {e}")
    return None


def fetch_case_account_name(
    case_number: str, access_token: str, cache: Dict[str, str],
) -> str:
    if case_number in cache:
        return cache[case_number]
    try:
        resp = requests.get(
            f"{RH_HYDRA_CASE_URL}/{case_number}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            account = (
                data.get("accountName")
                or data.get("account", {}).get("name", "")
                or data.get("contactName", "")
            )
            cache[case_number] = account
            return account
        elif resp.status_code == 404:
            cache[case_number] = ""
        else:
            print(f"  Hydra API {resp.status_code} for case {case_number}")
    except Exception:
        pass
    cache[case_number] = ""
    return ""


def extract_sfdc_case_ids(rfe_fields: Dict, rfe_key: str,
                          jira_url: str, session: requests.Session,
                          api_version: str) -> List[str]:
    seen: set = set()
    case_ids: list = []

    sfdc_field = rfe_fields.get("customfield_12313441")
    if sfdc_field:
        for case_num in re.split(r"[,\s;|]+", str(sfdc_field).strip()):
            case_num = case_num.strip()
            if case_num and case_num not in seen:
                seen.add(case_num)
                case_ids.append(case_num)

    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{rfe_key}/remotelink",
            timeout=15,
        )
        if resp.status_code == 200:
            for link in resp.json():
                obj = link.get("object", {}) or {}
                url_str = obj.get("url", "") or ""
                title = obj.get("title", "") or ""
                summary = obj.get("summary", "") or ""
                combined = f"{url_str} {title} {summary}"
                if not re.search(r"salesforce|force\.com|sfdc", combined, re.IGNORECASE):
                    continue
                for text in [url_str, title, summary]:
                    m = re.search(r"500[a-zA-Z0-9]{12,15}", text)
                    if m and m.group() not in seen:
                        seen.add(m.group())
                        case_ids.append(m.group())
                        break
                else:
                    for text in [url_str, title, summary]:
                        m = re.search(r"\b(\d{7,10})\b", text)
                        if m and m.group() not in seen:
                            seen.add(m.group())
                            case_ids.append(m.group())
                            break
    except Exception:
        pass

    return case_ids


def extract_linked_keys(issuelinks: List[Dict], prefix: str) -> List[str]:
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
    cipoe_key: str, jira_url: str, session: requests.Session,
    api_version: str, cache: Dict[str, str],
) -> str:
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


def fetch_rox_issue(
    rox_key: str, jira_url: str, session: requests.Session,
    api_version: str, cache: Dict[str, Dict],
) -> Optional[Dict]:
    """Fetch a ROX issue's key fields (status, resolution, fixVersions)."""
    if rox_key in cache:
        return cache[rox_key]
    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{rox_key}",
            params={"fields": ROX_STATUS_FIELDS},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            cache[rox_key] = data
            return data
        elif resp.status_code == 404:
            cache[rox_key] = None
    except Exception:
        pass
    cache[rox_key] = None
    return None


def is_closed(status_name: str) -> bool:
    return status_name.lower().strip() in CLOSED_STATUSES


def fetch_rhacs_components(
    jira_url: str, session: requests.Session, api_version: str,
) -> List[str]:
    """Fetch all RFE project components whose name contains 'rhacs' (case-insensitive)."""
    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/project/RFE/components",
            timeout=30,
        )
        if resp.status_code == 200:
            all_components = resp.json()
            matched = [
                c["name"] for c in all_components
                if "rhacs" in c.get("name", "").lower()
            ]
            return sorted(matched)
    except Exception as e:
        print(f"  Could not fetch RFE components: {e}")
    return []


def run_report(
    jira_url: str,
    api_token: str,
    output_path: Optional[Path],
    rh_access_token: Optional[str] = None,
    jira_email: Optional[str] = None,
) -> Path:
    """Find open RHACS RFEs whose linked ROX features are closed."""
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    if is_jira_cloud_url(jira_url) and jira_email:
        session.auth = (jira_email, api_token)
    else:
        session.headers["Authorization"] = f"Bearer {api_token}"

    is_cloud = is_jira_cloud_url(jira_url)
    api_version = "3" if is_cloud else "2"

    try:
        r = session.get(f"{jira_url}/rest/api/{api_version}/serverInfo", timeout=10)
        if r.status_code == 200:
            print(f"Connected to Jira (API v{api_version})")
    except Exception:
        print(f"Connected to Jira (API v{api_version})")

    # ── 1. Fetch all open RHACS RFEs ──
    rhacs_components = fetch_rhacs_components(jira_url, session, api_version)
    if not rhacs_components:
        print("No components containing 'rhacs' found in RFE project")
        sys.exit(1)
    print(f"Found {len(rhacs_components)} RHACS components: {', '.join(rhacs_components)}")

    component_list = ", ".join(f'"{c}"' for c in rhacs_components)
    component_filter = f"component in ({component_list})"
    status_filter = 'status in (New, Open, "In Progress", "To Do", Backlog, Refinement, "Under Consideration")'
    jql = f"project = RFE AND {component_filter} AND {status_filter} ORDER BY updated DESC"

    print("Fetching open RHACS RFEs...")

    fields_param = ",".join(RFE_FIELD_IDS)
    all_rfe_issues: List[Dict] = []
    max_results = 50

    if is_cloud:
        search_url = f"{jira_url}/rest/api/3/search/jql"
        next_token = None
        while True:
            params: dict = {"jql": jql, "maxResults": max_results, "fields": fields_param}
            if next_token:
                params["nextPageToken"] = next_token
            resp = session.get(search_url, params=params, timeout=60)
            if resp.status_code != 200:
                print(f"Jira search failed: {resp.status_code}")
                print(resp.text[:500])
                sys.exit(1)
            data = resp.json()
            all_rfe_issues.extend(data.get("issues", []))
            print(f"  Fetched {len(all_rfe_issues)} RFEs...", end="\r")
            if data.get("isLast", True):
                break
            next_token = data.get("nextPageToken")
            if not next_token:
                break
    else:
        search_url = f"{jira_url}/rest/api/{api_version}/search"
        start_at = 0
        while True:
            resp = session.get(search_url, params={
                "jql": jql, "startAt": start_at,
                "maxResults": max_results, "fields": fields_param,
            }, timeout=60)
            if resp.status_code != 200:
                print(f"Jira search failed: {resp.status_code}")
                print(resp.text[:500])
                sys.exit(1)
            data = resp.json()
            issues = data.get("issues", [])
            all_rfe_issues.extend(issues)
            print(f"  Fetched {len(all_rfe_issues)}/{data.get('total', '?')} RFEs...", end="\r")
            if len(issues) < max_results:
                break
            start_at += max_results

    print(f"Retrieved {len(all_rfe_issues)} open RHACS RFEs          ")

    if not all_rfe_issues:
        print("No open RHACS RFEs found")
        if output_path is None:
            output_path = Path(__file__).parent / "rfe_rox_mismatch_empty.csv"
        output_path.write_text(
            "rfe_key,rfe_summary,rfe_status,rox_key,rox_summary,rox_status,rox_resolution,rox_fix_versions\n",
            encoding="utf-8",
        )
        return output_path

    # ── 2. For each RFE, check linked ROX features ──
    rox_cache: Dict[str, Optional[Dict]] = {}
    cipoe_cache: Dict[str, str] = {}
    case_account_cache: Dict[str, str] = {}

    if rh_access_token:
        print("Red Hat API token available - will look up SFDC account names")

    rows = []
    rfe_with_links = 0
    mismatches_found = 0

    for idx, rfe_issue in enumerate(all_rfe_issues):
        rfe_fields = rfe_issue.get("fields", {})
        rfe_key = rfe_issue.get("key", "")
        rfe_summary = flatten_value(rfe_fields.get("summary"))
        rfe_status = flatten_value(rfe_fields.get("status"))
        rfe_priority = flatten_value(rfe_fields.get("priority"))
        rfe_components = flatten_value(rfe_fields.get("components"))
        rfe_rank = flatten_value(rfe_fields.get("customfield_12311940"))
        rfe_px_score = flatten_value(rfe_fields.get("customfield_12322244"))
        rfe_created = flatten_value(rfe_fields.get("created"))
        rfe_updated = flatten_value(rfe_fields.get("updated"))
        issuelinks = rfe_fields.get("issuelinks", [])

        if (idx + 1) % 20 == 0:
            print(f"  Processing RFE {idx + 1}/{len(all_rfe_issues)}...", end="\r")

        rox_keys = extract_linked_keys(issuelinks, "ROX")
        if not rox_keys:
            continue

        rfe_with_links += 1

        # SFDC enrichment
        sfdc_ids = extract_sfdc_case_ids(rfe_fields, rfe_key, jira_url, session, api_version)
        sfdc_accounts = []
        if rh_access_token and sfdc_ids:
            for cid in sfdc_ids:
                acct = fetch_case_account_name(cid, rh_access_token, case_account_cache)
                if acct:
                    sfdc_accounts.append(f"{cid}: {acct}")
                else:
                    sfdc_accounts.append(cid)

        cipoe_keys = extract_linked_keys(issuelinks, "CIPOE")
        customer_names = []
        for ck in cipoe_keys:
            name = fetch_cipoe_summary(ck, jira_url, session, api_version, cipoe_cache)
            if name:
                customer_names.append(f"{ck}: {name}")

        # Check each linked ROX feature
        for rox_key in rox_keys:
            rox_issue = fetch_rox_issue(rox_key, jira_url, session, api_version, rox_cache)
            if rox_issue is None:
                continue

            rox_fields = rox_issue.get("fields", {})
            rox_status_name = flatten_value(rox_fields.get("status"))

            if not is_closed(rox_status_name):
                continue

            mismatches_found += 1
            rows.append({
                "rfe_key": rfe_key,
                "rfe_summary": rfe_summary,
                "rfe_status": rfe_status,
                "rfe_priority": rfe_priority,
                "rfe_components": rfe_components,
                "rfe_rank": rfe_rank,
                "rfe_px_impact_score": rfe_px_score,
                "rfe_created": rfe_created,
                "rfe_updated": rfe_updated,
                "rox_key": rox_key,
                "rox_summary": flatten_value(rox_fields.get("summary")),
                "rox_status": rox_status_name,
                "rox_resolution": flatten_value(rox_fields.get("resolution")),
                "rox_resolution_date": flatten_value(rox_fields.get("resolutiondate")),
                "rox_fix_versions": flatten_value(rox_fields.get("fixVersions")),
                "sfdc_case_ids": " | ".join(sfdc_ids) if sfdc_ids else "",
                "sfdc_accounts": " | ".join(sfdc_accounts) if sfdc_accounts else "",
                "cipoe_customers": " | ".join(customer_names) if customer_names else "",
                "jira_rfe_url": f"{jira_url}/browse/{rfe_key}",
                "jira_rox_url": f"{jira_url}/browse/{rox_key}",
            })

    print(f"  Processed {len(all_rfe_issues)} RFEs                    ")
    print(f"  RFEs with ROX links: {rfe_with_links}")
    print(f"  Mismatches found: {mismatches_found} (open RFE + closed ROX)")

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(__file__).parent / f"rfe_rox_mismatch_{timestamp}.csv"

    if rows:
        fieldnames = list(rows[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    else:
        output_path.write_text(
            "rfe_key,rfe_summary,rfe_status,rox_key,rox_summary,rox_status,rox_resolution,rox_fix_versions\n",
            encoding="utf-8",
        )

    print(f"CSV saved: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Report open RHACS RFEs whose linked ROX features are closed",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--skip-upload", action="store_true",
        help="Generate CSV only, skip NotebookLM upload",
    )
    parser.add_argument(
        "--notebook-name", default=DEFAULT_NOTEBOOK_NAME,
        help=f"NotebookLM notebook name (default: {DEFAULT_NOTEBOOK_NAME})",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output CSV path (default: rfe_rox_mismatch_YYYYMMDD_HHMMSS.csv)",
    )
    args = parser.parse_args()

    token = jira_api_token_from_env()
    if not token:
        print("JIRA_TOKEN or JIRA_API_TOKEN environment variable not set")
        sys.exit(1)

    jira_url = os.getenv("JIRA_BASE_URL", DEFAULT_JIRA_URL)
    jira_email = os.getenv("JIRA_EMAIL", "")

    rh_access_token = None
    rh_offline_token = os.getenv("RH_OFFLINE_TOKEN", "").strip()
    if rh_offline_token:
        rh_access_token = get_rh_access_token(rh_offline_token)
        if not rh_access_token:
            print("Could not get Red Hat API access token - account names will be unavailable")
    else:
        print("RH_OFFLINE_TOKEN not set - SFDC account names will not be resolved")

    csv_path = run_report(
        jira_url=jira_url,
        api_token=token,
        output_path=args.output,
        rh_access_token=rh_access_token,
        jira_email=jira_email,
    )

    if not args.skip_upload:
        if not notebooklm_upload_available():
            print(
                "NotebookLM upload unavailable: install notebooklm-mcp-cli and run `nlm login`, "
                "or pip install 'notebooklm-py[browser]' and run `notebooklm login`"
            )
            sys.exit(1)
        if not upload_csvs_to_notebook([csv_path], args.notebook_name):
            sys.exit(1)
    else:
        print("Skipping NotebookLM upload (--skip-upload)")


if __name__ == "__main__":
    main()

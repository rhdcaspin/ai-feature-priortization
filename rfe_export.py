#!/usr/bin/env python3
"""
RFE (Feature Request) Export to CSV and NotebookLM

Exports Feature Requests from the RFE Jira project whose component names
contain "rhacs".  Supports incremental daily runs (only new/updated RFEs
since last run) and full exports.

Data enrichment per RFE:
  - SFDC case IDs and customer account names (via Red Hat Hydra API)
  - Linked CIPOE customer names
  - Linked ROX feature keys

Usage:
    python3 rfe_export.py                  # incremental (since last run)
    python3 rfe_export.py --force-all      # all open RHACS RFEs
    python3 rfe_export.py --all-rfes       # every RHACS RFE regardless of status
    python3 rfe_export.py --skip-upload    # CSV only, no NotebookLM upload
"""

import os
import sys
import csv
import json
import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from jira_auth import is_jira_cloud_url, jira_api_token_from_env  # noqa: E402
from jira_utils import flatten_value, extract_linked_keys, fetch_cipoe_summary  # noqa: E402
from rh_api import get_rh_access_token, fetch_case_account_name, extract_sfdc_case_ids  # noqa: E402
from notebooklm_upload import notebooklm_upload_available, upload_csvs_to_notebook  # noqa: E402

DEFAULT_STATE_FILE = Path(__file__).parent / ".rfe_export_last_run"
DEFAULT_JIRA_URL = "https://issues.redhat.com"
DEFAULT_NOTEBOOK_NAME = "The Big Notebook for RHACS Product Management"

RHACS_COMPONENTS = [
    "rhacs",
    "rhacs-Auth-Authz",
    "rhacs-compliance",
    "rhacs-documentation",
    "rhacs-integration",
    "rhacs-network-graph",
    "rhacs-observability",
    "rhacs-operator",
    "rhacs-policy",
    "rhacs-risk",
    "rhacs-runtime",
    "rhacs-scanner",
    "rhacs-ui",
    "rhacs-vuln-management",
]

FIELD_IDS = [
    "key", "summary", "description", "status", "assignee", "reporter",
    "issuetype", "priority", "labels", "components", "fixVersions",
    "created", "updated", "resolution", "resolutiondate",
    "issuelinks", "subtasks", "parent", "project",
    "customfield_12311940",   # Rank
    "customfield_12313440",   # SFDC Cases Counter
    "customfield_12313441",   # SFDC Cases Links
    "customfield_12316542",   # Ready
    "customfield_12316543",   # Blocked
    "customfield_12316544",   # Blocked Reason
    "customfield_12320040",   # Activity Type
    "customfield_12320845",   # Color Status
    "customfield_12320946",   # Intelligence Requested
    "customfield_12320947",   # Market
    "customfield_12322244",   # PX Impact Score
    "customfield_12324540",   # SFDC Cases Open
]


def load_last_run(state_file: Path) -> Optional[str]:
    if not state_file.exists():
        return None
    try:
        return state_file.read_text().strip()
    except Exception:
        return None


def save_last_run(state_file: Path, timestamp: str) -> None:
    try:
        state_file.write_text(timestamp)
    except Exception as e:
        print(f"  Could not save state file: {e}")


def run_export(
    jira_url: str,
    api_token: str,
    state_file: Path,
    force_all: bool,
    all_rfes: bool,
    output_path: Optional[Path],
    rh_access_token: Optional[str] = None,
    jira_email: Optional[str] = None,
) -> Path:
    """Export RHACS RFEs to CSV. Returns path to created CSV."""
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
        else:
            print(f"Connected to Jira (API v{api_version}, serverInfo returned {r.status_code})")
    except Exception:
        print(f"Connected to Jira (API v{api_version})")

    component_list = ", ".join(f'"{c}"' for c in RHACS_COMPONENTS)
    component_filter = f"component in ({component_list})"

    if all_rfes:
        jql = f"project = RFE AND {component_filter} ORDER BY updated DESC"
        print("Exporting ALL RHACS RFEs (--all-rfes)")
    else:
        status_filter = 'status in (New, Open, "In Progress", "To Do", Backlog, Refinement, "Under Consideration")'
        base_filter = f"project = RFE AND {component_filter} AND {status_filter}"

        if force_all:
            jql = f"{base_filter} ORDER BY updated DESC"
            print("Exporting open RHACS RFEs (--force-all)")
        else:
            last_run = load_last_run(state_file)
            if last_run:
                try:
                    dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                    jql = f'{base_filter} AND updated >= "{date_str}" ORDER BY updated DESC'
                    print(f"Exporting RHACS RFEs updated since {last_run}")
                except Exception:
                    jql = f"{base_filter} ORDER BY updated DESC"
                    print("Exporting open RHACS RFEs (invalid state file)")
            else:
                since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                jql = f'{base_filter} AND updated >= "{since}" ORDER BY updated DESC'
                print(f"First run: exporting RHACS RFEs updated since {since}")

    fields_param = ",".join(FIELD_IDS)

    all_issues = []
    max_results = 50

    if is_cloud:
        search_url = f"{jira_url}/rest/api/3/search/jql"
        next_token = None
        while True:
            params: dict = {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields_param,
            }
            if next_token:
                params["nextPageToken"] = next_token
            resp = session.get(search_url, params=params, timeout=60)
            if resp.status_code != 200:
                print(f"Jira search failed: {resp.status_code}")
                print(resp.text[:500])
                sys.exit(1)
            data = resp.json()
            all_issues.extend(data.get("issues", []))
            print(f"  Fetched {len(all_issues)} RFEs...", end="\r")
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
            all_issues.extend(issues)
            print(f"  Fetched {len(all_issues)}/{data.get('total', '?')} RFEs...", end="\r")
            if len(issues) < max_results:
                break
            start_at += max_results

    print(f"Retrieved {len(all_issues)} RHACS RFEs       ")

    if not all_issues:
        print("No RFEs to export")
        if output_path is None:
            output_path = Path(__file__).parent / "rfe_export_empty.csv"
        output_path.write_text(
            "key,summary,status,components,_sfdc_case_ids,_sfdc_accounts,_cipoe_customers,_rox_keys\n",
            encoding="utf-8",
        )
        return output_path

    cipoe_cache: Dict[str, str] = {}
    case_account_cache: Dict[str, str] = {}

    if rh_access_token:
        print("Red Hat API token available - will look up SFDC account names")

    rows = []
    for idx, issue in enumerate(all_issues):
        fields = issue.get("fields", {})
        issue_key = issue.get("key", "")
        issuelinks = fields.get("issuelinks", [])

        if (idx + 1) % 20 == 0:
            print(f"  Processing {idx + 1}/{len(all_issues)}...", end="\r")

        row = {"key": issue_key}
        for fid, fval in fields.items():
            row[fid] = flatten_value(fval)

        # SFDC case IDs from custom field + remote links
        sfdc_ids = extract_sfdc_case_ids(
            fields, issue_key, jira_url, session, api_version
        )
        row["_sfdc_case_ids"] = " | ".join(sfdc_ids) if sfdc_ids else ""

        # Resolve SFDC account names via Hydra API
        sfdc_accounts = []
        if rh_access_token and sfdc_ids:
            for cid in sfdc_ids:
                acct = fetch_case_account_name(cid, rh_access_token, case_account_cache)
                if acct:
                    sfdc_accounts.append(f"{cid}: {acct}")
                else:
                    sfdc_accounts.append(cid)
        row["_sfdc_accounts"] = " | ".join(sfdc_accounts) if sfdc_accounts else ""

        # CIPOE customer names
        cipoe_keys = extract_linked_keys(issuelinks, "CIPOE")
        customer_names = []
        for ck in cipoe_keys:
            name = fetch_cipoe_summary(
                ck, jira_url, session, api_version, cipoe_cache
            )
            if name:
                customer_names.append(f"{ck}: {name}")
        row["_cipoe_customers"] = " | ".join(customer_names) if customer_names else ""

        # Linked ROX features
        rox_keys = extract_linked_keys(issuelinks, "ROX")
        row["_rox_keys"] = " | ".join(rox_keys) if rox_keys else ""

        rows.append(row)

    print(f"  Processed {len(all_issues)} RFEs              ")

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(__file__).parent / f"rfe_rhacs_export_{timestamp}.csv"

    if rows:
        all_keys = list(rows[0].keys())
        priority_cols = [
            "key", "_sfdc_case_ids", "_sfdc_accounts",
            "_cipoe_customers", "_rox_keys",
        ]
        for col in priority_cols:
            if col in all_keys:
                all_keys.remove(col)
        all_keys = [c for c in priority_cols if c in rows[0]] + all_keys

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    print(f"CSV saved: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Export RHACS Feature Requests (RFE) to CSV and upload to NotebookLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--skip-upload", action="store_true",
        help="Generate CSV only, skip NotebookLM upload",
    )
    parser.add_argument(
        "--notebook-name",
        default=os.getenv("NOTEBOOKLM_NOTEBOOK_NAME", DEFAULT_NOTEBOOK_NAME),
        help="NotebookLM notebook name (default: NOTEBOOKLM_NOTEBOOK_NAME or "
        f"{DEFAULT_NOTEBOOK_NAME!r})",
    )
    parser.add_argument(
        "--force-all", action="store_true",
        help="Export all open RHACS RFEs (ignore last-run state)",
    )
    parser.add_argument(
        "--all-rfes", action="store_true",
        help="Export ALL RHACS RFEs regardless of status or date",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output CSV path (default: rfe_rhacs_export_YYYYMMDD_HHMMSS.csv)",
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

    csv_path = run_export(
        jira_url=jira_url,
        api_token=token,
        state_file=DEFAULT_STATE_FILE,
        force_all=args.force_all,
        all_rfes=args.all_rfes,
        output_path=args.output,
        rh_access_token=rh_access_token,
        jira_email=jira_email,
    )

    if not args.force_all and not args.all_rfes:
        save_last_run(DEFAULT_STATE_FILE, datetime.now().isoformat())

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

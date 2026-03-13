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

try:
    import asyncio
    from notebooklm import NotebookLMClient
    NOTEBOOKLM_AVAILABLE = True
except ImportError:
    NOTEBOOKLM_AVAILABLE = False

DEFAULT_STATE_FILE = Path(__file__).parent / ".rfe_export_last_run"
DEFAULT_JIRA_URL = "https://issues.redhat.com"
DEFAULT_NOTEBOOK_NAME = "ACS RICE Scoring and Prioritization Framework"

RH_SSO_TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
RH_HYDRA_CASE_URL = "https://access.redhat.com/hydra/rest/cases"

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


def get_rh_access_token(offline_token: str) -> Optional[str]:
    """Exchange a Red Hat offline token for a short-lived access token."""
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
    """Look up the account/customer name for a support case via Hydra API."""
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
    """Extract SFDC case IDs from custom fields and remote links of an RFE."""
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
    """Extract linked issue keys matching a project prefix (e.g. 'CIPOE', 'ROX')."""
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
) -> Path:
    """Export RHACS RFEs to CSV. Returns path to created CSV."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })

    api_version = "2"
    for v in ("2", "3"):
        try:
            r = session.get(f"{jira_url}/rest/api/{v}/serverInfo", timeout=10)
            if r.status_code == 200:
                api_version = v
                break
        except Exception:
            continue

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
    start_at = 0
    max_results = 50

    while True:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/search",
            params={
                "jql": jql,
                "startAt": start_at,
                "maxResults": max_results,
                "fields": fields_param,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"Jira search failed: {resp.status_code}")
            print(resp.text[:500])
            sys.exit(1)

        data = resp.json()
        issues = data.get("issues", [])
        all_issues.extend(issues)

        total = data.get("total", 0)
        print(f"  Fetched {len(all_issues)}/{total} RFEs...", end="\r")

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


async def upload_to_notebooklm(csv_path: Path, notebook_name: str) -> bool:
    """Upload CSV file to NotebookLM. Returns True on success."""
    if not NOTEBOOKLM_AVAILABLE:
        print("notebooklm-py not installed. Run: pip install 'notebooklm-py[browser]'")
        return False

    try:
        async with await NotebookLMClient.from_storage() as client:
            notebooks = await client.notebooks.list()
            nb = None
            for n in notebooks:
                if n.title == notebook_name:
                    nb = n
                    break
            if nb is None:
                nb = await client.notebooks.create(notebook_name)
                print(f"Created NotebookLM notebook: {notebook_name}")
            else:
                print(f"Using existing NotebookLM notebook: {notebook_name}")

            await client.sources.add_file(nb.id, str(csv_path), wait=True)
            print(f"Uploaded {csv_path.name} to NotebookLM")
            return True
    except Exception as e:
        print(f"NotebookLM upload failed: {e}")
        print("   Ensure you have run 'notebooklm login' first")
        return False


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
        "--notebook-name", default=DEFAULT_NOTEBOOK_NAME,
        help=f"NotebookLM notebook name (default: {DEFAULT_NOTEBOOK_NAME})",
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

    token = os.getenv("JIRA_TOKEN")
    if not token:
        print("JIRA_TOKEN environment variable not set")
        print("Get a token from: https://issues.redhat.com/secure/ViewProfile.jspa"
              "?selectedTab=com.atlassian.pats.pats-plugin:jira-user-personal-access-tokens")
        sys.exit(1)

    rh_access_token = None
    rh_offline_token = os.getenv("RH_OFFLINE_TOKEN", "").strip()
    if rh_offline_token:
        rh_access_token = get_rh_access_token(rh_offline_token)
        if not rh_access_token:
            print("Could not get Red Hat API access token - account names will be unavailable")
    else:
        print("RH_OFFLINE_TOKEN not set - SFDC account names will not be resolved")

    csv_path = run_export(
        jira_url=DEFAULT_JIRA_URL,
        api_token=token,
        state_file=DEFAULT_STATE_FILE,
        force_all=args.force_all,
        all_rfes=args.all_rfes,
        output_path=args.output,
        rh_access_token=rh_access_token,
    )

    if not args.force_all and not args.all_rfes:
        save_last_run(DEFAULT_STATE_FILE, datetime.now().isoformat())

    if not args.skip_upload:
        if NOTEBOOKLM_AVAILABLE:
            success = asyncio.run(upload_to_notebooklm(csv_path, args.notebook_name))
            if not success:
                sys.exit(1)
        else:
            print("Install notebooklm-py for upload: pip install 'notebooklm-py[browser]'")
            sys.exit(1)
    else:
        print("Skipping NotebookLM upload (--skip-upload)")


if __name__ == "__main__":
    main()

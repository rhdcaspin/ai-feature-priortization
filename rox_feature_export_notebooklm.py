#!/usr/bin/env python3
"""
ROX Feature Export to CSV and NotebookLM

Exports ROX project features updated since the last script run to CSV,
including key Jira fields and customer names from CIPOE (direct links and
via related RFE feature requests).
Uploads the CSV to Google NotebookLM.

Usage:
    export JIRA_TOKEN=your_token   # or JIRA_API_TOKEN
    python3 rox_feature_export_notebooklm.py

    # First run: exports features updated in last 30 days
    # Subsequent runs: exports only features updated since last run

Options:
    --skip-upload     Generate CSV only, skip NotebookLM upload
    --drive-folder-id Upload results to Google Drive folder (replaces file each run)
    --notebook-name   Name of NotebookLM notebook (default: The Big Notebook for RHACS Product Management)
    --force-all      Ignore last-run state; export open features only
    --all-features    Export ALL features (all statuses, no date filter)
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
from notebooklm_upload import notebooklm_upload_available, upload_csvs_to_notebook  # noqa: E402

# State file to track last run timestamp
DEFAULT_STATE_FILE = Path(__file__).parent / ".rox_export_last_run"
DEFAULT_JIRA_URL = "https://issues.redhat.com"
DEFAULT_NOTEBOOK_NAME = "The Big Notebook for RHACS Product Management"

# Red Hat SSO / Hydra API for case account lookup
RH_SSO_TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
RH_HYDRA_CASE_URL = "https://access.redhat.com/hydra/rest/cases"


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
        print(f"⚠️  Red Hat SSO token exchange failed: {resp.status_code}")
    except Exception as e:
        print(f"⚠️  Red Hat SSO error: {e}")
    return None


def fetch_case_account_name(
    case_number: str,
    access_token: str,
    cache: Dict[str, str],
) -> str:
    """Look up the account/customer name for a support case via the Hydra API."""
    if case_number in cache:
        return cache[case_number]
    try:
        resp = requests.get(
            f"{RH_HYDRA_CASE_URL}/{case_number}",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
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
            print(f"⚠️  Hydra API {resp.status_code} for case {case_number}")
    except Exception:
        pass
    cache[case_number] = ""
    return ""


# Curated list of fields - avoids 400 errors from requesting too many fields at once.
# Red Hat Jira has many custom fields; requesting all can exceed API limits.
DEFAULT_FIELD_IDS = [
    "key", "summary", "description", "status", "assignee", "reporter",
    "issuetype", "priority", "labels", "components", "fixVersions",
    "created", "updated", "resolution", "resolutiondate",
    "issuelinks", "subtasks", "parent", "project",
    "customfield_12316752",   # Product Manager
    "customfield_12319940",   # Target Version
    "customfield_12311940",   # Rank
    "customfield_12313240",   # Team
    "customfield_12313440",   # CIPOE (customer link)
]

RICE_FIELD_CANDIDATES: Dict[str, List[str]] = {
    "Reach": ["Reach"],
    "Impact": ["Impact (migrated)", "Impact"],
    "Confidence": ["Confidence"],
    "Effort": ["Effort"],
    "RICE Score": ["RICE Score", "Rice Score", "RICE score"],
}

# Column order for CSV (subset of keys); remaining keys keep discovery order after these.
RICE_EXPORT_COLUMN_ORDER = ["Reach", "Impact", "Confidence", "Effort", "RICE Score"]


def discover_rice_fields(
    jira_url: str, session: requests.Session, api_version: str,
) -> Dict[str, str]:
    """Auto-discover RICE custom field IDs from Jira.

    Returns mapping like ``{"Reach": "customfield_123", ...}``.
    Missing fields map to ``""``.
    """
    try:
        resp = session.get(f"{jira_url}/rest/api/{api_version}/field", timeout=15)
        resp.raise_for_status()
        all_fields = resp.json()
    except Exception as e:
        print(f"  Could not fetch Jira fields for RICE discovery: {e}")
        return {k: "" for k in RICE_FIELD_CANDIDATES}

    name_to_id: Dict[str, str] = {}
    for f in all_fields:
        if f.get("custom"):
            name_to_id[f.get("name", "")] = f.get("id", "")

    result: Dict[str, str] = {}
    for col_name, candidates in RICE_FIELD_CANDIDATES.items():
        fid = ""
        for cand in candidates:
            if cand in name_to_id:
                fid = name_to_id[cand]
                break
        result[col_name] = fid

    env_score = (os.getenv("JIRA_RICE_SCORE_FIELD") or "").strip()
    if env_score:
        result["RICE Score"] = env_score

    found = {k: v for k, v in result.items() if v}
    if found:
        print(f"  RICE fields discovered: {', '.join(f'{k}={v}' for k, v in found.items())}")
    return result



def flatten_value(val: Any) -> str:
    """Convert a Jira field value to a CSV-safe string."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        # Replace newlines and problematic chars for CSV
        return val.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    if isinstance(val, dict):
        # Common patterns: {name, key, displayName, value}
        for k in ("name", "displayName", "key", "value"):
            if k in val and val[k] is not None:
                return str(val[k]).replace("\n", " ")
        return json.dumps(val)[:500]  # Truncate complex objects
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


def extract_sfdc_case_ids(
    issue_key: str,
    fields: Dict,
    jira_url: str,
    session: requests.Session,
    api_version: str,
    cache: Dict[str, List[str]],
) -> str:
    """Extract SFDC case IDs from custom fields and remote links."""
    if issue_key in cache:
        return " | ".join(cache[issue_key])

    case_ids = []

    # 1. Scan all fields for values that look like SFDC case IDs (15 or 18 alphanumeric)
    for fval in fields.values():
        val_str = flatten_value(fval) if fval is not None else ""
        if not val_str or len(val_str) < 10:
            continue
        # SFDC case IDs: 15 or 18 chars, alphanumeric, often start with 500
        if re.match(r"^500[a-zA-Z0-9]{12,15}$", val_str.strip()):
            case_ids.append(val_str.strip())

    # 2. Fetch remote links (Salesforce links)
    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{issue_key}/remotelink",
            timeout=15,
        )
        if resp.status_code == 200:
            links = resp.json()
            for link in links:
                obj = link.get("object", {}) or {}
                url = obj.get("url", "") or ""
                title = obj.get("title", "") or ""
                if "salesforce" in url.lower() or "salesforce" in str(link).lower():
                    # Extract case ID from URL (e.g. .../500xx...) or title
                    for text in [url, title]:
                        match = re.search(r"500[a-zA-Z0-9]{12,15}", text)
                        if match:
                            case_ids.append(match.group())
                        # Also match generic 15-18 char SFDC ID
                        match = re.search(r"\b([a-zA-Z0-9]{15,18})\b", text)
                        if match and match.group() not in case_ids:
                            case_ids.append(match.group())
    except Exception:
        pass

    # Deduplicate preserving order
    seen = set()
    unique = []
    for cid in case_ids:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)

    cache[issue_key] = unique
    return " | ".join(unique) if unique else ""


def extract_cipoe_keys_from_links(issuelinks: List[Dict]) -> List[str]:
    """Extract CIPOE issue keys from issue links."""
    keys = []
    for link in issuelinks or []:
        for direction in ("inwardIssue", "outwardIssue"):
            issue = link.get(direction)
            if issue and isinstance(issue, dict):
                key = issue.get("key", "")
                if key and key.upper().startswith("CIPOE"):
                    keys.append(key)
    return list(dict.fromkeys(keys))  # Unique, preserve order


def extract_rfe_keys_from_links(issuelinks: List[Dict]) -> List[str]:
    """Extract RFE issue keys from issue links."""
    keys = []
    for link in issuelinks or []:
        for direction in ("inwardIssue", "outwardIssue"):
            issue = link.get(direction)
            if issue and isinstance(issue, dict):
                key = issue.get("key", "")
                if key and key.upper().startswith("RFE"):
                    keys.append(key)
    return list(dict.fromkeys(keys))  # Unique, preserve order


def fetch_issue_links(
    issue_key: str,
    jira_url: str,
    session: requests.Session,
    api_version: str,
    cache: Dict[str, List[Dict]],
) -> List[Dict]:
    """Fetch issuelinks for an issue (with caching)."""
    if issue_key in cache:
        return cache[issue_key]
    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{issue_key}",
            params={"fields": "issuelinks"},
            timeout=15,
        )
        if resp.status_code == 200:
            links = resp.json().get("fields", {}).get("issuelinks", [])
            cache[issue_key] = links
            return links
    except Exception:
        pass
    cache[issue_key] = []
    return []


def fetch_rfe_sfdc_data(
    rfe_key: str,
    jira_url: str,
    session: requests.Session,
    api_version: str,
    cache: Dict[str, List[Dict[str, str]]],
) -> List[Dict[str, str]]:
    """Fetch SFDC case IDs and account names from an RFE issue.

    Checks remote links for Salesforce URLs and extracts case IDs.
    Then fetches the RFE issue fields to look for SFDC account data
    in custom fields (e.g. SFDC Cases Links, summary patterns).

    Returns list of dicts: [{"case_id": "...", "account_name": "..."}, ...]
    """
    if rfe_key in cache:
        return cache[rfe_key]

    results = []
    seen_case_ids: set = set()

    # --- 1. Remote links: look for Salesforce case URLs ---
    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{rfe_key}/remotelink",
            timeout=15,
        )
        if resp.status_code == 200:
            for link in resp.json():
                obj = link.get("object", {}) or {}
                url = obj.get("url", "") or ""
                title = obj.get("title", "") or ""
                summary = obj.get("summary", "") or ""

                combined = f"{url} {title} {summary}"
                if not re.search(r"salesforce|force\.com|sfdc", combined, re.IGNORECASE):
                    continue

                case_id = ""
                for text in [url, title, summary]:
                    m = re.search(r"500[a-zA-Z0-9]{12,15}", text)
                    if m:
                        case_id = m.group()
                        break
                if not case_id:
                    for text in [url, title, summary]:
                        m = re.search(r"\b(\d{7,10})\b", text)
                        if m:
                            case_id = m.group()
                            break

                if not case_id or case_id in seen_case_ids:
                    continue
                seen_case_ids.add(case_id)

                account = ""
                for text in [title, summary]:
                    am = re.search(
                        r"(?:account|customer)\s*[:\-]\s*(.+?)(?:\s*[|;]|$)",
                        text, re.IGNORECASE,
                    )
                    if am:
                        account = am.group(1).strip()
                        break

                results.append({"case_id": case_id, "account_name": account})
    except Exception:
        pass

    # --- 2. Fetch RFE issue fields for SFDC case number (customfield_12313441) ---
    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{rfe_key}",
            params={"fields": "customfield_12313441"},
            timeout=15,
        )
        if resp.status_code == 200:
            fields = resp.json().get("fields", {})
            sfdc_field = fields.get("customfield_12313441")
            if sfdc_field:
                for case_num in re.split(r"[,\s;|]+", str(sfdc_field).strip()):
                    case_num = case_num.strip()
                    if case_num and case_num not in seen_case_ids:
                        seen_case_ids.add(case_num)
                        results.append({"case_id": case_num, "account_name": ""})
    except Exception:
        pass

    cache[rfe_key] = results
    return results


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


def load_last_run(state_file: Path) -> Optional[str]:
    """Load last run timestamp from state file."""
    if not state_file.exists():
        return None
    try:
        return state_file.read_text().strip()
    except Exception:
        return None


def save_last_run(state_file: Path, timestamp: str) -> None:
    """Save current timestamp to state file."""
    try:
        state_file.write_text(timestamp)
    except Exception as e:
        print(f"⚠️  Could not save state file: {e}")


def run_export(
    jira_url: str,
    api_token: str,
    state_file: Path,
    force_all: bool,
    all_features: bool,
    output_path: Optional[Path],
    rh_access_token: Optional[str] = None,
    jira_email: Optional[str] = None,
) -> Path:
    """Export ROX features to CSV. Returns path to created CSV."""
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
            print(f"✅ Connected to Jira (API v{api_version})")
        else:
            print(f"✅ Connected to Jira (API v{api_version}, serverInfo returned {r.status_code})")
    except Exception:
        print(f"✅ Connected to Jira (API v{api_version})")

    rice_fields = discover_rice_fields(jira_url, session, api_version)
    rice_id_to_col = {v: k for k, v in rice_fields.items() if v}

    # Build JQL
    if all_features:
        jql = "project = ROX AND type = feature ORDER BY updated DESC"
        print("🔍 Exporting all ROX features (--all-features)")
    else:
        status_filter = 'status in (Backlog, "New", Refinement, "To Do")'
        base_type_filter = "project = ROX AND type = feature AND " + status_filter

        if force_all:
            jql = f"{base_type_filter} ORDER BY updated DESC"
            print("🔍 Exporting open ROX features (--force-all)")
        else:
            last_run = load_last_run(state_file)
            if last_run:
                try:
                    dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                    jql = f'{base_type_filter} AND updated >= "{date_str}" ORDER BY updated DESC'
                    print(f"🔍 Exporting open ROX features updated since {last_run}")
                except Exception:
                    jql = f"{base_type_filter} ORDER BY updated DESC"
                    print("🔍 Exporting open ROX features (invalid state file)")
            else:
                since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                jql = f'{base_type_filter} AND updated >= "{since}" ORDER BY updated DESC'
                print(f"🔍 First run: exporting open ROX features updated since {since}")

    rice_extra = [fid for fid in rice_fields.values() if fid]
    fields_param = ",".join(DEFAULT_FIELD_IDS + rice_extra)

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
                print(f"❌ Jira search failed: {resp.status_code}")
                print(resp.text[:500])
                sys.exit(1)
            data = resp.json()
            all_issues.extend(data.get("issues", []))
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
                print(f"❌ Jira search failed: {resp.status_code}")
                print(resp.text[:500])
                sys.exit(1)
            data = resp.json()
            issues = data.get("issues", [])
            all_issues.extend(issues)
            if len(issues) < max_results:
                break
            start_at += max_results

    print(f"📊 Retrieved {len(all_issues)} features")

    if not all_issues:
        print("⚠️  No features to export")
        if output_path is None:
            output_path = Path(__file__).parent / "rox_features_export_empty.csv"
        output_path.write_text("key,_sfdc_case_id,_rfe_keys,_rfe_sfdc_accounts,_customer_names_cipoe\n", encoding="utf-8")
        return output_path

    # Caches
    cipoe_cache: Dict[str, str] = {}
    sfdc_cache: Dict[str, List[str]] = {}
    rfe_links_cache: Dict[str, List[Dict]] = {}
    rfe_sfdc_cache: Dict[str, List[Dict[str, str]]] = {}
    case_account_cache: Dict[str, str] = {}

    if rh_access_token:
        print("🔑 Red Hat API token available — will look up SFDC account names")

    # Build CSV rows
    rows = []
    for issue in all_issues:
        fields = issue.get("fields", {})
        issue_key = issue.get("key", "")
        issuelinks = fields.get("issuelinks", [])

        row = {"key": issue_key}  # Ensure JIRA key is first
        for fid, fval in fields.items():
            col = rice_id_to_col.get(fid, fid)
            row[col] = flatten_value(fval)

        # SFDC case ID from custom fields and remote links
        row["_sfdc_case_id"] = extract_sfdc_case_ids(
            issue_key, fields, jira_url, session, api_version, sfdc_cache
        )

        # CIPOE: direct (ROX->CIPOE) + via RFE (ROX->RFE->CIPOE)
        cipoe_keys = list(extract_cipoe_keys_from_links(issuelinks))
        rfe_keys = extract_rfe_keys_from_links(issuelinks)
        for rfe_key in rfe_keys:
            rfe_links = fetch_issue_links(
                rfe_key, jira_url, session, api_version, rfe_links_cache
            )
            cipoe_from_rfe = extract_cipoe_keys_from_links(rfe_links)
            for ck in cipoe_from_rfe:
                if ck not in cipoe_keys:
                    cipoe_keys.append(ck)

        customer_names = []
        for ck in cipoe_keys:
            name = fetch_cipoe_summary(ck, jira_url, session, api_version, cipoe_cache)
            if name:
                customer_names.append(f"{ck}: {name}")
        row["_rfe_keys"] = " | ".join(rfe_keys) if rfe_keys else ""
        row["_customer_names_cipoe"] = " | ".join(customer_names) if customer_names else ""

        # SFDC account names from RFE remote links (ROX -> RFE -> SF case -> Account)
        rfe_sfdc_entries: List[Dict[str, str]] = []
        for rfe_key in rfe_keys:
            entries = fetch_rfe_sfdc_data(
                rfe_key, jira_url, session, api_version, rfe_sfdc_cache
            )
            rfe_sfdc_entries.extend(entries)

        # Enrich with account names from Red Hat Hydra API
        if rh_access_token and rfe_sfdc_entries:
            for entry in rfe_sfdc_entries:
                if entry["account_name"]:
                    continue
                acct = fetch_case_account_name(
                    entry["case_id"], rh_access_token, case_account_cache
                )
                if acct:
                    entry["account_name"] = acct

        if rfe_sfdc_entries:
            parts = []
            for e in rfe_sfdc_entries:
                if e["account_name"]:
                    parts.append(f"{e['case_id']}: {e['account_name']}")
                else:
                    parts.append(e["case_id"])
            row["_rfe_sfdc_accounts"] = " | ".join(parts)
        else:
            row["_rfe_sfdc_accounts"] = ""

        rows.append(row)

    # Determine output path
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(__file__).parent / f"rox_features_export_{timestamp}.csv"

    # Write CSV
    if rows:
        all_keys = list(rows[0].keys())
        priority_cols = [
            "key", "_sfdc_case_id", "_rfe_keys",
            "_rfe_sfdc_accounts", "_customer_names_cipoe",
        ]
        for col in priority_cols:
            if col in all_keys:
                all_keys.remove(col)
        rice_present = [c for c in RICE_EXPORT_COLUMN_ORDER if c in all_keys]
        for col in rice_present:
            all_keys.remove(col)
        all_keys = (
            [c for c in priority_cols if c in rows[0]]
            + rice_present
            + all_keys
        )

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    print(f"✅ CSV saved: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Export ROX features to CSV and upload to NotebookLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Generate CSV only, skip NotebookLM upload",
    )
    parser.add_argument(
        "--notebook-name",
        default=DEFAULT_NOTEBOOK_NAME,
        help=f"NotebookLM notebook name (default: {DEFAULT_NOTEBOOK_NAME})",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Export open features (ignore last-run state)",
    )
    parser.add_argument(
        "--all-features",
        action="store_true",
        help="Export ALL features from Jira (all statuses, no date filter)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output CSV path (default: rox_features_export_YYYYMMDD_HHMMSS.csv)",
    )
    args = parser.parse_args()

    token = jira_api_token_from_env()
    if not token:
        print("❌ JIRA_TOKEN or JIRA_API_TOKEN environment variable not set")
        sys.exit(1)

    jira_url = os.getenv("JIRA_BASE_URL", DEFAULT_JIRA_URL)
    jira_email = os.getenv("JIRA_EMAIL", "")

    # Red Hat Customer Portal API for SFDC account lookups
    rh_access_token = None
    rh_offline_token = os.getenv("RH_OFFLINE_TOKEN", "").strip()
    if rh_offline_token:
        rh_access_token = get_rh_access_token(rh_offline_token)
        if not rh_access_token:
            print("⚠️  Could not get Red Hat API access token — account names will be unavailable")
    else:
        print("ℹ️  RH_OFFLINE_TOKEN not set — SFDC account names will not be resolved")
        print("   Get one from: https://access.redhat.com/management/api")

    csv_path = run_export(
        jira_url=jira_url,
        api_token=token,
        state_file=DEFAULT_STATE_FILE,
        force_all=args.force_all,
        all_features=args.all_features,
        output_path=args.output,
        rh_access_token=rh_access_token,
        jira_email=jira_email,
    )

    # Save last run timestamp (unless force-all or all-features)
    if not args.force_all and not args.all_features:
        save_last_run(DEFAULT_STATE_FILE, datetime.now().isoformat())

    # Upload to NotebookLM
    if not args.skip_upload:
        if not notebooklm_upload_available():
            print(
                "❌ NotebookLM upload unavailable: install notebooklm-mcp-cli and run `nlm login`, "
                "or pip install 'notebooklm-py[browser]' and run `notebooklm login`"
            )
            sys.exit(1)
        if not upload_csvs_to_notebook([csv_path], args.notebook_name):
            sys.exit(1)
    else:
        print("📤 Skipping NotebookLM upload (--skip-upload)")


if __name__ == "__main__":
    main()

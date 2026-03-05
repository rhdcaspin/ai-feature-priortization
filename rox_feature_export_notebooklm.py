#!/usr/bin/env python3
"""
ROX Feature Export to CSV and NotebookLM

Exports ROX project features updated since the last script run to CSV,
including key Jira fields and customer names from CIPOE (direct links and
via related RFE feature requests).
Uploads the CSV to Google NotebookLM.

Usage:
    export JIRA_TOKEN=your_token
    python3 rox_feature_export_notebooklm.py

    # First run: exports features updated in last 30 days
    # Subsequent runs: exports only features updated since last run

Options:
    --skip-upload     Generate CSV only, skip NotebookLM upload
    --notebook-name   Name of NotebookLM notebook (default: ROX Features Export)
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

# Optional NotebookLM support
try:
    import asyncio
    from notebooklm import NotebookLMClient
    NOTEBOOKLM_AVAILABLE = True
except ImportError:
    NOTEBOOKLM_AVAILABLE = False

# State file to track last run timestamp
DEFAULT_STATE_FILE = Path(__file__).parent / ".rox_export_last_run"
DEFAULT_JIRA_URL = "https://issues.redhat.com"


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
) -> Path:
    """Export ROX features to CSV. Returns path to created CSV."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })

    # Determine API version
    api_version = "2"
    for v in ("2", "3"):
        try:
            r = session.get(f"{jira_url}/rest/api/{v}/serverInfo", timeout=10)
            if r.status_code == 200:
                api_version = v
                break
        except Exception:
            continue

    print(f"✅ Connected to Jira (API v{api_version})")

    # Build JQL
    if all_features:
        # Export ALL features (all statuses, no date filter)
        jql = "project = ROX AND type = feature ORDER BY updated DESC"
        print("🔍 Exporting all ROX features (--all-features)")
    else:
        # Default: only open features (Backlog, New, Refinement, To Do)
        status_filter = 'status in (Backlog, "New", Refinement, "To Do")'
        base_type_filter = "project = ROX AND type = feature AND " + status_filter

        if force_all:
            jql = f"{base_type_filter} ORDER BY updated DESC"
            print("🔍 Exporting open ROX features (--force-all)")
        else:
            last_run = load_last_run(state_file)
            if last_run:
                # Jira expects format: "yyyy-MM-dd" or "yyyy/MM/dd"
                try:
                    dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                    jql = f'{base_type_filter} AND updated >= "{date_str}" ORDER BY updated DESC'
                    print(f"🔍 Exporting open ROX features updated since {last_run}")
                except Exception:
                    jql = f"{base_type_filter} ORDER BY updated DESC"
                    print("🔍 Exporting open ROX features (invalid state file)")
            else:
                # First run: last 30 days
                since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                jql = f'{base_type_filter} AND updated >= "{since}" ORDER BY updated DESC'
                print(f"🔍 First run: exporting open ROX features updated since {since}")

    # Use curated field list (requesting all fields can cause 400 Bad Request)
    fields_param = ",".join(DEFAULT_FIELD_IDS)

    # Fetch issues
    all_issues = []
    start_at = 0
    max_results = 50

    while True:
        params = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
            "fields": fields_param,
        }
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/search",
            params=params,
            timeout=60,
        )
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
        output_path.write_text("key,_sfdc_case_id,_rfe_keys,_customer_names_cipoe\n", encoding="utf-8")
        return output_path

    # Caches
    cipoe_cache: Dict[str, str] = {}
    sfdc_cache: Dict[str, List[str]] = {}
    rfe_links_cache: Dict[str, List[Dict]] = {}

    # Build CSV rows
    rows = []
    for issue in all_issues:
        fields = issue.get("fields", {})
        issue_key = issue.get("key", "")
        issuelinks = fields.get("issuelinks", [])

        row = {"key": issue_key}  # Ensure JIRA key is first
        for fid, fval in fields.items():
            row[fid] = flatten_value(fval)

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
        rows.append(row)

    # Determine output path
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(__file__).parent / f"rox_features_export_{timestamp}.csv"

    # Write CSV
    if rows:
        all_keys = list(rows[0].keys())
        # Order: key, _sfdc_case_id, _rfe_keys, _customer_names_cipoe first, then rest
        for col in ["key", "_sfdc_case_id", "_rfe_keys", "_customer_names_cipoe"]:
            if col in all_keys:
                all_keys.remove(col)
        all_keys = ["key", "_sfdc_case_id", "_rfe_keys", "_customer_names_cipoe"] + [
            k for k in all_keys
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    print(f"✅ CSV saved: {output_path}")
    return output_path


async def upload_to_notebooklm(csv_path: Path, notebook_name: str) -> bool:
    """Upload CSV file to NotebookLM. Returns True on success."""
    if not NOTEBOOKLM_AVAILABLE:
        print("❌ notebooklm-py not installed. Run: pip install 'notebooklm-py[browser]'")
        return False

    try:
        async with await NotebookLMClient.from_storage() as client:
            # Create or find notebook
            notebooks = await client.notebooks.list()
            nb = None
            for n in notebooks:
                if n.title == notebook_name:
                    nb = n
                    break
            if nb is None:
                nb = await client.notebooks.create(notebook_name)
                print(f"📓 Created NotebookLM notebook: {notebook_name}")
            else:
                print(f"📓 Using existing NotebookLM notebook: {notebook_name}")

            await client.sources.add_file(nb.id, str(csv_path), wait=True)
            print(f"✅ Uploaded {csv_path.name} to NotebookLM")
            return True
    except Exception as e:
        print(f"❌ NotebookLM upload failed: {e}")
        print("   Ensure you have run 'notebooklm login' first")
        return False


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
        default="ROX Features Export",
        help="NotebookLM notebook name (default: ROX Features Export)",
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

    token = os.getenv("JIRA_TOKEN")
    if not token:
        print("❌ JIRA_TOKEN environment variable not set")
        print("   Get a token from: https://issues.redhat.com/secure/ViewProfile.jspa?selectedTab=com.atlassian.pats.pats-plugin:jira-user-personal-access-tokens")
        sys.exit(1)

    csv_path = run_export(
        jira_url=DEFAULT_JIRA_URL,
        api_token=token,
        state_file=DEFAULT_STATE_FILE,
        force_all=args.force_all,
        all_features=args.all_features,
        output_path=args.output,
    )

    # Save last run timestamp (unless force-all or all-features)
    if not args.force_all and not args.all_features:
        save_last_run(DEFAULT_STATE_FILE, datetime.now().isoformat())

    # Upload to NotebookLM
    if not args.skip_upload:
        if NOTEBOOKLM_AVAILABLE:
            success = asyncio.run(upload_to_notebooklm(csv_path, args.notebook_name))
            if not success:
                sys.exit(1)
        else:
            print("❌ Install notebooklm-py for upload: pip install 'notebooklm-py[browser]'")
            sys.exit(1)
    else:
        print("📤 Skipping NotebookLM upload (--skip-upload)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
RHACS Support Cases Export

Exports all support cases from the Red Hat Customer Portal assigned to
RHACS products ("Red Hat Advanced Cluster Security for Kubernetes" and
"Red Hat Advanced Cluster Security Cloud Service") via the Hydra search API.

Authentication uses RH_OFFLINE_TOKEN from .env.

Usage:
    python3 rhacs_cases_export.py                          # all RHACS cases
    python3 rhacs_cases_export.py --status open             # only open cases
    python3 rhacs_cases_export.py --status closed           # only closed cases
    python3 rhacs_cases_export.py --since 2025-01-01        # cases created since date
    python3 rhacs_cases_export.py --skip-upload             # CSV only
    python3 rhacs_cases_export.py -o my_report.csv          # custom output path
"""

import os
import sys
import csv
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from notebooklm_upload import notebooklm_upload_available, upload_csvs_to_notebook  # noqa: E402

DEFAULT_NOTEBOOK_NAME = "The Big Notebook for RHACS Product Management"

RH_SSO_TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
RH_HYDRA_SEARCH_URL = "https://access.redhat.com/hydra/rest/search/cases"

RHACS_PRODUCTS = [
    "Red Hat Advanced Cluster Security for Kubernetes",
    "Red Hat Advanced Cluster Security Cloud Service",
]

# Hydra Solr field -> CSV column name
FIELD_MAP = {
    "case_number": "case_number",
    "case_product": "product",
    "case_version": "version",
    "case_summary": "summary",
    "case_description": "description",
    "case_status": "status",
    "case_severity": "severity",
    "case_type": "case_type",
    "case_account_name": "account_name",
    "case_contactName": "contact_name",
    "case_contact_sso_username": "contact_sso",
    "case_owner": "case_owner",
    "case_createdDate": "created_date",
    "case_lastModifiedDate": "last_modified_date",
    "case_closedDate": "closed_date",
    "case_sbr": "sbr_group",
    "case_tags": "tags",
    "case_origin": "origin",
    "case_urgency": "urgency",
    "case_customer_escalation": "escalated",
    "case_issue": "issue",
    "case_hotfix_requested": "hotfix_requested",
    "case_hotfix_delivered": "hotfix_delivered",
    "uri": "portal_url",
    "case_accountNumber": "account_number",
    "case_super_region": "region",
}

CSV_COLUMNS = list(FIELD_MAP.values())

PAGE_SIZE = 100


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
        print(f"  SSO token exchange failed: {resp.status_code}")
    except Exception as e:
        print(f"  SSO error: {e}")
    return None


def safe_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return val.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    if isinstance(val, list):
        return " | ".join(safe_str(v) for v in val)
    if isinstance(val, dict):
        return str(val)[:500]
    return str(val)


def build_solr_query(
    status_filter: Optional[str] = None,
    since_date: Optional[str] = None,
) -> str:
    """Build a Solr query string for RHACS cases."""
    product_clauses = " OR ".join(
        f'case_product:"{p}"' for p in RHACS_PRODUCTS
    )
    parts = [f"({product_clauses})"]

    if status_filter == "open":
        parts.append("NOT case_status:Closed")
    elif status_filter == "closed":
        parts.append("case_status:Closed")

    if since_date:
        parts.append(f"case_createdDate:[{since_date}T00:00:00Z TO *]")

    return " AND ".join(parts)


def extract_row(doc: Dict) -> Dict[str, str]:
    """Convert a Hydra Solr document to a flat CSV row."""
    row = {}
    for solr_field, csv_col in FIELD_MAP.items():
        row[csv_col] = safe_str(doc.get(solr_field))
    return row


def fetch_all_rhacs_cases(
    access_token: str,
    status_filter: Optional[str] = None,
    since_date: Optional[str] = None,
) -> List[Dict]:
    """Fetch all RHACS cases via the Hydra Solr search API."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    query = build_solr_query(status_filter, since_date)
    print(f"Solr query: {query}")

    all_docs: List[Dict] = []
    start = 0
    total = None

    while True:
        try:
            resp = requests.get(
                RH_HYDRA_SEARCH_URL,
                headers=headers,
                params={
                    "q": query,
                    "rows": PAGE_SIZE,
                    "start": start,
                },
                timeout=60,
            )
        except Exception as e:
            print(f"  API request failed: {e}")
            break

        if resp.status_code == 401:
            print("  Authentication failed — token may have expired")
            break
        if resp.status_code != 200:
            print(f"  API returned {resp.status_code}: {resp.text[:300]}")
            break

        data = resp.json()
        response = data.get("response", {})

        if total is None:
            total = response.get("numFound", 0)
            print(f"Total RHACS cases: {total}")

        docs = response.get("docs", [])
        if not docs:
            break

        all_docs.extend(docs)
        print(f"  Fetched {len(all_docs)}/{total} cases...", end="\r")

        if len(all_docs) >= total or len(docs) < PAGE_SIZE:
            break

        start += PAGE_SIZE

    print(f"  Fetched {len(all_docs)} cases total          ")
    return all_docs


CHUNK_SIZE = 1000


def write_csv_chunk(rows: List[Dict], path: Path) -> None:
    """Write a list of rows to a CSV file."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_export(
    access_token: str,
    output_path: Optional[Path],
    status_filter: Optional[str] = None,
    since_date: Optional[str] = None,
) -> List[Path]:
    """Export RHACS cases to CSV, split into chunks of CHUNK_SIZE rows.

    Returns list of generated CSV file paths.
    """
    docs = fetch_all_rhacs_cases(access_token, status_filter, since_date)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = Path(__file__).parent

    if not docs:
        print("No RHACS cases found")
        empty_path = output_path or base_dir / f"rhacs_cases_{timestamp}.csv"
        empty_path.write_text(",".join(CSV_COLUMNS) + "\n", encoding="utf-8")
        print(f"CSV saved (empty): {empty_path}")
        return [empty_path]

    rows = [extract_row(d) for d in docs]

    # Split into chunks
    chunks = [rows[i:i + CHUNK_SIZE] for i in range(0, len(rows), CHUNK_SIZE)]
    csv_paths: List[Path] = []

    if len(chunks) == 1 and output_path:
        write_csv_chunk(chunks[0], output_path)
        csv_paths.append(output_path)
        print(f"CSV saved: {output_path} ({len(chunks[0])} cases)")
    else:
        for idx, chunk in enumerate(chunks, start=1):
            if output_path:
                stem = output_path.stem
                suffix = output_path.suffix or ".csv"
                chunk_path = output_path.parent / f"{stem}_part{idx}{suffix}"
            else:
                chunk_path = base_dir / f"rhacs_cases_{timestamp}_part{idx}.csv"
            write_csv_chunk(chunk, chunk_path)
            csv_paths.append(chunk_path)
            print(f"CSV saved: {chunk_path} ({len(chunk)} cases)")

    print(f"\nTotal: {len(rows)} cases across {len(csv_paths)} file(s)")

    # Summary
    products: Dict[str, int] = {}
    statuses: Dict[str, int] = {}
    severities: Dict[str, int] = {}
    for r in rows:
        products[r.get("product", "?")] = products.get(r.get("product", "?"), 0) + 1
        statuses[r.get("status", "?")] = statuses.get(r.get("status", "?"), 0) + 1
        severities[r.get("severity", "?")] = severities.get(r.get("severity", "?"), 0) + 1

    print(f"\n  {'Product':<55} Count")
    print(f"  {'-'*55} -----")
    for p, c in sorted(products.items()):
        print(f"  {p:<55} {c}")
    print(f"\n  {'Status':<35} Count")
    print(f"  {'-'*35} -----")
    for s, c in sorted(statuses.items()):
        print(f"  {s:<35} {c}")
    print(f"\n  {'Severity':<35} Count")
    print(f"  {'-'*35} -----")
    for s, c in sorted(severities.items()):
        print(f"  {s:<35} {c}")

    return csv_paths


def main():
    parser = argparse.ArgumentParser(
        description="Export RHACS support cases from the Red Hat Customer Portal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--status",
        choices=["open", "closed", "all"],
        default="all",
        help="Filter by case status (default: all)",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only cases created since this date (YYYY-MM-DD)",
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
        help="Output CSV base path (default: rhacs_cases_YYYYMMDD_HHMMSS.csv)",
    )
    args = parser.parse_args()

    rh_offline_token = os.getenv("RH_OFFLINE_TOKEN", "").strip()
    if not rh_offline_token:
        print("RH_OFFLINE_TOKEN environment variable not set")
        print("Get your offline token from: https://access.redhat.com/management/api")
        sys.exit(1)

    access_token = get_rh_access_token(rh_offline_token)
    if not access_token:
        print("Could not obtain access token")
        sys.exit(1)
    print("Authenticated with Red Hat SSO")

    status_filter = None if args.status == "all" else args.status

    csv_paths = run_export(
        access_token=access_token,
        output_path=args.output,
        status_filter=status_filter,
        since_date=args.since,
    )

    if not args.skip_upload:
        if not notebooklm_upload_available():
            print(
                "NotebookLM upload unavailable: install notebooklm-mcp-cli and run `nlm login`, "
                "or pip install 'notebooklm-py[browser]' and run `notebooklm login`"
            )
            sys.exit(1)
        if not upload_csvs_to_notebook(csv_paths, args.notebook_name):
            sys.exit(1)
    else:
        print("Skipping NotebookLM upload (--skip-upload)")


if __name__ == "__main__":
    main()

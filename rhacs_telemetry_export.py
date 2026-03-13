#!/usr/bin/env python3
"""
RHACS Telemetry Export to CSV and NotebookLM

Exports Red Hat Advanced Cluster Security (RHACS) customer telemetry data
from the Dataverse (Snowflake) to CSV and uploads to Google NotebookLM.

Data sources:
  - OCP_USAGE_INDICATORS: Account-level health, risk/opportunity, version currency
  - OCP_OPR_LASTACTIVE: RHACS operator versions, clusters, cores, first/last active
  - OPENSHIFT_EBS_TYPE: Internal vs external account classification

Usage:
    python3 rhacs_telemetry_export.py
    python3 rhacs_telemetry_export.py --skip-upload
    python3 rhacs_telemetry_export.py --from-json indicators.json operator.json

Options:
    --skip-upload     Generate CSV only, skip NotebookLM upload
    --notebook-name   NotebookLM notebook name
    --from-json       Parse pre-fetched JSON files (indicators_file operator_file)
    --output / -o     Output CSV path
"""

import os
import sys
import csv
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

try:
    import asyncio
    from notebooklm import NotebookLMClient
    NOTEBOOKLM_AVAILABLE = True
except ImportError:
    NOTEBOOKLM_AVAILABLE = False

try:
    import snowflake.connector
    SNOWFLAKE_AVAILABLE = True
except ImportError:
    SNOWFLAKE_AVAILABLE = False

DEFAULT_NOTEBOOK_NAME = "ACS RICE Scoring and Prioritization Framework"

INDICATORS_SQL = """
SELECT
    ui.EBS_ACCOUNT,
    ui.AS_ON_DATE,
    ui.RECENT_1DAY_AVG_CORES,
    ui.LAST_7DAYS_AVG_CORES,
    ui.LAST_30DAYS_AVG_CORES,
    ui.LAST_90DAYS_AVG_CORES,
    ui.LAST_365DAYS_AVG_CORES,
    ui.RECENT_1DAY_CLUSTERS,
    ui.ACCNT_RISK_OPP_FLAG,
    ui.ACCNT_RISK_OPP_LEVEL,
    ui.ACCNT_RISK_OPP_DESC,
    ui.VERSION_CURRENCY_FLAG,
    ui.VERSION_CURRENCY_LEVEL,
    ui.VERSION_CURRENCY_DESC,
    ui.AWS_CLUSTERS,
    ui.AZURE_CLUSTERS,
    ui.GCP_CLUSTERS,
    ui.CS_AWS_CORES,
    ui.CS_AZURE_CORES,
    ui.CS_GCP_CORES,
    ui.HAS_AMAZON_EKS_RISK,
    ui.HAS_MICROSOFT_AKS_RISK,
    ui.HAS_GOOGLE_GKE_RISK,
    ui.CS_TAKEOVER_DESC,
    ui.UNATTACHED_CLUSTERS,
    ui.ATTACHED_CLUSTERS,
    ui.UNATTACHED_CLUSTER_FLAG,
    ui.COST_OPTIMIZATION_CLUSTERS,
    ui.COST_OPTIMIZATION_FLAG,
    ui.CONNECTED_STATUS,
    ui.IS_PP_OPR,
    ui.IS_ODF_OPR,
    ui.IS_QUAY_OPR,
    ui.IS_ACM_OPR,
    ui.IS_ACS_OPR
FROM TELESENSE_DB.OPENSHIFT_MARTS.OCP_USAGE_INDICATORS ui
LEFT JOIN TELESENSE_DB.OPENSHIFT_MARTS.OPENSHIFT_EBS_TYPE ebs
    ON ui.EBS_ACCOUNT = ebs."ebs_account"
WHERE ui.IS_ACS_OPR = 1
  AND (ebs."status" IS NULL OR ebs."status" != 'Internal')
ORDER BY ui.RECENT_1DAY_AVG_CORES DESC NULLS LAST
"""

OPERATOR_SQL = """
SELECT
    la.EBS_ACCOUNT_NUMBER,
    la.OPERATOR_NAME,
    la.VERSION AS RHACS_VERSION,
    la.CLUSTER_INSTALLED_ON AS RHACS_CLUSTERS,
    la.CORES_INSTALLED_ON AS RHACS_CORES,
    la.COUNT_OF_FAILURES AS RHACS_FAILURES,
    la.OPR_FIRST_ACTIVE AS RHACS_FIRST_ACTIVE,
    la.OPR_LAST_ACTIVE AS RHACS_LAST_ACTIVE,
    la.OPR_VER_FIRST_ACTIVE,
    la.OPR_VER_LAST_ACTIVE,
    la.DESC_OF_FAILURES AS RHACS_FAILURE_DESC
FROM TELESENSE_DB.OPENSHIFT_MARTS.OCP_OPR_LASTACTIVE la
LEFT JOIN TELESENSE_DB.OPENSHIFT_MARTS.OPENSHIFT_EBS_TYPE ebs
    ON la.EBS_ACCOUNT_NUMBER = ebs."ebs_account"
WHERE la.OPERATOR_NAME = 'rhacs-operator'
  AND (ebs."status" IS NULL OR ebs."status" != 'Internal')
ORDER BY la.EBS_ACCOUNT_NUMBER, la.OPR_VER_LAST_ACTIVE DESC
"""


def query_snowflake(sql: str, conn_params: dict) -> tuple[list[str], list[dict]]:
    """Execute SQL against Snowflake and return (columns, rows_as_dicts)."""
    conn = snowflake.connector.connect(**conn_params)
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(sql)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return columns, [dict(r) for r in rows]
    finally:
        conn.close()


def load_mcp_json(filepath: str) -> tuple[list[str], list[dict]]:
    """Parse a JSON result file from the Dataverse MCP execute_sql tool."""
    with open(filepath, "r") as f:
        data = json.load(f)
    columns = data.get("columns", [])
    rows = data.get("data", [])
    return columns, rows


def aggregate_operator_data(operator_rows: list[dict]) -> Dict[str, dict]:
    """
    Aggregate per-version RHACS operator rows into one summary per EBS account.

    For each account, collects:
      - all RHACS versions in use
      - total clusters and cores across versions
      - overall first/last active dates
      - total failure count and descriptions
    """
    by_account: Dict[str, dict] = {}
    for row in operator_rows:
        acct = row.get("EBS_ACCOUNT_NUMBER", "")
        if not acct:
            continue
        if acct not in by_account:
            by_account[acct] = {
                "rhacs_versions": [],
                "rhacs_total_clusters": 0.0,
                "rhacs_total_cores": 0.0,
                "rhacs_first_active": None,
                "rhacs_last_active": None,
                "rhacs_total_failures": 0.0,
                "rhacs_failure_descriptions": [],
            }
        rec = by_account[acct]

        ver = row.get("RHACS_VERSION", "")
        if ver and ver not in rec["rhacs_versions"]:
            rec["rhacs_versions"].append(ver)

        clusters = row.get("RHACS_CLUSTERS") or 0
        cores = row.get("RHACS_CORES") or 0
        failures = row.get("RHACS_FAILURES") or 0
        rec["rhacs_total_clusters"] += float(clusters)
        rec["rhacs_total_cores"] += float(cores)
        rec["rhacs_total_failures"] += float(failures)

        first = str(row.get("RHACS_FIRST_ACTIVE", "") or "")
        last = str(row.get("RHACS_LAST_ACTIVE", "") or "")
        if first and (rec["rhacs_first_active"] is None or first < rec["rhacs_first_active"]):
            rec["rhacs_first_active"] = first
        if last and (rec["rhacs_last_active"] is None or last > rec["rhacs_last_active"]):
            rec["rhacs_last_active"] = last

        desc = row.get("RHACS_FAILURE_DESC") or ""
        if desc:
            rec["rhacs_failure_descriptions"].append(desc)

    return by_account


CSV_COLUMNS = [
    "ebs_account",
    "as_on_date",
    "recent_1day_avg_cores",
    "last_7days_avg_cores",
    "last_30days_avg_cores",
    "last_90days_avg_cores",
    "last_365days_avg_cores",
    "recent_1day_clusters",
    "accnt_risk_opp_flag",
    "accnt_risk_opp_level",
    "accnt_risk_opp_desc",
    "version_currency_flag",
    "version_currency_level",
    "version_currency_desc",
    "aws_clusters",
    "azure_clusters",
    "gcp_clusters",
    "cs_aws_cores",
    "cs_azure_cores",
    "cs_gcp_cores",
    "has_amazon_eks_risk",
    "has_microsoft_aks_risk",
    "has_google_gke_risk",
    "cs_takeover_desc",
    "unattached_clusters",
    "attached_clusters",
    "unattached_cluster_flag",
    "cost_optimization_clusters",
    "cost_optimization_flag",
    "connected_status",
    "is_pp_opr",
    "is_odf_opr",
    "is_quay_opr",
    "is_acm_opr",
    "is_acs_opr",
    "rhacs_versions",
    "rhacs_total_clusters",
    "rhacs_total_cores",
    "rhacs_first_active",
    "rhacs_last_active",
    "rhacs_total_failures",
    "rhacs_failure_descriptions",
]

INDICATOR_KEY_MAP = {
    "EBS_ACCOUNT": "ebs_account",
    "AS_ON_DATE": "as_on_date",
    "RECENT_1DAY_AVG_CORES": "recent_1day_avg_cores",
    "LAST_7DAYS_AVG_CORES": "last_7days_avg_cores",
    "LAST_30DAYS_AVG_CORES": "last_30days_avg_cores",
    "LAST_90DAYS_AVG_CORES": "last_90days_avg_cores",
    "LAST_365DAYS_AVG_CORES": "last_365days_avg_cores",
    "RECENT_1DAY_CLUSTERS": "recent_1day_clusters",
    "ACCNT_RISK_OPP_FLAG": "accnt_risk_opp_flag",
    "ACCNT_RISK_OPP_LEVEL": "accnt_risk_opp_level",
    "ACCNT_RISK_OPP_DESC": "accnt_risk_opp_desc",
    "VERSION_CURRENCY_FLAG": "version_currency_flag",
    "VERSION_CURRENCY_LEVEL": "version_currency_level",
    "VERSION_CURRENCY_DESC": "version_currency_desc",
    "AWS_CLUSTERS": "aws_clusters",
    "AZURE_CLUSTERS": "azure_clusters",
    "GCP_CLUSTERS": "gcp_clusters",
    "CS_AWS_CORES": "cs_aws_cores",
    "CS_AZURE_CORES": "cs_azure_cores",
    "CS_GCP_CORES": "cs_gcp_cores",
    "HAS_AMAZON_EKS_RISK": "has_amazon_eks_risk",
    "HAS_MICROSOFT_AKS_RISK": "has_microsoft_aks_risk",
    "HAS_GOOGLE_GKE_RISK": "has_google_gke_risk",
    "CS_TAKEOVER_DESC": "cs_takeover_desc",
    "UNATTACHED_CLUSTERS": "unattached_clusters",
    "ATTACHED_CLUSTERS": "attached_clusters",
    "UNATTACHED_CLUSTER_FLAG": "unattached_cluster_flag",
    "COST_OPTIMIZATION_CLUSTERS": "cost_optimization_clusters",
    "COST_OPTIMIZATION_FLAG": "cost_optimization_flag",
    "CONNECTED_STATUS": "connected_status",
    "IS_PP_OPR": "is_pp_opr",
    "IS_ODF_OPR": "is_odf_opr",
    "IS_QUAY_OPR": "is_quay_opr",
    "IS_ACM_OPR": "is_acm_opr",
    "IS_ACS_OPR": "is_acs_opr",
}


def build_csv_rows(
    indicator_rows: list[dict],
    operator_agg: Dict[str, dict],
) -> list[dict]:
    """Merge indicator rows with aggregated operator data into flat CSV rows."""
    result = []
    for ind in indicator_rows:
        row: dict = {}
        for src_key, dst_key in INDICATOR_KEY_MAP.items():
            val = ind.get(src_key, "")
            if val is None:
                val = ""
            row[dst_key] = val
        acct = str(row["ebs_account"])
        opr = operator_agg.get(acct, {})
        row["rhacs_versions"] = " | ".join(opr.get("rhacs_versions", []))
        row["rhacs_total_clusters"] = opr.get("rhacs_total_clusters", "")
        row["rhacs_total_cores"] = opr.get("rhacs_total_cores", "")
        row["rhacs_first_active"] = opr.get("rhacs_first_active", "")
        row["rhacs_last_active"] = opr.get("rhacs_last_active", "")
        row["rhacs_total_failures"] = opr.get("rhacs_total_failures", "")
        descs = opr.get("rhacs_failure_descriptions", [])
        row["rhacs_failure_descriptions"] = " | ".join(descs) if descs else ""
        result.append(row)
    return result


def write_csv(rows: list[dict], output_path: Path) -> Path:
    """Write rows to CSV."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
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


def run_from_snowflake(conn_params: dict) -> tuple[list[dict], list[dict]]:
    """Fetch both datasets from Snowflake."""
    print("Querying Snowflake for RHACS usage indicators...")
    _, ind_rows = query_snowflake(INDICATORS_SQL, conn_params)
    print(f"  -> {len(ind_rows)} accounts with ACS operator")

    print("Querying Snowflake for RHACS operator details...")
    _, opr_rows = query_snowflake(OPERATOR_SQL, conn_params)
    print(f"  -> {len(opr_rows)} operator version records")

    return ind_rows, opr_rows


def run_from_json(indicators_file: str, operator_file: str) -> tuple[list[dict], list[dict]]:
    """Load both datasets from MCP JSON output files."""
    print(f"Loading indicators from {indicators_file}...")
    _, ind_rows = load_mcp_json(indicators_file)
    print(f"  -> {len(ind_rows)} accounts with ACS operator")

    print(f"Loading operator data from {operator_file}...")
    _, opr_rows = load_mcp_json(operator_file)
    print(f"  -> {len(opr_rows)} operator version records")

    return ind_rows, opr_rows


def main():
    parser = argparse.ArgumentParser(
        description="Export RHACS telemetry data to CSV and upload to NotebookLM",
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
        "--from-json",
        nargs=2,
        metavar=("INDICATORS_JSON", "OPERATOR_JSON"),
        help="Parse pre-fetched MCP JSON files instead of querying Snowflake",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output CSV path (default: rhacs_telemetry_YYYYMMDD_HHMMSS.csv)",
    )
    args = parser.parse_args()

    if args.from_json:
        ind_rows, opr_rows = run_from_json(args.from_json[0], args.from_json[1])
    elif SNOWFLAKE_AVAILABLE:
        sf_account = os.getenv("SNOWFLAKE_ACCOUNT", "")
        sf_user = os.getenv("SNOWFLAKE_USER", "")
        sf_warehouse = os.getenv("SNOWFLAKE_WAREHOUSE", "")
        sf_role = os.getenv("SNOWFLAKE_ROLE", "")
        sf_authenticator = os.getenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser")
        if not sf_account or not sf_user:
            print("Snowflake credentials not configured.")
            print("Set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER in .env,")
            print("or use --from-json with pre-fetched MCP JSON files.")
            sys.exit(1)
        conn_params = {
            "account": sf_account,
            "user": sf_user,
            "warehouse": sf_warehouse,
            "role": sf_role,
            "authenticator": sf_authenticator,
        }
        ind_rows, opr_rows = run_from_snowflake(conn_params)
    else:
        print("No data source available.")
        print("Either install snowflake-connector-python and set credentials,")
        print("or use --from-json with pre-fetched MCP JSON files.")
        sys.exit(1)

    operator_agg = aggregate_operator_data(opr_rows)
    csv_rows = build_csv_rows(ind_rows, operator_agg)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or Path(f"rhacs_telemetry_{ts}.csv")
    write_csv(csv_rows, output_path)
    print(f"Wrote {len(csv_rows)} accounts to {output_path}")

    if not args.skip_upload:
        if NOTEBOOKLM_AVAILABLE:
            success = asyncio.run(upload_to_notebooklm(output_path, args.notebook_name))
            if not success:
                sys.exit(1)
        else:
            print("Install notebooklm-py for upload: pip install 'notebooklm-py[browser]'")
            sys.exit(1)
    else:
        print("Skipping NotebookLM upload (--skip-upload)")


if __name__ == "__main__":
    main()

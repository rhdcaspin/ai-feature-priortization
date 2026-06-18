# rhacs_telemetry_export.py

Exports RHACS customer telemetry data from Snowflake or pre-fetched JSON to CSV and optionally uploads to NotebookLM.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--skip-upload` | `False` | — | Generate CSV only, skip NotebookLM upload |
| `--notebook-name` | `The Big Notebook for RHACS Product Management` | — | NotebookLM notebook title |
| `--from-json` | — | — | Two JSON file paths: indicators and operator data (skips Snowflake) |
| `--output` / `-o` | `rhacs_telemetry_<timestamp>.csv` | — | Output CSV path |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SNOWFLAKE_ACCOUNT` | For direct Snowflake | Snowflake account identifier |
| `SNOWFLAKE_USER` | For direct Snowflake | Snowflake username |
| `SNOWFLAKE_WAREHOUSE` | No | Snowflake warehouse |
| `SNOWFLAKE_ROLE` | No | Snowflake role |
| `SNOWFLAKE_AUTHENTICATOR` | No | Default: `externalbrowser` |

## Data sources

Two SQL queries run against `TELESENSE_DB.OPENSHIFT_MARTS`:

1. **OCP_USAGE_INDICATORS** — account-level health, risk/opportunity, version currency, cloud provider clusters/cores, operator flags. Filtered to `IS_ACS_OPR = 1` and non-internal accounts.
2. **OCP_OPR_LASTACTIVE** — per-version RHACS operator details: clusters, cores, failures, first/last active dates. Filtered to `rhacs-operator`.

## Data flow

1. Fetches both datasets via Snowflake connector or loads from `--from-json` files (Dataverse MCP `execute_sql` output format: `{"columns": [...], "data": [...]}`)
2. Aggregates operator data per EBS account (total clusters/cores, all versions, date ranges, failure summaries)
3. Merges indicator rows with aggregated operator data into flat CSV rows (41 columns)
4. Writes CSV
5. Uploads to NotebookLM (unless `--skip-upload`)

## Dependencies

- `notebooklm_upload.py`
- Optional: `snowflake-connector-python` (for direct Snowflake access)

## Automation

- Second script in `run_daily_export.sh`
- `com.rox.daily-export.plist` — macOS launchd at 9:00 AM

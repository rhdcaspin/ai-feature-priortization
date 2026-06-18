# rhacs_cases_export.py

Exports RHACS support cases from the Red Hat Customer Portal Hydra search API to CSV and optionally uploads to NotebookLM.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--status` | `all` | — | Filter: `open`, `closed`, or `all` |
| `--since` | — | — | Only cases created since date (YYYY-MM-DD) |
| `--skip-upload` | `False` | — | Generate CSV only, skip NotebookLM upload |
| `--notebook-name` | `The Big Notebook for RHACS Product Management` | — | NotebookLM notebook title |
| `--output` / `-o` | `rhacs_cases_<timestamp>.csv` | — | Output CSV base path |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `RH_OFFLINE_TOKEN` | Yes | Red Hat offline token from [access.redhat.com/management/api](https://access.redhat.com/management/api) |

## Data flow

1. Exchanges offline token for short-lived access token via `rh_api.get_rh_access_token`
2. Builds Solr query for RHACS products (ACS for Kubernetes + ACS Cloud Service), with optional status and date filters
3. Paginates through Hydra search API (`/hydra/rest/search/cases`, 100 per page)
4. Maps Solr fields to CSV columns (26 columns: case number, product, version, summary, description, status, severity, account, contact, dates, tags, region, etc.)
5. Splits output into chunks of 1000 rows if large
6. Prints summary counts by product, status, and severity
7. Uploads CSV(s) to NotebookLM (unless `--skip-upload`)

## Dependencies

- `rh_api.py` (for `get_rh_access_token`)
- `notebooklm_upload.py`

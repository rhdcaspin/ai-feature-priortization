# rfe_export.py

Exports RHACS RFE (Feature Request) issues from Jira to CSV with SFDC/CIPOE/ROX enrichment and optional NotebookLM upload. Supports incremental runs.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--skip-upload` | `False` | — | Generate CSV only, skip NotebookLM upload |
| `--notebook-name` | `The Big Notebook for RHACS Product Management` | `NOTEBOOKLM_NOTEBOOK_NAME` | NotebookLM notebook title |
| `--force-all` | `False` | — | Export all open RHACS RFEs (ignore last-run state) |
| `--all-rfes` | `False` | — | Export every RHACS RFE regardless of status or date |
| `--output` / `-o` | `rfe_rhacs_export_<timestamp>.csv` | — | Output CSV path |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Default: `https://issues.redhat.com` |
| `JIRA_EMAIL` | Cloud only | Atlassian account email |
| `RH_OFFLINE_TOKEN` | No | Enables SFDC account name resolution via Hydra |
| `NOTEBOOKLM_NOTEBOOK_NAME` | No | Default notebook title override |

## Data flow

1. Connects to Jira, determines Cloud (v3) vs Server (v2) API
2. Builds JQL for open RHACS RFEs (component names containing "rhacs")
3. Incremental mode: filters by `updated >= last_run_date` from `.rfe_export_last_run`
4. For each RFE, enriches with:
   - SFDC case IDs from `customfield_12313441` and remote links (via `rh_api.extract_sfdc_case_ids`)
   - SFDC account names from Red Hat Hydra API (via `rh_api.fetch_case_account_name`)
   - CIPOE customer names from linked CIPOE issues (via `jira_utils.fetch_cipoe_summary`)
   - Linked ROX feature keys (via `jira_utils.extract_linked_keys`)
5. Writes CSV with enrichment columns (`_sfdc_case_ids`, `_sfdc_accounts`, `_cipoe_customers`, `_rox_keys`)
6. Saves last-run timestamp to `.rfe_export_last_run`
7. Uploads CSV to NotebookLM (unless `--skip-upload`)

## Dependencies

- `jira_auth.py`, `jira_utils.py`, `rh_api.py`, `notebooklm_upload.py`

## Automation

- Part of `run_daily_export.sh` (third script in the daily export job)
- `com.rox.daily-export.plist` — macOS launchd at 9:00 AM

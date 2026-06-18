# rox_feature_export_notebooklm.py

Exports ROX project features to CSV with SFDC/CIPOE/RFE customer enrichment and optional NotebookLM upload. Supports incremental runs.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--skip-upload` | `False` | — | Generate CSV only, skip NotebookLM upload |
| `--notebook-name` | `The Big Notebook for RHACS Product Management` | — | NotebookLM notebook title |
| `--force-all` | `False` | — | Export open features (ignore last-run state) |
| `--all-features` | `False` | — | Export ALL features regardless of status or date |
| `--output` / `-o` | `rox_features_export_<timestamp>.csv` | — | Output CSV path |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Default: `https://issues.redhat.com` |
| `JIRA_EMAIL` | Cloud only | Atlassian account email |
| `RH_OFFLINE_TOKEN` | No | Enables SFDC account name resolution via Hydra |
| `JIRA_RICE_SCORE_FIELD` | No | Override for RICE Score field ID (auto-discovered otherwise) |

## Data flow

1. Connects to Jira, auto-discovers RICE custom field IDs
2. Builds JQL for open ROX features (Backlog, New, Refinement, To Do)
3. Incremental mode: filters by `updated >= last_run_date` from `.rox_export_last_run`
4. For each feature, enriches with:
   - SFDC case IDs from all fields and remote links (local `extract_sfdc_case_ids` — broader scan than `rh_api.py` version)
   - CIPOE customer names: direct ROX->CIPOE links + indirect ROX->RFE->CIPOE links
   - RFE SFDC accounts: ROX->RFE->Salesforce case->Hydra account name
   - RICE scores (Reach, Impact, Confidence, Effort, RICE Score)
5. Writes CSV with priority columns: key, SFDC case IDs, RFE keys, RFE SFDC accounts, CIPOE customers, then RICE, then remaining fields
6. Saves last-run timestamp to `.rox_export_last_run`
7. Uploads CSV to NotebookLM (unless `--skip-upload`)

## Dependencies

- `jira_auth.py`, `jira_utils.py` (flatten_value, fetch_cipoe_summary), `rh_api.py` (get_rh_access_token, fetch_case_account_name), `notebooklm_upload.py`

## Note

This script has its **own** `extract_sfdc_case_ids` function (different from `rh_api.py`). It scans all issue fields for SFDC-pattern values and uses a cache keyed by issue_key. The `rh_api.py` version only checks `customfield_12313441` and remote links.

## Automation

- First script in `run_daily_export.sh`
- `com.rox.daily-export.plist` — macOS launchd at 9:00 AM

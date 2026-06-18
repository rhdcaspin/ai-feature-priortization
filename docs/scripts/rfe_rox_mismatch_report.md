# rfe_rox_mismatch_report.py

Finds open RHACS RFEs whose linked ROX features are already closed. Helps PMs spot feature requests that may need closing or updating after the linked feature shipped.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--skip-upload` | `False` | — | Generate CSV only, skip NotebookLM upload |
| `--notebook-name` | `The Big Notebook for RHACS Product Management` | — | NotebookLM notebook title |
| `--output` / `-o` | `rfe_rox_mismatch_<timestamp>.csv` | — | Output CSV path |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Default: `https://issues.redhat.com` |
| `JIRA_EMAIL` | Cloud only | Atlassian account email |
| `RH_OFFLINE_TOKEN` | No | Enables SFDC account name resolution via Hydra |

## Data flow

1. Fetches all RFE project components containing "rhacs"
2. Queries all open RHACS RFEs (New, Open, In Progress, To Do, Backlog, Refinement, Under Consideration)
3. For each RFE with linked ROX features, fetches the ROX issue status
4. If ROX status is closed (Closed, Done, Resolved, Verified, Release Pending) but RFE is open: mismatch found
5. Enriches mismatches with SFDC case IDs/accounts and CIPOE customer names
6. Writes CSV with columns: RFE key/status, ROX key/status/resolution/fix versions, SFDC data, CIPOE customers
7. Uploads CSV to NotebookLM (unless `--skip-upload`)

## Dependencies

- `jira_auth.py`, `jira_utils.py`, `rh_api.py`, `notebooklm_upload.py`

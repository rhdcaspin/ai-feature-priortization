# `rfe_export.py`

## Role

Exports **RFE** Jira issues whose components are RHACS-related to a CSV. Enriches rows with SFDC case IDs, customer account names (Red Hat Hydra API), linked CIPOE customers, and linked **ROX** feature keys. Optionally uploads the CSV to **NotebookLM**.

Supports **incremental** runs using a state file (only issues updated since last run), or full exports.

## Prerequisites

- `JIRA_TOKEN` / `JIRA_API_TOKEN` and `JIRA_BASE_URL` (default `issues.redhat.com` for RFE).
- Optional: `RH_OFFLINE_TOKEN` for richer account name resolution via Red Hat APIs.
- For upload: NotebookLM auth as described in [notebooklm_upload.md](notebooklm_upload.md).

## Common commands

```bash
# Incremental export (since last run), then upload to NotebookLM
python3 rfe_export.py

# All open RHACS RFEs (ignores last-run state)
python3 rfe_export.py --force-all

# Every RHACS RFE regardless of status
python3 rfe_export.py --all-rfes

# CSV only
python3 rfe_export.py --skip-upload

# Custom notebook title or output path
python3 rfe_export.py --notebook-name "My Notebook" -o ./out/rfe.csv
```

See `python3 rfe_export.py --help` for the full option list.

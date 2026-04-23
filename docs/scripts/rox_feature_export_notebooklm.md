# `rox_feature_export_notebooklm.py`

## Role

Exports **ROX** Jira **features** updated since the last run (or a rolling window on first run) to CSV, including key fields and customer names resolved via **Red Hat Hydra** when `RH_OFFLINE_TOKEN` is set (CIPOE / case linkage logic is in-script). Uploads the CSV to **NotebookLM** by default.

State is kept in `.rox_export_last_run` so subsequent runs are incremental.

## Prerequisites

- `JIRA_TOKEN` / `JIRA_API_TOKEN`, `JIRA_BASE_URL`, and on Cloud `JIRA_EMAIL`.
- Optional: `RH_OFFLINE_TOKEN` for account name enrichment.
- NotebookLM: see [notebooklm_upload.md](notebooklm_upload.md).

## Common commands

```bash
python3 rox_feature_export_notebooklm.py
python3 rox_feature_export_notebooklm.py --skip-upload
python3 rox_feature_export_notebooklm.py --force-all
python3 rox_feature_export_notebooklm.py --all-features
```

Optional flags include `--notebook-name`, `--drive-folder-id`, and output-related options. See `python3 rox_feature_export_notebooklm.py --help`.

This script is what `run_daily_export.sh` invokes for the ROX portion of the daily job; see [README_DAILY_EXPORT.md](../../README_DAILY_EXPORT.md).

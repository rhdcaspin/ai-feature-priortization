# `rfe_rox_mismatch_report.py`

## Role

Finds **open** RHACS **RFE** issues that link to **ROX** features which are already **closed** (or otherwise in a “done” resolution path). Helps PMs spot RFEs that may need closing or updating after the linked feature shipped.

Writes a CSV (timestamped filename by default) and can upload it to **NotebookLM**.

## Prerequisites

- Same Jira token variables as other scripts (`JIRA_TOKEN` / `JIRA_API_TOKEN`, `JIRA_BASE_URL`).
- Optional: `RH_OFFLINE_TOKEN` for case/account enrichment where the script uses Red Hat APIs.
- NotebookLM setup if not using `--skip-upload`.

## Common commands

```bash
python3 rfe_rox_mismatch_report.py
python3 rfe_rox_mismatch_report.py --skip-upload
python3 rfe_rox_mismatch_report.py -o report.csv
```

Optional: set `JIRA_BASE_URL` in `.env` (defaults to Red Hat Jira). Run `python3 rfe_rox_mismatch_report.py --help` for `--notebook-name` and `--output`.

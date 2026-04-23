# `rhacs_cases_export.py`

## Role

Queries the **Red Hat Customer Portal** Hydra search API for support **cases** tied to RHACS products (“Red Hat Advanced Cluster Security for Kubernetes” and “Red Hat Advanced Cluster Security Cloud Service”), writes **CSV** (chunked when very large), prints summary counts, and optionally uploads to **NotebookLM**.

## Prerequisites

- `RH_OFFLINE_TOKEN` in `.env` — create or rotate from [Red Hat API management](https://access.redhat.com/management/api).
- For NotebookLM upload: see [notebooklm_upload.md](notebooklm_upload.md).

## Common commands

```bash
# All RHACS cases (default)
python3 rhacs_cases_export.py

python3 rhacs_cases_export.py --status open
python3 rhacs_cases_export.py --status closed
python3 rhacs_cases_export.py --since 2025-01-01

python3 rhacs_cases_export.py --skip-upload
python3 rhacs_cases_export.py -o my_cases.csv
```

Run `python3 rhacs_cases_export.py --help` for details.

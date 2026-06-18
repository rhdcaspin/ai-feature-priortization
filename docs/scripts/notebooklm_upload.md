# notebooklm_upload.py

Unified CSV upload to Google NotebookLM. Supports two backends and auto-selects between them.

## Backends

| Backend | Install | Auth |
|---------|---------|------|
| **nlm** (preferred) | `pip install notebooklm-mcp-cli` | `nlm login` |
| **notebooklm-py** | `pip install 'notebooklm-py[browser]'` | `notebooklm login` |

## Functions

| Function | Description |
|----------|-------------|
| `notebooklm_upload_available()` | Returns `True` if either backend is usable. |
| `upload_csvs_to_notebook(csv_paths, notebook_name)` | Uploads one or more CSV files to a notebook by title. Creates the notebook if it doesn't exist. Returns `True` on success. |
| `find_notebook_id_by_title(notebook_title)` | Resolves a notebook UUID by exact title via `nlm`. Returns `None` if not found or `nlm` missing. |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTEBOOKLM_UPLOAD_BACKEND` | `auto` | `auto` / `nlm` / `py`. Auto tries nlm first, falls back to py on failure. |

## Used by

All export scripts that upload to NotebookLM:

- `rox_feature_export_notebooklm.py`
- `rfe_export.py`
- `rfe_rox_mismatch_report.py`
- `rhacs_telemetry_export.py`
- `rhacs_cases_export.py`
- `rox_target_version_labels_pm_validation.py` (uses `find_notebook_id_by_title` for RICE queries)

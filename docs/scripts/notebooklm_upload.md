# `notebooklm_upload.py`

## Role

Shared library for uploading **CSV files** to a **Google NotebookLM** notebook by title. Other export scripts call `upload_csvs_to_notebook()` after generating a CSV.

**Backends** (controlled by `NOTEBOOKLM_UPLOAD_BACKEND`, default `auto`):

1. **`nlm`** — CLI from `notebooklm-mcp-cli` (same auth as the NotebookLM MCP flow: `nlm login`).
2. **`notebooklm-py`** — Python client (`notebooklm login`).

If `auto`, the code prefers `nlm` when it is on `PATH`, otherwise falls back to `notebooklm-py`.

## Usage

Import from other scripts; there is **no standalone CLI**.

```python
from notebooklm_upload import notebooklm_upload_available, upload_csvs_to_notebook

if notebooklm_upload_available():
    upload_csvs_to_notebook(["/path/to/report.csv"], "My Notebook Title")
```

Helper: `find_notebook_id_by_title(title)` resolves a notebook UUID via `nlm` when available.

## Environment

| Variable | Values | Purpose |
|----------|--------|---------|
| `NOTEBOOKLM_UPLOAD_BACKEND` | `auto`, `nlm`, `py` | Which uploader to use. |

Install one path:

- `pip install notebooklm-mcp-cli` then `nlm login`, or  
- `pip install 'notebooklm-py[browser]'` then `notebooklm login`.

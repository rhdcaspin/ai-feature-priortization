# `rhacs_telemetry_export.py`

## Role

Builds a combined **RHACS customer telemetry** CSV from two Dataverse / Snowflake-style datasets (usage indicators and operator last-active rows), excluding internal accounts where configured. Optionally uploads the result to **NotebookLM**.

**Data access** (pick one):

1. **Pre-fetched JSON** — from Dataverse MCP `execute_sql` (or any tool that saves the expected `{ "columns", "data" }` shape). Pass two files: indicators JSON and operator JSON.
2. **Direct Snowflake** — set `SNOWFLAKE_*` environment variables and install `snowflake-connector-python`.

## Prerequisites

- `.env` with either Snowflake variables or use `--from-json` only.
- For upload: NotebookLM auth per [notebooklm_upload.md](notebooklm_upload.md).

## Common commands

```bash
# From MCP-exported JSON (paths are examples)
python3 rhacs_telemetry_export.py --from-json indicators.json operator.json

# Query Snowflake directly (connector + env required)
python3 rhacs_telemetry_export.py

python3 rhacs_telemetry_export.py --skip-upload
python3 rhacs_telemetry_export.py -o ./out/telemetry.csv
```

See `python3 rhacs_telemetry_export.py --help` and the script docstring for Snowflake variable names.

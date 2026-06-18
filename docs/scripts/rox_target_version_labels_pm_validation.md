# rox_target_version_labels_pm_validation.py

Syncs version labels on ROX Features, reports missing Product Manager assignments, validates template compliance, and optionally queries NotebookLM for RICE scores.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--target-version` | `5.0.0` | — | Target Version value and label to enforce |
| `--dry-run` | `False` | — | Skip label writes in Jira; still write CSV reports |
| `--jira-url` | `https://issues.redhat.com` | `JIRA_BASE_URL` | Jira URL |
| `--email` | — | `JIRA_EMAIL` | Atlassian account email |
| `--token` | — | `JIRA_TOKEN` / `JIRA_API_TOKEN` | API token |
| `--notebooklm-rice` | `False` | — | Query NotebookLM for RICE scores per feature |
| `--notebook-name` | `The Big Notebook for RHACS Product Management` | `NOTEBOOKLM_NOTEBOOK_NAME` | NotebookLM notebook title |
| `--rice-batch-size` | `2` | — | Features per NotebookLM batch |
| `--rice-jira-context` | `False` | `NOTEBOOKLM_RICE_INCLUDE_JIRA_CONTEXT` | Include full Jira description in RICE prompts |
| `--rice-desc-max` | Auto | `NOTEBOOKLM_RICE_DESC_MAX` / `_PY` | Max description chars in RICE prompt (0 = summary only) |
| `--rice-delay` | `2.0` | — | Seconds between NotebookLM batches |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Jira URL |
| `JIRA_EMAIL` | Cloud only | Atlassian account email |
| `NOTEBOOKLM_NOTEBOOK_NAME` | No | Default notebook title |
| `NOTEBOOKLM_RICE_SUMMARY_MAX` | No | Max chars of Jira summary per issue in RICE prompt (default: 500; 0 = keys only) |
| `NOTEBOOKLM_RICE_INCLUDE_JIRA_CONTEXT` | No | `1` = embed full Jira text in RICE prompts |
| `NOTEBOOKLM_RICE_DESC_MAX` | No | Default: 600 (nlm path) |
| `NOTEBOOKLM_RICE_DESC_MAX_PY` | No | Default: 1800 (notebooklm-py path) |
| `NOTEBOOKLM_RICE_QUERY_TIMEOUT` | No | Default: 600 seconds per batch |

## Data flow

1. Connects to Jira via `JiraFeatureValidator` for the target version
2. For each feature, checks if the version label (e.g. `5.0.0`) is present; adds it if missing (unless `--dry-run`)
3. Validates each feature description against template sections (same rules as `jira_feature_validator.py`)
4. Optionally queries NotebookLM for RICE scores:
   - Prefers `nlm` CLI; falls back to `notebooklm-py`
   - Sends batch prompts asking for Reach/Impact/Confidence/Effort per the ACS RICE framework
   - Splits batches on `INVALID_ARGUMENT` errors
5. Writes two CSV reports to `output/`:
   - Full report: `rox_<version>_labels_pm_validation_<timestamp>.csv`
   - Missing PM only: `rox_<version>_missing_product_manager_<timestamp>.csv`

## Dependencies

- `jira_auth.py`, `jira_feature_validator.py` (validation logic, ADF parsing)
- `notebooklm_upload.py` (for `find_notebook_id_by_title`)
- Optional: `notebooklm-py` library or `nlm` CLI for RICE scoring

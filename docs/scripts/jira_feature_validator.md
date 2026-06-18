# jira_feature_validator.py

Validates ROX Feature descriptions against a required template, checks RICE field completeness and product-pillar labels, exports a compliance CSV, and optionally uploads to Google Sheets.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--target-version` | `5.0.0` | — | Target Version to filter features |
| `--jira-url` | `https://redhat.atlassian.net` | `JIRA_BASE_URL` | Jira base URL |
| `--email` | — | `JIRA_EMAIL` | Atlassian account email (required for Cloud) |
| `--token` | — | `JIRA_TOKEN` / `JIRA_API_TOKEN` | Jira API token |
| `--no-rice-comments` | `False` | — | Skip posting Jira comments when RICE fields are missing |
| `--update-sheet` | `False` | — | Upload compliance CSV to Google Sheets |
| `--sheet-id` | — | `GOOGLE_SHEET_ID` | Google Spreadsheet ID |
| `--sheet-name` | `5.0 Plan` | `GOOGLE_SHEET_NAME` | Sheet tab name to replace |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Jira site URL |
| `JIRA_EMAIL` | Cloud only | Atlassian account email |
| `JIRA_PRODUCT_MANAGER_FIELD` | No | Custom field ID override (default: auto Cloud/Server) |
| `JIRA_TARGET_VERSION_FIELD` | No | Custom field ID override |
| `JIRA_RICE_REACH_FIELD` | No | RICE Reach custom field ID (auto-discovered if unset) |
| `JIRA_RICE_IMPACT_FIELD` | No | RICE Impact custom field ID |
| `JIRA_RICE_CONFIDENCE_FIELD` | No | RICE Confidence custom field ID |
| `JIRA_RICE_EFFORT_FIELD` | No | RICE Effort custom field ID |
| `JIRA_RICE_SCORE_FIELD` | No | RICE Score custom field ID |
| `JIRA_RANK_FIELD` | No | Rank (LexoRank) custom field ID |
| `GOOGLE_SHEET_ID` | For `--update-sheet` | Spreadsheet ID |
| `GOOGLE_SHEET_NAME` | No | Tab name |

## Data flow

1. Connects to Jira, auto-discovers RICE custom field IDs
2. Fetches ROX Features for the target version (excludes Done status category), ordered by Rank ASC
3. Validates each feature description against 6 template sections (4 required, 2 optional)
4. Checks RICE field completeness (Reach, Impact, Confidence, Effort, RICE Score)
5. Checks for at least one product-pillar label
6. Posts Jira comment @mentioning PM and Assignee on issues with missing RICE (unless `--no-rice-comments`)
7. Writes compliance CSV to `output/rox_<version>_compliance_<timestamp>.csv`
8. Optionally uploads to Google Sheets with HYPERLINK formulas on Key column

## Dependencies

- `jira_auth.py` — token and Cloud detection
- Google Sheets API via `gcloud auth print-access-token` (for `--update-sheet`)

## Also provides

`JiraFeatureValidator` class and `upload_csv_to_google_sheet()` function are imported by other scripts (`rox_rice_rank_sync.py`, `rox_feature_category_labels.py`, `rox_target_version_labels_pm_validation.py`, `rox_assignee_pm_report.py`).

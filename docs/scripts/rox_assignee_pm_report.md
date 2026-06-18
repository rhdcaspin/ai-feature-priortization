# rox_assignee_pm_report.py

Exports ROX issues where Assignee or Product Manager (and optionally Reporter) matches a person. Only issues not in the Done status category are included.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--name` | `Anjali Talang` | — | Display name to match via Jira user search |
| `--account-id` | — | — | Jira accountId(s), comma-separated (skips user search) |
| `--include-reporter` | `False` | — | Also include issues where the person is Reporter |
| `--google-sheet-title` | — | — | Create a new Google Sheet with this title and upload |
| `--update-sheet` | `False` | — | Upload CSV to an existing spreadsheet |
| `--sheet-id` | — | `GOOGLE_SHEET_ID` | Spreadsheet ID for `--update-sheet` |
| `--sheet-name` | `Sheet1` | `GOOGLE_SHEET_NAME` | Tab name for `--update-sheet` |
| `--project` | `ROX` | — | Project key |
| `--output` / `-o` | `output/rox_<project>_<name>_<timestamp>.csv` | — | Output CSV path |
| `--jira-url` | `https://issues.redhat.com` | `JIRA_BASE_URL` | Jira URL |
| `--email` | — | `JIRA_EMAIL` | Atlassian account email |
| `--token` | — | `JIRA_TOKEN` / `JIRA_API_TOKEN` | API token |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Jira URL |
| `JIRA_EMAIL` | Cloud only | Atlassian account email |
| `JIRA_PRODUCT_MANAGER_FIELD` | No | Custom field ID for Product Manager |
| `JIRA_PRODUCT_MANAGER_JQL` | No | JQL name for PM field (default: `Product Manager`) |
| `JIRA_TARGET_VERSION_FIELD` | No | Custom field ID for Target Version |
| `GOOGLE_SHEET_ID` | For `--update-sheet` | Spreadsheet ID |
| `GOOGLE_SHEET_NAME` | No | Tab name |

## Data flow

1. Resolves person by display name via Jira user search (handles spelling variants like Talang/Telang)
2. Runs separate JQL queries for Assignee, Product Manager, and optionally Reporter
3. Merges results (OR semantics, deduplicated by issue key)
4. Writes CSV with columns: Key, Summary, Status, Issue Type, Reporter, Assignee, Product Manager, Target Version, Labels, Created, Updated, URL
5. Optionally creates a new Google Sheet or uploads to an existing one (Key column gets HYPERLINK formulas)

## Dependencies

- `jira_auth.py`
- `jira_feature_validator.py` (JiraFeatureValidator for connection, `upload_csv_to_google_sheet`, `_get_gcloud_access_token`)

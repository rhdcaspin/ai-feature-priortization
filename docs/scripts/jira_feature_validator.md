# `jira_feature_validator.py`

## Role

Connects to Jira, loads **ROX** issues of type **Feature** for a given **Target Version**, and checks that the description follows the expected product template (Goal Summary, Goals/outcomes, Acceptance Criteria, Success criteria, optional sections). Produces a timestamped **compliance CSV** under `output/`. Optionally uploads that CSV to **Google Sheets** via a `gcloud` access token. On upload, the **Key** column becomes a clickable **HYPERLINK** to `{JIRA_BASE_URL}/browse/<KEY>` (default base `https://redhat.atlassian.net` if unset).

Issues in Jira’s **Done** status category (for example Closed) are **not** fetched, so completed features are excluded from validation.

## Prerequisites

- Python dependencies from `requirements.txt`.
- `.env` with Jira settings (see below).
- For `--update-sheet`: `gcloud auth login --enable-gdrive-access` so `gcloud auth print-access-token` works.

## Environment

| Variable | Purpose |
|----------|---------|
| `JIRA_BASE_URL` | Jira site URL (Cloud or Server/DC). |
| `JIRA_EMAIL` | Required on Atlassian Cloud (paired with API token). |
| `JIRA_TOKEN` or `JIRA_API_TOKEN` | API token or PAT. |
| `JIRA_PRODUCT_MANAGER_FIELD`, `JIRA_TARGET_VERSION_FIELD`, `JIRA_RICE_*` | Optional custom field overrides. |
| `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_NAME` | Optional defaults for `--sheet-id` / `--sheet-name`. |

## Common commands

```bash
# Validate features for a target version (CSV under output/)
python3 jira_feature_validator.py --target-version 5.0.0

# Jira Cloud example (set JIRA_EMAIL in .env)
python3 jira_feature_validator.py --target-version 5.0.0 \
  --jira-url https://your-site.atlassian.net

# Also push CSV to Google Sheets
python3 jira_feature_validator.py --target-version 5.0.0 --update-sheet
```

Run `python3 jira_feature_validator.py --help` for all flags (`--token`, `--sheet-id`, etc.).

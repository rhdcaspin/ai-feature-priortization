# `rox_assignee_pm_report.py`

## Role

Exports **ROX** issues where **Assignee** or **Product Manager** matches a given person (resolved via Jira user search by display name, or directly by **account id**). Only issues whose status category is **not Done** are included. Writes a **CSV** report to disk (default under `output/` or path from `-o`).

Uses the same Jira session patterns as `jira_feature_validator.py` (Cloud vs Server/DC).

## Prerequisites

- `JIRA_TOKEN` / `JIRA_API_TOKEN`, `JIRA_BASE_URL`.
- On Atlassian Cloud: `JIRA_EMAIL`.
- Optional: `JIRA_PRODUCT_MANAGER_FIELD`, `JIRA_PRODUCT_MANAGER_JQL` if your site uses different custom field or JQL names for Product Manager.

## Common commands

```bash
python3 rox_assignee_pm_report.py --name "Jane Doe"
python3 rox_assignee_pm_report.py --name "Jane Doe" -o report.csv
python3 rox_assignee_pm_report.py --account-id "557058:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Run `python3 rox_assignee_pm_report.py --help` for project key, Jira URL, and other overrides.

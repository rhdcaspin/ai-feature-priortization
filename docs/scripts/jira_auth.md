# jira_auth.py

Shared helper to read Jira API credentials from environment variables and detect Cloud vs Server instances.

## Functions

| Function | Returns | Description |
|----------|---------|-------------|
| `jira_api_token_from_env()` | `str` | Returns `JIRA_TOKEN` (or `JIRA_API_TOKEN` if unset), stripped. Empty string if neither is set. |
| `is_jira_cloud_url(url)` | `bool` | `True` if the URL contains `atlassian.net` (case-insensitive). Used by all scripts to choose Basic auth (Cloud) vs Bearer token (Server/DC). |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` | Yes (or `JIRA_API_TOKEN`) | Jira API token (Cloud) or PAT (Server/DC) |
| `JIRA_API_TOKEN` | Fallback | Used only if `JIRA_TOKEN` is unset |

## Notes

- This file is **imported**, not run as a CLI.
- Does **not** call `load_dotenv` itself. The importing script must load `.env` before importing.
- All Python scripts in this repo import from this module for token access and Cloud detection.

# `jira_auth.py`

## Role

Small shared helper used by other scripts. It reads the Jira API token from the environment and detects whether a Jira base URL is **Atlassian Cloud** (`*.atlassian.net`) versus **Server/Data Center** (for example `issues.redhat.com`), so callers can pick Basic auth (email + token) or Bearer token as appropriate.

## Usage

This file is **imported**, not run as a CLI. In your entrypoint script, load `.env` **before** importing if you rely on a project `.env` file:

```python
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from jira_auth import jira_api_token_from_env, is_jira_cloud_url
```

## Environment

| Variable | Purpose |
|----------|---------|
| `JIRA_TOKEN` | Preferred API token (or PAT on Server/DC). |
| `JIRA_API_TOKEN` | Alternative name if `JIRA_TOKEN` is unset. |

## API

- `jira_api_token_from_env() -> str` — returns stripped token or empty string.
- `is_jira_cloud_url(url: str) -> bool` — true when the host indicates Jira Cloud.

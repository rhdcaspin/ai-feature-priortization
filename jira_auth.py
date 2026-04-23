"""Jira API token from environment. Load dotenv in the caller before importing if you use .env."""

import os


def jira_api_token_from_env() -> str:
    """Return ``JIRA_TOKEN`` or, if unset, ``JIRA_API_TOKEN`` (strip whitespace)."""
    return (os.getenv("JIRA_TOKEN") or os.getenv("JIRA_API_TOKEN") or "").strip()


def is_jira_cloud_url(url: str) -> bool:
    """True if ``url`` is Jira Cloud (host contains ``atlassian.net``). Case-insensitive."""
    return "atlassian.net" in (url or "").lower()

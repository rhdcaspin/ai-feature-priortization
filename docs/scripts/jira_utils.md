# jira_utils.py

Shared Jira helpers extracted from multiple export scripts. Handles pagination, field flattening, linked-issue extraction, and CIPOE customer name lookup.

## Functions

| Function | Description |
|----------|-------------|
| `create_jira_session(jira_url, api_token, email)` | Returns a `requests.Session` with Cloud (Basic) or Server (Bearer) auth headers. |
| `jira_api_version(jira_url)` | Returns `"3"` for Cloud, `"2"` for Server/Data Center. |
| `jira_search_paginated(session, jira_url, jql, fields_param, *, max_results, timeout)` | Unified JQL search with Cloud `nextPageToken` or Server `startAt` pagination. Returns all matching issues. |
| `flatten_value(val)` | Converts any Jira field value (dict, list, scalar) to a CSV-safe string. Extracts `name`/`displayName`/`key`/`value` from objects; pipe-separates lists. |
| `extract_linked_keys(issuelinks, prefix)` | Returns deduplicated issue keys from `issuelinks` whose project key matches `prefix` (e.g. `"ROX"`, `"CIPOE"`, `"RFE"`). |
| `fetch_cipoe_summary(cipoe_key, jira_url, session, api_version, cache)` | Fetches the CIPOE issue summary (customer name) with an in-memory cache. |

## Used by

- `rfe_export.py` — `flatten_value`, `extract_linked_keys`, `fetch_cipoe_summary`
- `rfe_rox_mismatch_report.py` — same three
- `rox_feature_export_notebooklm.py` — `flatten_value`, `fetch_cipoe_summary`

## Dependencies

- `jira_auth.py` (for `is_jira_cloud_url`)
- `requests`

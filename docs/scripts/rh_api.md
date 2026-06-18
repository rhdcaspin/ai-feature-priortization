# rh_api.py

Shared Red Hat Customer Portal / Hydra API helpers for SSO token exchange and SFDC case lookups.

## Functions

| Function | Description |
|----------|-------------|
| `get_rh_access_token(offline_token)` | Exchanges a Red Hat offline token for a short-lived access token via SSO. Returns `None` on failure. |
| `fetch_case_account_name(case_number, access_token, cache)` | Looks up the customer/account name for a support case via the Hydra REST API. Cached in-memory. |
| `extract_sfdc_case_ids(rfe_fields, rfe_key, jira_url, session, api_version)` | Extracts SFDC case IDs from `customfield_12313441` (SFDC Cases Links) and Jira remote links matching Salesforce patterns. |

## Constants

| Name | Value |
|------|-------|
| `RH_SSO_TOKEN_URL` | `https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token` |
| `RH_HYDRA_CASE_URL` | `https://access.redhat.com/hydra/rest/cases` |

## Environment variables

| Variable | Required | Used by |
|----------|----------|---------|
| `RH_OFFLINE_TOKEN` | Optional | All scripts that call `get_rh_access_token` |

## Used by

- `rfe_export.py`, `rfe_rox_mismatch_report.py` — all three functions
- `rox_feature_export_notebooklm.py` — `get_rh_access_token`, `fetch_case_account_name`
- `rhacs_cases_export.py` — `get_rh_access_token` only

## Note on `rox_feature_export_notebooklm.py`

That script has its **own** `extract_sfdc_case_ids` with a different signature and broader field scanning logic. It does not use the version in this module.

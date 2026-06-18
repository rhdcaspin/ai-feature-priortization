# RHACS Feature Prioritization Tooling

Scripts for RHACS product management workflows: Jira feature validation, RICE-based rank sync, RFE/customer data enrichment, telemetry exports, and AI-powered pillar classification.

## Quick start

1. Copy `.env.example` to `.env` and set `JIRA_TOKEN`, `JIRA_BASE_URL`, and (for Atlassian Cloud) `JIRA_EMAIL`. **Never commit `.env`.**
2. `pip install -r requirements.txt`
3. Run any script: `python3 <script>.py --help`

## Script index

### Jira validation and planning

| Script | Purpose | Guide |
|--------|---------|-------|
| `jira_feature_validator.py` | Validate ROX features against template, export compliance CSV, upload to Google Sheets | [scripts/jira_feature_validator.md](scripts/jira_feature_validator.md) |
| `rox_rice_rank_sync.py` | Sync Jira Rank (LexoRank) from RICE Score, respecting manual overrides | [scripts/rox_rice_rank_sync.md](scripts/rox_rice_rank_sync.md) |
| `rox_target_version_labels_pm_validation.py` | Sync version label, PM gap report, template validation, optional NotebookLM RICE | [scripts/rox_target_version_labels_pm_validation.md](scripts/rox_target_version_labels_pm_validation.md) |
| `rox_feature_category_labels.py` | AI-classify features into product-pillar labels via Ollama | [scripts/rox_feature_category_labels.md](scripts/rox_feature_category_labels.md) |
| `rox_assignee_pm_report.py` | Export ROX issues by person (assignee/PM) to CSV or Google Sheets | [scripts/rox_assignee_pm_report.md](scripts/rox_assignee_pm_report.md) |

### Data exports

| Script | Purpose | Guide |
|--------|---------|-------|
| `rox_feature_export_notebooklm.py` | Export ROX features with SFDC/CIPOE/RFE enrichment to CSV + NotebookLM | [scripts/rox_feature_export_notebooklm.md](scripts/rox_feature_export_notebooklm.md) |
| `rfe_export.py` | Export RHACS RFEs with SFDC/CIPOE enrichment to CSV + NotebookLM | [scripts/rfe_export.md](scripts/rfe_export.md) |
| `rfe_rox_mismatch_report.py` | Find open RFEs whose linked ROX features are already closed | [scripts/rfe_rox_mismatch_report.md](scripts/rfe_rox_mismatch_report.md) |
| `rhacs_telemetry_export.py` | RHACS customer telemetry from Snowflake/JSON to CSV + NotebookLM | [scripts/rhacs_telemetry_export.md](scripts/rhacs_telemetry_export.md) |
| `rhacs_cases_export.py` | RHACS support cases from Red Hat Hydra to CSV + NotebookLM | [scripts/rhacs_cases_export.md](scripts/rhacs_cases_export.md) |

### Shared modules

| Module | Purpose | Guide |
|--------|---------|-------|
| `jira_auth.py` | Jira token from env, Cloud URL detection | [scripts/jira_auth.md](scripts/jira_auth.md) |
| `jira_utils.py` | Shared Jira helpers: pagination, flatten_value, linked keys, CIPOE lookup | [scripts/jira_utils.md](scripts/jira_utils.md) |
| `rh_api.py` | Shared Red Hat API helpers: SSO token exchange, Hydra case lookup, SFDC extraction | [scripts/rh_api.md](scripts/rh_api.md) |
| `notebooklm_upload.py` | Unified NotebookLM upload (nlm CLI / notebooklm-py) | [scripts/notebooklm_upload.md](scripts/notebooklm_upload.md) |

## Daily automation

Two launchd jobs run on macOS:

| Schedule | Shell script | plist | What it runs |
|----------|-------------|-------|--------------|
| **8:30 AM** | `run_daily_rank_sync.sh` | `com.rox.daily-rank-sync.plist` | `rox_rice_rank_sync.py --apply --only-if-changed --force-rank` |
| **9:00 AM** | `run_daily_export.sh` | `com.rox.daily-export.plist` | `rox_feature_export_notebooklm.py` + `rhacs_telemetry_export.py` + `rfe_export.py` |

### Setup (one-time)

1. Edit paths in the `.plist` files to match your machine.
2. Copy to LaunchAgents:
   ```bash
   cp com.rox.daily-*.plist ~/Library/LaunchAgents/
   chmod 644 ~/Library/LaunchAgents/com.rox.daily-*.plist
   ```
3. Load:
   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rox.daily-export.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rox.daily-rank-sync.plist
   ```

### Logs

- `~/Library/Logs/rox-daily-export.log` / `.err.log`
- `~/Library/Logs/rox-daily-rank-sync.log` / `.err.log`

### Useful commands

```bash
# Check if loaded
launchctl print gui/$(id -u)/com.rox.daily-export

# Unload
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.rox.daily-export.plist

# Run manually
./run_daily_export.sh
./run_daily_rank_sync.sh
```

## Environment variables

See [../.env.example](../.env.example) for the full list with which scripts use each variable.

## Security

- Never commit `.env`, API tokens, offline tokens, or Snowflake passwords.
- Prefer least-privilege Jira tokens and rotate on schedule.

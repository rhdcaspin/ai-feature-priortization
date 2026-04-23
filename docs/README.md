# Documentation

Per-script guides live under [scripts/](scripts/). Each page describes what the script does, required environment variables, and common commands.

## Script index

| Script | Guide |
|--------|--------|
| `jira_auth.py` | [scripts/jira_auth.md](scripts/jira_auth.md) |
| `jira_feature_validator.py` | [scripts/jira_feature_validator.md](scripts/jira_feature_validator.md) |
| `notebooklm_upload.py` | [scripts/notebooklm_upload.md](scripts/notebooklm_upload.md) |
| `rfe_export.py` | [scripts/rfe_export.md](scripts/rfe_export.md) |
| `rfe_rox_mismatch_report.py` | [scripts/rfe_rox_mismatch_report.md](scripts/rfe_rox_mismatch_report.md) |
| `rhacs_cases_export.py` | [scripts/rhacs_cases_export.md](scripts/rhacs_cases_export.md) |
| `rhacs_telemetry_export.py` | [scripts/rhacs_telemetry_export.md](scripts/rhacs_telemetry_export.md) |
| `rox_assignee_pm_report.py` | [scripts/rox_assignee_pm_report.md](scripts/rox_assignee_pm_report.md) |
| `rox_feature_category_labels.py` | [scripts/rox_feature_category_labels.md](scripts/rox_feature_category_labels.md) |
| `rox_feature_export_notebooklm.py` | [scripts/rox_feature_export_notebooklm.md](scripts/rox_feature_export_notebooklm.md) |
| `rox_target_version_labels_pm_validation.py` | [scripts/rox_target_version_labels_pm_validation.md](scripts/rox_target_version_labels_pm_validation.md) |

## Environment

Copy `.env.example` to `.env` and fill in values locally. **Never commit `.env`** (it is listed in `.gitignore`).

## Other guides

- [../README_DAILY_EXPORT.md](../README_DAILY_EXPORT.md) — scheduled ROX / telemetry / RFE export
- [../README_DAILY_VALIDATION.md](../README_DAILY_VALIDATION.md) — template validation workflow

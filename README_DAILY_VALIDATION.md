# ROX feature template validation

This repository validates **ROX** Jira **Features** against the product description template (Goal Summary, goals/outcomes, acceptance criteria, success criteria, and optional sections). Completed issues (**Done** status category, for example Closed) are excluded from the main validator query.

## Which script to use

| Goal | Script | Documentation |
|------|--------|----------------|
| Compliance CSV + optional Google Sheets for a **Target Version** | `jira_feature_validator.py` | [docs/scripts/jira_feature_validator.md](docs/scripts/jira_feature_validator.md) |
| Version labels on issues, PM gap report, same compliance columns, optional **NotebookLM RICE** | `rox_target_version_labels_pm_validation.py` | [docs/scripts/rox_target_version_labels_pm_validation.md](docs/scripts/rox_target_version_labels_pm_validation.md) |
| **Rank** backlog order from **RICE Score** (respects manual rank overrides) | `rox_rice_rank_sync.py` | [docs/scripts/rox_rice_rank_sync.md](docs/scripts/rox_rice_rank_sync.md) |

## Quick start

1. Copy `.env.example` to `.env` and set `JIRA_TOKEN` (or `JIRA_API_TOKEN`), `JIRA_BASE_URL`, and on Atlassian Cloud `JIRA_EMAIL`. **Do not commit `.env`.**

2. Run the validator (writes under `output/`):

   ```bash
   python3 jira_feature_validator.py --target-version 5.0.0
   ```

3. For label sync + PM report + optional RICE from NotebookLM:

   ```bash
   python3 rox_target_version_labels_pm_validation.py --target-version 5.0.0 --dry-run
   ```

## Automation

Use `cron` or another scheduler to run the same commands on a host that has network access and credentials. Example:

```bash
0 9 * * * cd /path/to/aifeaturepriortization && /usr/bin/python3 jira_feature_validator.py --target-version 5.0.0
```

## Security

- Never commit `.env`, API tokens, offline tokens, or Snowflake passwords.
- Prefer least-privilege Jira tokens and rotate on schedule.
- See [.env.example](.env.example) for variable names only (placeholders, no real secrets).

## Further reading

- [docs/README.md](docs/README.md) — index of all script guides.

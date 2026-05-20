# `rox_target_version_labels_pm_validation.py`

## Role

For all **ROX Features** with a given **Target Version** (default `5.0.0`):

1. Ensures the **version label** (for example `5.0.0`) exists on each issue (optional live updates; use `--dry-run` to skip label writes).
2. Writes CSV reports: full snapshot with labels, PM, template compliance (same rules as `jira_feature_validator.py`), and a CSV of issues **missing Product Manager**.
3. Optional **`--notebooklm-rice`**: prompts NotebookLM (via `nlm` or `notebooklm-py`) for Reach / Impact / Confidence / Effort per feature and merges results.

## Prerequisites

- Jira env vars as in `jira_feature_validator.py`.
- For NotebookLM RICE: `nlm` + `nlm login`, or `notebooklm-py` + `notebooklm login`; optional env vars documented in `.env.example` (`NOTEBOOKLM_*`, `NOTEBOOKLM_RICE_*`).

## Common commands

```bash
python3 rox_target_version_labels_pm_validation.py
python3 rox_target_version_labels_pm_validation.py --target-version 5.0.0
python3 rox_target_version_labels_pm_validation.py --dry-run
python3 rox_target_version_labels_pm_validation.py --notebooklm-rice
```

Run `python3 rox_target_version_labels_pm_validation.py --help` for token overrides, RICE options, and output paths.

### NotebookLM “can’t find” an issue (e.g. ROX-26429)

NotebookLM only **indexes uploaded sources**. If the RICE prompt listed **keys only**, issues missing from the latest export could not be resolved. The script now sends **each key with its Jira summary** by default (`NOTEBOOKLM_RICE_SUMMARY_MAX`, default `500`). For even more context, run with **`--rice-jira-context`** or re-upload a fresh feature CSV to the notebook via `rox_feature_export_notebooklm.py`.

See also [README_DAILY_VALIDATION.md](../../README_DAILY_VALIDATION.md) for how this fits a validation workflow.

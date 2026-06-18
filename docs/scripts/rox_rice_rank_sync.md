# rox_rice_rank_sync.py

Syncs Jira Rank (LexoRank) for ROX Features from RICE Score via the Jira Software Rank API. Higher RICE Score = higher on the backlog. Equal RICE Score = higher Reach ranks higher.

## CLI parameters

| Flag | Default | Env override | Description |
|------|---------|-------------|-------------|
| `--target-version` | `5.0.0` | — | Target Version filter |
| `--cohort` | `target-version` | `JIRA_RANK_COHORT` | Which issues: `target-version`, `label`, or `both` |
| `--dry-run` | `False` | — | Show planned rank order only |
| `--apply` | `False` | — | Apply rank updates via Jira Rank API |
| `--state-file` | `.rox_rice_rank_state.json` | — | JSON state for manual-override tracking |
| `--manual-label` | `rice-rank-manual` | `JIRA_RANK_MANUAL_LABEL` | Jira label that locks rank |
| `--clear-manual` | — | — | Clear manual override for key(s) before run (repeatable) |
| `--force-rank` | `False` | — | Ignore auto-detected state locks (not labels) |
| `--force-all` | `False` | — | Rank every issue ignoring all locks (dangerous) |
| `--only-if-changed` | `False` | — | Skip Rank API if no RICE changes and already in order |
| `--change-report` | Auto with `--only-if-changed` | — | Write CSV of RICE score changes |
| `--rank-view` | `asc` | `JIRA_RANK_VIEW` | `asc` (JPD global plan) or `desc` (Rank DESC boards) |
| `--plan-csv` | — | — | Jira plan export CSV; rank only listed keys |
| `--jira-url` | `https://redhat.atlassian.net` | `JIRA_BASE_URL` | Jira Cloud URL |
| `--email` | — | `JIRA_EMAIL` | Atlassian email |
| `--token` | — | `JIRA_TOKEN` / `JIRA_API_TOKEN` | API token |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_TOKEN` / `JIRA_API_TOKEN` | Yes | API token |
| `JIRA_BASE_URL` | Yes | Must be Jira Cloud (atlassian.net) |
| `JIRA_EMAIL` | Yes (Cloud) | Atlassian account email |
| `JIRA_RICE_SCORE_FIELD` | No | Auto-discovered from Jira fields API |
| `JIRA_RANK_FIELD` | No | Auto-discovered |
| `JIRA_RANK_MANUAL_LABEL` | No | Default: `rice-rank-manual` |
| `JIRA_RANK_COHORT` | No | Default: `target-version` |
| `JIRA_RANK_VIEW` | No | Default: `asc` |
| `JIRA_RANK_TARGET_VERSION` | No | Used by `run_daily_rank_sync.sh` |

## Data flow

1. Loads state from `.rox_rice_rank_state.json`
2. Fetches all features in the cohort (Target Version, label, or both)
3. Detects RICE score changes since last run; unlocks state-locked issues when RICE changes
4. Detects manual rank overrides (rank changed in Jira while RICE unchanged)
5. With `--only-if-changed`: skips apply if no RICE changes and no misalignment
6. Applies rank via Jira Software Rank API (`/rest/agile/1.0/issue/rank`)
7. Uses segment ranking around `rice-rank-manual` anchors to preserve their position
8. Saves updated state (RICE scores, LexoRank values, lock flags)
9. Writes change report CSV to `output/`

## Dependencies

- `jira_auth.py` — token and Cloud detection
- `jira_feature_validator.py` — `JiraFeatureValidator` class (connection, field discovery)

## Automation

- `run_daily_rank_sync.sh` — runs `--apply --only-if-changed --force-rank`
- `com.rox.daily-rank-sync.plist` — macOS launchd at 8:30 AM
- Logs: `output/daily_rank_sync_YYYYMMDD.log`

## State file

`.rox_rice_rank_state.json` — tracks per-issue RICE scores, LexoRank values, manual override flags, and timestamps. Do not commit.

# `rox_rice_rank_sync.py`

## Role

Orders **ROX** **Feature** issues by **RICE Score** on Jira Cloud so the **#** column in Jira Product Discovery matches priority.

- Default **rank view: `asc`** — matches the **ACS Global Plan 5.0.0** export (`ORDER BY Rank ASC`; row #1 = top of plan).
- Default cohort: **Target Version** field only.
- Use **`--plan-csv`** to rank exactly the issues in a plan export (76 rows with label `5.0.0`, etc.).

## Prerequisites

- Jira Cloud (`*.atlassian.net`) with Jira Software (Rank field).
- `.env`: `JIRA_TOKEN`, `JIRA_EMAIL`, `JIRA_BASE_URL`.
- Optional: `JIRA_RANK_VIEW` (`asc` | `desc`), `JIRA_RANK_COHORT`, `JIRA_RICE_SCORE_FIELD`, `JIRA_RANK_FIELD`.

## Commands

```bash
# Target Version cohort only (73 issues)
python3 rox_rice_rank_sync.py --apply --target-version 5.0.0

# Global plan export (all keys in CSV — recommended for JPD 5.0.0 plan view)
python3 rox_rice_rank_sync.py --apply \
  --plan-csv ~/Downloads/plans_acs_global_plan_5_0_0_19052026.csv \
  --force-rank
```

Preview:

```bash
python3 rox_rice_rank_sync.py --dry-run --plan-csv ~/Downloads/plans_acs_global_plan_5_0_0_19052026.csv
```

## How ranking works (`rank-view=asc`)

1. **Scored issues** (including RICE 0): chained `rankAfterIssue` from the plan tail in **RICE descending** order → highest RICE at row **#1**.
2. **No RICE:** each issue moved `rankAfterIssue` a refreshed plan tail so they stay at the **bottom**.

Refresh the plan view sorted by **Rank ascending** (not the RICE column) to verify **#** vs RICE.

## Manual override

- Label `rice-rank-manual`, or auto-detect when Rank changed but RICE did not.
- Clear: `python3 rox_rice_rank_sync.py --clear-manual ROX-31439 --apply`

## State file

`.rox_rice_rank_state.json` — do not commit if it contains environment-specific data.

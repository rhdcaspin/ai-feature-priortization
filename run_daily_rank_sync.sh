#!/bin/bash
# Daily ROX backlog rank sync from RICE Score (respects rice-rank-manual anchors).
# Safe for automation: reports RICE changes, skips apply when nothing changed.
#
# Install launchd (macOS): see com.rox.daily-rank-sync.plist
# Manual: ./run_daily_rank_sync.sh

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p output

export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"

TARGET_VERSION="${JIRA_RANK_TARGET_VERSION:-5.0.0}"
LOG="output/daily_rank_sync_$(date +%Y%m%d).log"

{
  echo "=== $(date) - RICE rank sync (target ${TARGET_VERSION}) ==="
  /usr/bin/env python3 rox_rice_rank_sync.py \
    --apply \
    --target-version "${TARGET_VERSION}" \
    --only-if-changed --force-rank
  echo "=== $(date) - Done (exit $?) ==="
} 2>&1 | tee -a "${LOG}"

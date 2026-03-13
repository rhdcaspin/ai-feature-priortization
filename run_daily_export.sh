#!/bin/bash
# Daily ROX feature + RHACS telemetry export
# Runs both exports and uploads to NotebookLM
# Used by launchd job

cd "$(dirname "$0")"
mkdir -p output

# Ensure PATH includes common Python/CLI locations
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"

echo "=== $(date) - Starting daily exports ==="

# 1. ROX feature export (Jira -> NotebookLM)
echo "--- ROX Feature Export ---"
/usr/bin/env python3 rox_feature_export_notebooklm.py
ROX_EXIT=$?
if [ $ROX_EXIT -ne 0 ]; then
    echo "WARNING: ROX feature export failed (exit code $ROX_EXIT)"
fi

# 2. RHACS telemetry export (Snowflake -> NotebookLM)
# Requires SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER in .env
echo "--- RHACS Telemetry Export ---"
/usr/bin/env python3 rhacs_telemetry_export.py
RHACS_EXIT=$?
if [ $RHACS_EXIT -ne 0 ]; then
    echo "WARNING: RHACS telemetry export failed (exit code $RHACS_EXIT)"
fi

# 3. RHACS RFE export (Jira RFE project -> NotebookLM)
echo "--- RHACS RFE Export ---"
/usr/bin/env python3 rfe_export.py
RFE_EXIT=$?
if [ $RFE_EXIT -ne 0 ]; then
    echo "WARNING: RHACS RFE export failed (exit code $RFE_EXIT)"
fi

echo "=== $(date) - Daily exports complete (ROX=$ROX_EXIT, RHACS=$RHACS_EXIT, RFE=$RFE_EXIT) ==="

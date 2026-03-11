#!/bin/bash
# Daily ROX feature export - runs diff export and uploads to NotebookLM
# Used by launchd job

cd "$(dirname "$0")"
mkdir -p output

# Ensure PATH includes common Python/CLI locations
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"

# Run the export (features updated since last run) and upload to NotebookLM
exec /usr/bin/env python3 rox_feature_export_notebooklm.py

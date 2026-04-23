# Daily ROX Feature Export

Automated daily process that exports Jira features updated since the last run and uploads the CSV to NotebookLM ("ACS RICE Scoring and Prioritization Framework").

## Setup (one-time)

1. **Edit paths in `com.rox.daily-export.plist`** so they match your machine: `run_daily_export.sh` location, repo `WorkingDirectory`, and log file paths (or keep logs under `~/Library/Logs/` as in the template).

2. **Copy (not symlink) the plist to LaunchAgents:**
   ```bash
   cp /path/to/aifeaturepriortization/com.rox.daily-export.plist ~/Library/LaunchAgents/
   chmod 644 ~/Library/LaunchAgents/com.rox.daily-export.plist
   ```

3. **Load and enable** (pick one that works on your macOS):
   ```bash
   # Newer macOS (Sonoma+)
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rox.daily-export.plist

   # Older macOS
   launchctl load ~/Library/LaunchAgents/com.rox.daily-export.plist
   ```

4. **If you get "Load failed: 5"** — unload first, then reload:
   ```bash
   launchctl bootout gui/$(id -u) com.rox.daily-export 2>/dev/null  # or: launchctl unload ...
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rox.daily-export.plist
   ```

## Schedule

Runs **daily at 9:00 AM** (your machine must be on and awake).

## Logs

- `~/Library/Logs/rox-daily-export.log` — script output
- `~/Library/Logs/rox-daily-export.err.log` — errors

## Useful commands

```bash
# Check if job is loaded
launchctl print gui/$(id -u)/com.rox.daily-export

# Unload (disable) the job
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.rox.daily-export.plist

# Run manually to test
./run_daily_export.sh
```

## Changing the schedule

Edit `com.rox.daily-export.plist` and change the `StartCalendarInterval`:

```xml
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key>
    <integer>9</integer>   <!-- 0-23 -->
    <key>Minute</key>
    <integer>0</integer>   <!-- 0-59 -->
</dict>
```

Then reload: `launchctl unload ... && launchctl load ...`

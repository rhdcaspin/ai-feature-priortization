# ROX Bugs and RFE Fetcher

This script uses a **CIPOE-first approach** to fetch ROX bugs and RFE requests from Red Hat Jira (https://issues.redhat.com). Instead of searching ROX/RFE projects directly, it queries CIPOE issues first and then finds related ROX bugs and RFE requests through issue links.

## 🔄 **New CIPOE-First Approach**

**Why this approach is better:**
- More efficient: Query CIPOE project first, then find linked issues
- More accurate: Only finds issues actually linked to CIPOE
- Better performance: Fewer API calls, more targeted results
- Logical flow: Start with customer impact, find related technical issues

## Features

- ✅ **CIPOE-first querying**: Start with CIPOE issues, find linked ROX/RFE issues
- ✅ **Issue link traversal**: Follow issue links to find related items
- ✅ **Impacts account filtering**: Filter by "impacts account" issue links
- ✅ Multiple output formats (console, CSV, JSON)
- ✅ Bearer token authentication for Red Hat Jira
- ✅ Comprehensive debugging mode
- ✅ Issue links parsing and analysis
- ✅ Error handling and connection testing

## Prerequisites

1. **Python 3.6+** with `requests` library
2. **Red Hat Jira API Token** - Get yours from: https://issues.redhat.com/secure/ViewProfile.jspa?selectedTab=com.atlassian.pats.pats-plugin:jira-user-personal-access-tokens
3. **Access permissions** to ROX and RFE projects in Red Hat Jira

## Installation

```bash
# Install required dependencies
pip install requests

# Make script executable (optional)
chmod +x rox_rfe_fetcher.py
```

## Usage

### Basic Usage

```bash
# Set your Jira token as environment variable
export JIRA_TOKEN="your_api_token_here"

# Fetch ROX/RFE issues linked to a specific CIPOE issue
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002

# Fetch ROX/RFE issues linked to any CIPOE issue (not recommended - too broad)
python rox_rfe_fetcher.py --cipoe-project CIPOE
```

### Filter by "Impacts Account" Links

```bash
# Only fetch issues that have "impacts account" issue links
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --impacts-account-only
```

### Export to Files

```bash
# Export to CSV
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --output csv

# Export to JSON
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --output json

# Export to all formats (console + CSV + JSON)
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --output all
```

### Advanced Usage

```bash
# Filter by impacts account links and export to CSV
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --impacts-account-only --output csv

# Debug mode to troubleshoot issues
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --debug

# Include specific issues regardless of linking
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --include-issues ROX-30293 --output csv

# Use custom Jira URL and token
python rox_rfe_fetcher.py --jira-url https://issues.redhat.com --token YOUR_TOKEN --cipoe-project CIPOE-129002
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--jira-url` | Jira base URL | `https://issues.redhat.com` |
| `--token` | Jira API token | From `JIRA_TOKEN` env var |
| `--cipoe-project` | **REQUIRED** CIPOE project key (e.g., CIPOE-129002 or CIPOE) | None |
| `--impacts-account-only` | Only fetch issues with "impacts account" links | False |
| `--output` | Output format: `console`, `csv`, `json`, `all` | `console` |
| `--debug` | Enable debug mode for troubleshooting | False |

## Output Formats

### Console Output
Displays issues in a formatted, human-readable format with key details.

### CSV Export
Creates a CSV file with columns:
- Key, Summary, Type, Status, Priority, Project, Assignee, CIPOE Project, Impacts Account, Created, Updated

### JSON Export
Creates a structured JSON file with separate arrays for ROX bugs and RFE requests.

## How the CIPOE-First Approach Works

### Step 1: Query CIPOE Project
- If you specify `--cipoe-project CIPOE-123`, it fetches that specific CIPOE issue
- If you specify `--cipoe-project CIPOE`, it fetches all open CIPOE issues

### Step 2: Find Linked Issues
- For each CIPOE issue found, the script examines its issue links
- It looks for links to ROX and RFE projects
- It fetches the full details of linked ROX bugs and RFE requests

### Step 3: Filter by Link Type (Optional)
- If `--impacts-account-only` is specified, only issues with "impacts account" links are included
- The script checks link types like "impacts account", "impact account", "affects account", etc.

## "Impacts Account" Link Filtering

The script can filter issues based on their issue links, specifically looking for links of type "impacts account" (or similar variations like "impact account", "affects account", "account impact").

When using `--impacts-account-only`:
1. The script first tries to use JQL filtering with `linkedIssuesOfRecursive()` function
2. If that fails or isn't supported, it fetches all issues and filters them locally
3. It checks each issue's links for the "impacts account" link type
4. Only issues with this link type are included in the results

This is particularly useful for finding ROX bugs and RFE requests that directly impact customer accounts.

## Examples

### Example 1: Basic CIPOE-First Fetch
```bash
export JIRA_TOKEN="your_token"
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002
```

Output:
```
🚀 Starting ROX Bugs and RFE Fetcher (CIPOE-first approach)
============================================================
✅ Connected to Jira as: Doron Caspin

📋 Step 1: Fetching CIPOE issues for CIPOE-129002
🔍 Fetching CIPOE issue: CIPOE-129002
📊 Found 1 issues
✅ Found 1 CIPOE issue(s)

🔗 Step 2: Finding linked ROX bugs and RFE requests

ROX OPEN BUGS (2 issues):
============================================================
🎫 ROX-30293: Compliance Operator V1.7.0 showing difference status on CLI
   Type: Bug | Status: Backlog | Priority: Normal
   Assignee: Unassigned
   Created: 2025-07-25 | Updated: 2025-09-13

🎫 ROX-31264: Message sent from RHACS to syslog is incomplete
   Type: Bug | Status: New | Priority: Major
   Assignee: Van Wilson
   Components: RHACS, syslog integration
   Created: 2025-10-14 | Updated: 2025-10-16
...
```

### Example 2: Export to CSV
```bash
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --output csv
```

This will:
1. Fetch CIPOE-129002 issue and its linked ROX/RFE issues
2. Apply filtering (non-closed ROX bugs, rhacs RFEs)
3. Export results to a timestamped CSV file like `rox_rfe_issues_20251016_113836.csv`

### Example 3: Include Specific Issues
```bash
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --include-issues ROX-30293 --output csv
```

This will:
1. Fetch linked issues from CIPOE-129002
2. Additionally include ROX-30293 even if not linked
3. Export all results to CSV format

### Example 4: Filter by "Impacts Account" Links
```bash
python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --impacts-account-only --output json
```

This will:
1. Fetch issues linked to CIPOE-129002
2. Filter only those with "impacts account" issue links
3. Export results to a timestamped JSON file

## Error Handling

The script includes comprehensive error handling:
- Connection testing before fetching
- API version fallback (tries v2 then v3)
- Graceful handling of missing fields
- Detailed error messages with HTTP status codes

## Troubleshooting

### Authentication Issues
```
❌ Failed to connect to Jira: 401 Unauthorized
```
- Verify your API token is correct
- Check that the token hasn't expired
- Ensure you have access to ROX and RFE projects

### No Issues Found
```
📊 Found 0 issues
```
- Check if you have read permissions for the projects
- Verify the project keys (ROX, RFE) exist in your Jira instance
- Try without CIPOE filtering to see if issues exist

### Custom Field Issues
If CIPOE project filtering isn't working well, the script tries multiple approaches:
1. Looks in custom fields (field ID may need adjustment)
2. Searches description and summary text
3. Uses case-insensitive matching

## Integration

This script can be easily integrated into:
- CI/CD pipelines for automated reporting
- Cron jobs for regular issue monitoring
- Other Python applications via import
- Shell scripts for batch processing

## Security Notes

- Store API tokens securely (use environment variables)
- Don't commit tokens to version control
- Consider using token rotation policies
- Limit token permissions to read-only if possible

# Daily ROX Feature Validation

This guide explains how to set up and run daily validation for ROX 4.10 features, including automatic template compliance comments.

## 🚀 Quick Start

### 1. Set up Environment Variables

```bash
# Set your Jira API token
export JIRA_TOKEN="your_jira_token_here"

# Or create a .env file
echo "JIRA_TOKEN=your_jira_token_here" > .env
```

### 2. Test the Setup

```bash
# Dry run to see what comments would be added
python3 daily_validation.py --dry-run

# Generate report without adding comments
python3 daily_validation.py --skip-comments
```

### 3. Run Daily Validation

```bash
# Full validation (generates report + adds comments)
python3 daily_validation.py
```

## 📋 Available Scripts

### Main Scripts

1. **`rox_csv_generator.py`** - Main analysis script with comment functionality
2. **`daily_validation.py`** - Simplified daily runner script  
3. **`add_template_comments.py`** - Standalone comment addition script

### Command Options

#### rox_csv_generator.py
```bash
# Generate report with AI analysis
python3 rox_csv_generator.py

# Add template compliance comments  
python3 rox_csv_generator.py --add-comments

# Dry run for comments
python3 rox_csv_generator.py --dry-run-comments

# Clear LLM cache
python3 rox_csv_generator.py --clear-cache

# Show cache stats
python3 rox_csv_generator.py --cache-stats
```

#### daily_validation.py
```bash
# Full daily validation
python3 daily_validation.py

# Dry run (see what would happen)
python3 daily_validation.py --dry-run

# Report only, no comments
python3 daily_validation.py --skip-comments
```

## 🔄 Automation Setup

### Cron Job (Linux/Mac)

```bash
# Edit crontab
crontab -e

# Add daily run at 9:00 AM
0 9 * * * cd /path/to/aifeaturepriortization && /usr/bin/python3 daily_validation.py

# Add weekly dry run on Fridays at 5 PM
0 17 * * 5 cd /path/to/aifeaturepriortization && /usr/bin/python3 daily_validation.py --dry-run
```

### Windows Task Scheduler

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger to Daily at 9:00 AM
4. Set action to start program: `python3`
5. Add arguments: `daily_validation.py`
6. Set start in: `C:\path\to\aifeaturepriortization`

## 📝 Comment Functionality

### What Comments Are Added

The system automatically adds comments to Jira features that are missing required template sections:

- **Goal Summary**
- **Goals and expected user outcomes**  
- **Acceptance Criteria**
- **Success Criteria or KPIs measured**

### Comment Format

```
🚨 Template Compliance Issue

Hello @ProductManagerName,

This feature is missing the following required template sections:
• Goal Summary
• Goals and expected user outcomes  
• Acceptance Criteria
• Success Criteria or KPIs measured

Action Required:
Please update the feature description to include all required template sections.
```

### Comment Targeting

- **Product Manager Mentions**: Comments tag the assigned Product Manager
- **Account ID Resolution**: Proper @mentions using Jira account IDs
- **Fallback Handling**: Graceful fallback for unassigned features

## 🛠 Troubleshooting

### Common Issues

1. **"JIRA_TOKEN not set"**
   ```bash
   export JIRA_TOKEN="your_token_here"
   ```

2. **"Cannot connect to Jira"**
   - Check network connectivity
   - Verify token permissions
   - Check Jira URL

3. **"Ollama not available"**
   - Ensure Ollama is running: `ollama serve`
   - Check model availability: `ollama list`

4. **Permission Errors**
   ```bash
   chmod +x *.py
   ```

### Debug Mode

Run with Python verbose mode to see detailed output:
```bash
python3 -v daily_validation.py --dry-run
```

## 📊 Output Files

- **CSV Reports**: `rox_4_10_features_YYYYMMDD_HHMMSS.csv`
- **LLM Cache**: `llm_cache/` directory
- **Logs**: Console output (redirect to file if needed)

## 🔒 Security Notes

- **Never commit** `.env` files or tokens to git
- **Use environment variables** for sensitive data
- **Rotate tokens** regularly according to your organization's policy
- **Limit token permissions** to minimum required scope

## 🆘 Support

For issues or questions:
1. Check the troubleshooting section above
2. Review the console output for error messages
3. Verify environment setup and permissions
4. Test with `--dry-run` first

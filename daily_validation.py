#!/usr/bin/env python3
"""
Daily ROX Feature Validation Script

This script runs the daily validation process:
1. Generates feature analysis report with AI validation
2. Adds template compliance comments to incomplete features
3. Can be run as a cron job or scheduled task

Author: AI Assistant  
Date: September 2024
"""

import os
import sys
import subprocess
import argparse
from datetime import datetime


def run_daily_validation(dry_run: bool = False, skip_comments: bool = False):
    """
    Run the daily ROX feature validation process
    
    Args:
        dry_run: If True, only show what comments would be added
        skip_comments: If True, skip adding comments entirely
    """
    
    print("🌅 Starting Daily ROX Feature Validation")
    print("=" * 50)
    print(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔧 Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()
    
    # Check environment
    jira_token = os.getenv('JIRA_TOKEN')
    if not jira_token:
        print("❌ Error: JIRA_TOKEN environment variable not set")
        print("   Please set your Jira API token in the environment")
        return 1
    
    try:
        # Build command
        script_dir = os.path.dirname(os.path.abspath(__file__))
        rox_script = os.path.join(script_dir, 'rox_csv_generator.py')
        
        cmd = [
            sys.executable, rox_script,
            '--jira-url', 'https://issues.redhat.com',
            '--ollama-url', 'http://localhost:11434',
            '--ollama-model', 'LLama3.1:8b'
        ]
        
        # Add comment options
        if not skip_comments:
            if dry_run:
                cmd.append('--dry-run-comments')
            else:
                cmd.append('--add-comments')
        
        print(f"🚀 Running: {' '.join(cmd)}")
        print()
        
        # Execute the validation
        result = subprocess.run(cmd, capture_output=False, text=True)
        
        if result.returncode == 0:
            print("\n" + "=" * 50)
            print("✅ Daily validation completed successfully!")
            
            if not skip_comments:
                if dry_run:
                    print("🔍 Template comment dry run completed")
                    print("   To actually add comments, run without --dry-run")
                else:
                    print("📝 Template compliance comments added to incomplete features")
            else:
                print("📊 Analysis report generated (comments skipped)")
                
        else:
            print("\n" + "=" * 50)
            print(f"❌ Daily validation failed with exit code: {result.returncode}")
            return result.returncode
        
        return 0
        
    except KeyboardInterrupt:
        print("\n🛑 Daily validation interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ Error during daily validation: {e}")
        return 1


def main():
    """Main function with command line argument parsing"""
    parser = argparse.ArgumentParser(
        description='Daily ROX Feature Validation - Generates reports and adds template compliance comments',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full daily validation (generate report + add comments)
  python3 daily_validation.py
  
  # Dry run to see what comments would be added
  python3 daily_validation.py --dry-run
  
  # Generate report only, skip comments
  python3 daily_validation.py --skip-comments
  
  # Set up as daily cron job (runs at 9 AM daily)
  # 0 9 * * * cd /path/to/project && python3 daily_validation.py
        """
    )
    
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what template comments would be added without actually adding them')
    parser.add_argument('--skip-comments', action='store_true',
                        help='Generate analysis report only, skip adding comments')
    
    args = parser.parse_args()
    
    if args.dry_run and args.skip_comments:
        print("❌ Error: --dry-run and --skip-comments are mutually exclusive")
        return 1
    
    return run_daily_validation(
        dry_run=args.dry_run,
        skip_comments=args.skip_comments
    )


if __name__ == "__main__":
    exit(main())

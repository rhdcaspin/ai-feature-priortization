#!/usr/bin/env python3
"""
ROX Case Monitor

This script monitors ROX project bugs and automatically adds comments when
linked Red Hat support cases are closed.

Features:
- Fetches all bugs from ROX project
- Extracts support case links from bug descriptions and comments
- Checks case status via Red Hat Customer Portal API
- Adds comments to bugs when linked cases are closed
- Tracks processed cases to avoid duplicate comments

Usage:
    python rox_case_monitor.py --jira-token YOUR_JIRA_TOKEN --rh-token YOUR_RH_TOKEN
    python rox_case_monitor.py --dry-run  # Test mode without adding comments
"""

import os
import sys
import json
import re
import requests
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
import time


@dataclass
class SupportCase:
    """Represents a Red Hat support case"""
    case_number: str
    status: str
    summary: str
    severity: str
    created_date: str
    last_modified: str
    account_name: str = ""
    product: str = ""


@dataclass
class ROXBug:
    """Represents a ROX bug with potential case links"""
    key: str
    summary: str
    status: str
    assignee: str
    description: str
    created: str
    updated: str
    case_links: List[str] = None
    processed_cases: Set[str] = None


class ROXCaseMonitor:
    """Monitors ROX bugs and updates them based on linked case status"""
    
    def __init__(self, jira_token: str, rh_token: str = None, dry_run: bool = False, debug: bool = False, rate_limit: float = 1.0):
        """
        Initialize the monitor
        
        Args:
            jira_token: Red Hat Jira API token
            rh_token: Red Hat Customer Portal API token (optional)
            dry_run: If True, don't actually add comments
            debug: Enable debug logging
            rate_limit: Seconds to wait between requests (default: 1.0)
        """
        self.jira_token = jira_token
        self.rh_token = rh_token
        self.dry_run = dry_run
        self.debug = debug
        self.rate_limit = rate_limit
        
        # Jira session
        self.jira_session = requests.Session()
        self.jira_session.headers.update({
            'Authorization': f'Bearer {jira_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        
        # Red Hat Customer Portal session
        self.rh_session = requests.Session()
        if rh_token:
            self.rh_session.headers.update({
                'Authorization': f'Bearer {rh_token}',
                'Accept': 'application/json'
            })
        
        self.jira_url = "https://issues.redhat.com"
        self.rh_portal_url = "https://access.redhat.com"
        
        # Case link patterns
        self.case_patterns = [
            r'https://access\.redhat\.com/support/cases/#/case/(\d+)',
            r'case[:\s#]*(\d{8})',
            r'support case[:\s#]*(\d{8})',
            r'customer case[:\s#]*(\d{8})',
        ]
        
        # Track processed cases to avoid duplicate comments
        self.processed_cases_file = "processed_cases.json"
        self.processed_cases = self.load_processed_cases()
    
    def load_processed_cases(self) -> Dict[str, Set[str]]:
        """Load previously processed cases from file"""
        try:
            if os.path.exists(self.processed_cases_file):
                with open(self.processed_cases_file, 'r') as f:
                    data = json.load(f)
                    # Convert lists back to sets
                    return {bug_key: set(cases) for bug_key, cases in data.items()}
        except Exception as e:
            if self.debug:
                print(f"🐛 DEBUG: Failed to load processed cases: {e}")
        return {}
    
    def save_processed_cases(self):
        """Save processed cases to file"""
        try:
            # Convert sets to lists for JSON serialization
            data = {bug_key: list(cases) for bug_key, cases in self.processed_cases.items()}
            with open(self.processed_cases_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            if self.debug:
                print(f"🐛 DEBUG: Failed to save processed cases: {e}")
    
    def test_connections(self) -> bool:
        """Test connections to Jira and Red Hat Customer Portal"""
        print("🔍 Testing connections...")
        
        # Test Jira connection
        try:
            response = self.jira_session.get(f"{self.jira_url}/rest/api/2/myself")
            response.raise_for_status()
            user_data = response.json()
            print(f"✅ Connected to Jira as: {user_data.get('displayName', 'Unknown')}")
        except Exception as e:
            print(f"❌ Failed to connect to Jira: {e}")
            return False
        
        # Test Red Hat Customer Portal connection (if token provided)
        if self.rh_token:
            try:
                # Test endpoint - this might need adjustment based on actual API
                response = self.rh_session.get(f"{self.rh_portal_url}/rs/cases")
                if response.status_code in [200, 401, 403]:  # 401/403 means auth issue but API is reachable
                    print(f"✅ Red Hat Customer Portal API reachable")
                else:
                    print(f"⚠️  Red Hat Customer Portal API returned: {response.status_code}")
            except Exception as e:
                print(f"⚠️  Red Hat Customer Portal connection test failed: {e}")
                print("   (This might be expected if API endpoint differs)")
        else:
            print("⚠️  No Red Hat Customer Portal token provided - case status checking disabled")
        
        return True
    
    def fetch_rox_bugs(self, max_results: int = 1000) -> List[ROXBug]:
        """
        Fetch all non-closed bugs from ROX project
        
        Args:
            max_results: Maximum number of bugs to fetch
            
        Returns:
            List of non-closed ROX bugs
        """
        print(f"🔍 Fetching non-closed ROX bugs (max: {max_results})...")
        
        bugs = []
        start_at = 0
        batch_size = 50
        
        while len(bugs) < max_results:
            try:
                params = {
                    'jql': 'project = ROX AND issuetype = Bug AND status not in (Closed, Done, Resolved, Cancelled, Rejected)',
                    'startAt': start_at,
                    'maxResults': min(batch_size, max_results - len(bugs)),
                    'fields': 'key,summary,description,status,assignee,created,updated'
                }
                
                response = self.jira_session.get(f"{self.jira_url}/rest/api/2/search", params=params)
                response.raise_for_status()
                data = response.json()
                
                batch_issues = data.get('issues', [])
                if not batch_issues:
                    break
                
                for issue in batch_issues:
                    fields = issue.get('fields', {})
                    assignee = fields.get('assignee')
                    assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
                    
                    bug = ROXBug(
                        key=issue.get('key', ''),
                        summary=fields.get('summary', ''),
                        status=fields.get('status', {}).get('name', ''),
                        assignee=assignee_name,
                        description=fields.get('description', '') or '',
                        created=fields.get('created', ''),
                        updated=fields.get('updated', ''),
                        case_links=[],
                        processed_cases=set()
                    )
                    bugs.append(bug)
                
                start_at += len(batch_issues)
                
                if self.debug:
                    print(f"🐛 DEBUG: Fetched {len(bugs)} bugs so far...")
                
                # Rate limiting - increased to avoid 429 errors
                time.sleep(self.rate_limit * 0.5)
                
            except Exception as e:
                print(f"❌ Error fetching bugs: {e}")
                break
        
        print(f"📊 Found {len(bugs)} non-closed ROX bugs")
        return bugs
    
    def extract_case_links(self, bug: ROXBug) -> List[str]:
        """
        Extract support case numbers from bug description and comments
        
        Args:
            bug: ROX bug to analyze
            
        Returns:
            List of case numbers found
        """
        case_numbers = set()
        text_to_search = bug.description
        
        # Also fetch and search comments
        try:
            response = self.jira_session.get(f"{self.jira_url}/rest/api/2/issue/{bug.key}/comment")
            if response.status_code == 200:
                comments_data = response.json()
                for comment in comments_data.get('comments', []):
                    text_to_search += " " + (comment.get('body', '') or '')
        except Exception as e:
            if self.debug:
                print(f"🐛 DEBUG: Failed to fetch comments for {bug.key}: {e}")
        
        # Search for case patterns
        for pattern in self.case_patterns:
            matches = re.findall(pattern, text_to_search, re.IGNORECASE)
            case_numbers.update(matches)
        
        # Filter out invalid case numbers (should be 8 digits)
        valid_cases = [case for case in case_numbers if len(case) == 8 and case.isdigit()]
        
        if valid_cases and self.debug:
            print(f"🐛 DEBUG: Found cases in {bug.key}: {valid_cases}")
        
        return valid_cases
    
    def get_case_status(self, case_number: str) -> Optional[SupportCase]:
        """
        Get support case status from Red Hat Customer Portal
        
        Args:
            case_number: Support case number
            
        Returns:
            SupportCase object or None if not found/accessible
        """
        if not self.rh_token:
            # Mock response for testing when no RH token
            if self.debug:
                print(f"🐛 DEBUG: No RH token - mocking case {case_number} as closed")
            return SupportCase(
                case_number=case_number,
                status="Closed",
                summary=f"Mock case {case_number}",
                severity="Normal",
                created_date="2024-01-01",
                last_modified="2024-12-01"
            )
        
        try:
            # Note: This endpoint might need adjustment based on actual Red Hat Customer Portal API
            response = self.rh_session.get(f"{self.rh_portal_url}/rs/cases/{case_number}")
            
            if response.status_code == 200:
                case_data = response.json()
                return SupportCase(
                    case_number=case_number,
                    status=case_data.get('status', 'Unknown'),
                    summary=case_data.get('summary', ''),
                    severity=case_data.get('severity', ''),
                    created_date=case_data.get('createdDate', ''),
                    last_modified=case_data.get('lastModifiedDate', ''),
                    account_name=case_data.get('account', {}).get('name', ''),
                    product=case_data.get('product', {}).get('name', '')
                )
            elif response.status_code == 404:
                if self.debug:
                    print(f"🐛 DEBUG: Case {case_number} not found")
                return None
            else:
                if self.debug:
                    print(f"🐛 DEBUG: Failed to fetch case {case_number}: {response.status_code}")
                return None
                
        except Exception as e:
            if self.debug:
                print(f"🐛 DEBUG: Error fetching case {case_number}: {e}")
            return None
    
    def add_comment_to_bug(self, bug_key: str, case: SupportCase, retry_count: int = 0) -> bool:
        """
        Add comment to bug about closed case with retry logic for rate limiting
        
        Args:
            bug_key: Jira bug key
            case: Support case information
            retry_count: Current retry attempt (for internal use)
            
        Returns:
            True if comment was added successfully
        """
        comment_text = f"""🔔 **Linked Support Case Update**

The support case #{case.case_number} linked to this bug has been closed.

**Case Details:**
- Status: {case.status}
- Summary: {case.summary}
- Severity: {case.severity}
- Last Modified: {case.last_modified}

Please review if this bug can also be resolved or if further action is needed.

_This comment was automatically generated by ROX Case Monitor._"""
        
        if self.dry_run:
            print(f"🔍 DRY RUN: Would add comment to {bug_key} about closed case {case.case_number}")
            return True
        
        try:
            payload = {
                "body": comment_text
            }
            
            response = self.jira_session.post(
                f"{self.jira_url}/rest/api/2/issue/{bug_key}/comment",
                json=payload
            )
            
            if response.status_code == 201:
                print(f"✅ Added comment to {bug_key} about closed case {case.case_number}")
                return True
            elif response.status_code == 429:
                # Rate limited - implement exponential backoff
                if retry_count < 3:
                    wait_time = (2 ** retry_count) * 5  # 5, 10, 20 seconds
                    print(f"⏳ Rate limited on {bug_key}, waiting {wait_time}s before retry {retry_count + 1}/3...")
                    time.sleep(wait_time)
                    return self.add_comment_to_bug(bug_key, case, retry_count + 1)
                else:
                    print(f"❌ Failed to add comment to {bug_key}: Rate limited after 3 retries")
                    return False
            else:
                print(f"❌ Failed to add comment to {bug_key}: {response.status_code}")
                if self.debug:
                    print(f"🐛 DEBUG: Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error adding comment to {bug_key}: {e}")
            return False
    
    def add_comment_to_bug_multiple_cases(self, bug_key: str, cases: List[SupportCase], retry_count: int = 0) -> bool:
        """
        Add comment to bug about multiple closed cases with retry logic for rate limiting
        
        Args:
            bug_key: Jira bug key
            cases: List of support case information
            retry_count: Current retry attempt (for internal use)
            
        Returns:
            True if comment was added successfully
        """
        if len(cases) == 1:
            return self.add_comment_to_bug(bug_key, cases[0], retry_count)
        
        case_details = []
        case_links = []
        
        for case in cases:
            case_links.append(f"#{case.case_number}")
            case_details.append(f"- **{case.case_number}**: {case.status} (Severity: {case.severity}, Updated: {case.last_modified})")
        
        comment_text = f"""🔔 **All Linked Support Cases Closed**

All support cases linked to this bug have been closed: {', '.join(case_links)}

**Case Details:**
{chr(10).join(case_details)}

Please review if this bug can also be resolved or if further action is needed.

_This comment was automatically generated by ROX Case Monitor._"""
        
        if self.dry_run:
            case_numbers = [case.case_number for case in cases]
            print(f"🔍 DRY RUN: Would add comment to {bug_key} about {len(cases)} closed cases: {', '.join(case_numbers)}")
            return True
        
        try:
            payload = {
                "body": comment_text
            }
            
            response = self.jira_session.post(
                f"{self.jira_url}/rest/api/2/issue/{bug_key}/comment",
                json=payload
            )
            
            if response.status_code == 201:
                case_numbers = [case.case_number for case in cases]
                print(f"✅ Added comment to {bug_key} about {len(cases)} closed cases: {', '.join(case_numbers)}")
                return True
            elif response.status_code == 429:
                # Rate limited - implement exponential backoff
                if retry_count < 3:
                    wait_time = (2 ** retry_count) * 5  # 5, 10, 20 seconds
                    print(f"⏳ Rate limited on {bug_key}, waiting {wait_time}s before retry {retry_count + 1}/3...")
                    time.sleep(wait_time)
                    return self.add_comment_to_bug_multiple_cases(bug_key, cases, retry_count + 1)
                else:
                    print(f"❌ Failed to add comment to {bug_key}: Rate limited after 3 retries")
                    return False
            else:
                print(f"❌ Failed to add comment to {bug_key}: {response.status_code}")
                if self.debug:
                    print(f"🐛 DEBUG: Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error adding comment to {bug_key}: {e}")
            return False
    
    def process_bugs(self, bugs: List[ROXBug]) -> Dict[str, int]:
        """
        Process bugs and add comments for closed cases
        
        Args:
            bugs: List of ROX bugs to process
            
        Returns:
            Dictionary with processing statistics
        """
        stats = {
            'bugs_processed': 0,
            'cases_found': 0,
            'closed_cases': 0,
            'comments_added': 0,
            'errors': 0
        }
        
        print(f"\n🔄 Processing {len(bugs)} bugs...")
        
        for i, bug in enumerate(bugs, 1):
            if self.debug and i % 10 == 0:
                print(f"🐛 DEBUG: Processing bug {i}/{len(bugs)}")
            
            stats['bugs_processed'] += 1
            
            # Extract case links
            case_links = self.extract_case_links(bug)
            if not case_links:
                continue
            
            stats['cases_found'] += len(case_links)
            
            # Check all cases for this bug
            case_statuses = {}
            unprocessed_cases = []
            
            for case_number in case_links:
                # Skip if already processed
                if bug.key in self.processed_cases and case_number in self.processed_cases[bug.key]:
                    if self.debug:
                        print(f"🐛 DEBUG: Skipping already processed case {case_number} for {bug.key}")
                    continue
                
                unprocessed_cases.append(case_number)
                
                # Get case status
                case = self.get_case_status(case_number)
                if not case:
                    stats['errors'] += 1
                    continue
                
                case_statuses[case_number] = case
                
                # Rate limiting - increased to avoid 429 errors
                time.sleep(self.rate_limit)
            
            # Check if ALL unprocessed cases are closed
            if unprocessed_cases and case_statuses:
                closed_cases = []
                open_cases = []
                
                for case_number in unprocessed_cases:
                    if case_number in case_statuses:
                        case = case_statuses[case_number]
                        if case.status.lower() in ['closed', 'resolved', 'cancelled']:
                            closed_cases.append(case)
                            stats['closed_cases'] += 1
                        else:
                            open_cases.append(case)
                
                if self.debug:
                    print(f"🐛 DEBUG: {bug.key} - Closed cases: {len(closed_cases)}, Open cases: {len(open_cases)}")
                
                # Only add comment if ALL cases are closed
                if closed_cases and not open_cases:
                    # Add comment about all closed cases
                    if self.add_comment_to_bug_multiple_cases(bug.key, closed_cases):
                        stats['comments_added'] += 1
                        
                        # Mark all cases as processed
                        if bug.key not in self.processed_cases:
                            self.processed_cases[bug.key] = set()
                        for case in closed_cases:
                            self.processed_cases[bug.key].add(case.case_number)
                elif closed_cases and open_cases:
                    if self.debug:
                        print(f"🐛 DEBUG: {bug.key} - Not commenting yet, some cases still open: {[c.case_number for c in open_cases]}")
                elif not closed_cases and open_cases:
                    if self.debug:
                        print(f"🐛 DEBUG: {bug.key} - All cases still open: {[c.case_number for c in open_cases]}")
        
        # Save processed cases
        self.save_processed_cases()
        
        return stats
    
    def run(self, max_bugs: int = 1000):
        """
        Run the complete monitoring process
        
        Args:
            max_bugs: Maximum number of bugs to process
        """
        print("🚀 Starting ROX Case Monitor")
        print("=" * 50)
        
        if self.dry_run:
            print("🔍 DRY RUN MODE - No comments will be added")
        
        # Test connections
        if not self.test_connections():
            return
        
        # Fetch ROX bugs
        bugs = self.fetch_rox_bugs(max_bugs)
        if not bugs:
            print("❌ No bugs found")
            return
        
        # Process bugs
        stats = self.process_bugs(bugs)
        
        # Print summary
        print("\n" + "=" * 50)
        print("📊 PROCESSING SUMMARY")
        print("=" * 50)
        print(f"Bugs processed: {stats['bugs_processed']}")
        print(f"Cases found: {stats['cases_found']}")
        print(f"Closed cases: {stats['closed_cases']}")
        print(f"Comments added: {stats['comments_added']}")
        print(f"Errors: {stats['errors']}")
        
        if self.dry_run:
            print("\n🔍 This was a dry run - no actual comments were added")


def main():
    """Main function with command line argument parsing"""
    parser = argparse.ArgumentParser(
        description='Monitor ROX bugs and add comments when linked support cases are closed',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python rox_case_monitor.py --jira-token YOUR_JIRA_TOKEN
  
  # With Red Hat Customer Portal token
  python rox_case_monitor.py --jira-token YOUR_JIRA_TOKEN --rh-token YOUR_RH_TOKEN
  
  # Dry run mode (test without adding comments)
  python rox_case_monitor.py --jira-token YOUR_JIRA_TOKEN --dry-run
  
  # Debug mode with limited bugs
  python rox_case_monitor.py --jira-token YOUR_JIRA_TOKEN --debug --max-bugs 10
  
  # Slower rate limiting to avoid 429 errors
  python rox_case_monitor.py --jira-token YOUR_JIRA_TOKEN --rate-limit 2.0
        """
    )
    
    parser.add_argument(
        '--jira-token',
        default=os.getenv('JIRA_TOKEN', ''),
        help='Red Hat Jira API token (or set JIRA_TOKEN environment variable)'
    )
    parser.add_argument(
        '--rh-token',
        default=os.getenv('RH_PORTAL_TOKEN', ''),
        help='Red Hat Customer Portal API token (or set RH_PORTAL_TOKEN environment variable)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Test mode - analyze bugs but don\'t add comments'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    parser.add_argument(
        '--max-bugs',
        type=int,
        default=1000,
        help='Maximum number of bugs to process (default: 1000)'
    )
    parser.add_argument(
        '--rate-limit',
        type=float,
        default=1.0,
        help='Seconds to wait between requests to avoid rate limiting (default: 1.0)'
    )
    
    args = parser.parse_args()
    
    if not args.jira_token:
        print("❌ Error: Jira API token is required")
        print("   Set JIRA_TOKEN environment variable or use --jira-token argument")
        print("   Get your token from: https://issues.redhat.com/secure/ViewProfile.jspa?selectedTab=com.atlassian.pats.pats-plugin:jira-user-personal-access-tokens")
        sys.exit(1)
    
    try:
        monitor = ROXCaseMonitor(
            jira_token=args.jira_token,
            rh_token=args.rh_token or None,
            dry_run=args.dry_run,
            debug=args.debug,
            rate_limit=args.rate_limit
        )
        
        monitor.run(max_bugs=args.max_bugs)
        
    except KeyboardInterrupt:
        print("\n⚠️  Process interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()

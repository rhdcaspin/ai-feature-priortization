#!/usr/bin/env python3
"""
ROX Bugs and RFE Fetcher

This script connects to Red Hat Jira (https://issues.redhat.com) and fetches:
1. Issues from CIPOE project (if specified)
2. Related ROX bugs (non-closed only) and RFE requests (rhacs component only) linked to CIPOE issues
3. Filters results by "impacts account" issue links if specified

Enhanced Filtering:
- ROX Project: Only non-closed bugs (excludes closed, done, resolved, cancelled, rejected)
- RFE Project: Only RFEs with 'rhacs' component
- CIPOE-first approach: Query CIPOE project first, then find linked issues
- More efficient and accurate than searching ROX/RFE directly

Usage:
    python rox_rfe_fetcher.py --cipoe-project CIPOE-123
    python rox_rfe_fetcher.py --cipoe-project CIPOE-123 --impacts-account-only
    python rox_rfe_fetcher.py --cipoe-project CIPOE-123 --output csv --debug
"""

import os
import sys
import json
import csv
import requests
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class JiraIssue:
    """Represents a Jira issue with relevant fields"""
    key: str
    summary: str
    issue_type: str
    status: str
    project: str
    assignee: str
    priority: str
    created: str
    updated: str
    cipoe_project: str = ""
    description: str = ""
    issue_links: List[Dict] = None
    impacts_account: bool = False
    components: List[str] = None


class ROXRFEFetcher:
    """Fetches open bugs from ROX and open RFEs from RFE projects"""
    
    def __init__(self, jira_url: str = "https://issues.redhat.com", api_token: str = None, debug: bool = False):
        """
        Initialize the fetcher
        
        Args:
            jira_url: Base URL of Red Hat Jira instance
            api_token: Jira API token for authentication
            debug: Enable debug mode for troubleshooting
        """
        self.jira_url = jira_url.rstrip('/')
        self.api_token = api_token
        self.debug = debug
        self.session = requests.Session()
        
        # Set up authentication for Red Hat Jira (Bearer token)
        if api_token:
            self.session.headers.update({
                'Authorization': f'Bearer {api_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            })
        else:
            raise ValueError("API token is required for Red Hat Jira authentication")
    
    def test_connection(self) -> bool:
        """Test the Jira connection"""
        try:
            # Try API v2 first for Red Hat Jira
            for api_version in ['2', '3']:
                try:
                    response = self.session.get(f"{self.jira_url}/rest/api/{api_version}/myself")
                    response.raise_for_status()
                    
                    # Check if response is JSON
                    try:
                        user_data = response.json()
                    except ValueError:
                        print(f"⚠️  API {api_version} returned non-JSON response")
                        continue
                        
                    print(f"✅ Connected to Jira as: {user_data.get('displayName', 'Unknown')}")
                    print(f"   Using API version: {api_version}")
                    # Store working API version
                    self.api_version = api_version
                    
                    # Test basic search capability if in debug mode
                    if self.debug:
                        self.test_basic_search(api_version)
                    
                    return True
                except requests.exceptions.HTTPError as e:
                    print(f"⚠️  API {api_version} failed with HTTP {e.response.status_code}")
                    continue
            
            print(f"❌ Failed to connect with both API v2 and v3")
            return False
        except Exception as e:
            print(f"❌ Failed to connect to Jira: {e}")
            return False
    
    def test_basic_search(self, api_version: str):
        """Test basic search functionality for debugging"""
        try:
            print(f"🐛 DEBUG: Testing basic search functionality...")
            
            # Test 1: Search for any issues in ROX project
            test_params = {
                'jql': 'project = ROX',
                'maxResults': 1,
                'fields': 'key,summary'
            }
            
            response = self.session.get(
                f"{self.jira_url}/rest/api/{api_version}/search",
                params=test_params
            )
            
            if response.status_code == 200:
                data = response.json()
                total_rox = data.get('total', 0)
                print(f"🐛 DEBUG: Total issues in ROX project: {total_rox}")
            else:
                print(f"🐛 DEBUG: ROX project search failed: {response.status_code}")
                print(f"🐛 DEBUG: Response: {response.text}")
            
            # Test 2: Search for any issues in RFE project
            test_params['jql'] = 'project = RFE'
            response = self.session.get(
                f"{self.jira_url}/rest/api/{api_version}/search",
                params=test_params
            )
            
            if response.status_code == 200:
                data = response.json()
                total_rfe = data.get('total', 0)
                print(f"🐛 DEBUG: Total issues in RFE project: {total_rfe}")
            else:
                print(f"🐛 DEBUG: RFE project search failed: {response.status_code}")
                print(f"🐛 DEBUG: Response: {response.text}")
                
            # Test 3: List available projects
            response = self.session.get(f"{self.jira_url}/rest/api/{api_version}/project")
            if response.status_code == 200:
                projects = response.json()
                project_keys = [p.get('key', 'Unknown') for p in projects[:10]]  # First 10 projects
                print(f"🐛 DEBUG: Available projects (first 10): {project_keys}")
            else:
                print(f"🐛 DEBUG: Failed to list projects: {response.status_code}")
                
        except Exception as e:
            print(f"🐛 DEBUG: Basic search test failed: {e}")
    
    def get_issue_links(self, issue_key: str) -> List[Dict]:
        """
        Fetch issue links for a specific issue
        
        Args:
            issue_key: Jira issue key
            
        Returns:
            List of issue link dictionaries
        """
        try:
            api_version = getattr(self, 'api_version', '2')
            response = self.session.get(f"{self.jira_url}/rest/api/{api_version}/issue/{issue_key}")
            response.raise_for_status()
            
            issue_data = response.json()
            issue_links = issue_data.get('fields', {}).get('issuelinks', [])
            
            return issue_links
        except Exception as e:
            print(f"⚠️  Failed to fetch issue links for {issue_key}: {e}")
            return []
    
    def check_impacts_account_link(self, issue_links: List[Dict]) -> bool:
        """
        Check if any issue links are of type 'impacts account'
        
        Args:
            issue_links: List of issue link dictionaries
            
        Returns:
            True if any link is 'impacts account' type
        """
        if not issue_links:
            return False
        
        for link in issue_links:
            link_type = link.get('type', {})
            link_name = link_type.get('name', '').lower()
            
            # Check for various forms of "impacts account" link type
            if any(keyword in link_name for keyword in ['impacts account', 'impact account', 'affects account', 'account impact']):
                return True
        
        return False

    def fetch_cipoe_issues(self, cipoe_project: str = None) -> List[JiraIssue]:
        """
        Fetch issues from CIPOE project
        
        Args:
            cipoe_project: Specific CIPOE project key (e.g., CIPOE-123) or None for all CIPOE issues
            
        Returns:
            List of CIPOE issues
        """
        if cipoe_project:
            # Fetch specific CIPOE issue
            jql = f'key = {cipoe_project}'
            description = f"Fetching CIPOE issue: {cipoe_project}"
        else:
            # Fetch all open CIPOE issues
            jql = 'project = CIPOE AND status not in (Closed, Done, Resolved)'
            description = "Fetching open CIPOE issues"
        
        return self.fetch_issues(jql, description, fetch_links=True)

    def find_linked_issues(self, cipoe_issues: List[JiraIssue], target_projects: List[str] = None, 
                          link_types: List[str] = None, filter_criteria: Dict = None) -> List[JiraIssue]:
        """
        Find issues linked to CIPOE issues in specified projects
        
        Args:
            cipoe_issues: List of CIPOE issues to find links for
            target_projects: List of project keys to search in (e.g., ['ROX', 'RFE'])
            link_types: List of link types to filter by (e.g., ['impacts account'])
            filter_criteria: Dict with project-specific filters (e.g., {'ROX': {'exclude_closed': True}, 'RFE': {'component': 'rhacs'}})
            
        Returns:
            List of linked issues from target projects
        """
        if not cipoe_issues:
            return []
        
        if target_projects is None:
            target_projects = ['ROX', 'RFE']
        
        linked_issues = []
        
        for cipoe_issue in cipoe_issues:
            if self.debug:
                print(f"🐛 DEBUG: Finding links for CIPOE issue {cipoe_issue.key}")
            
            # Get issue links for this CIPOE issue
            issue_links = cipoe_issue.issue_links or self.get_issue_links(cipoe_issue.key)
            
            for link in issue_links:
                linked_issue_key = None
                link_type_name = link.get('type', {}).get('name', '').lower()
                
                # Check if this is the link type we're interested in
                if link_types:
                    if not any(lt.lower() in link_type_name for lt in link_types):
                        continue
                
                # Extract the linked issue key
                if 'outwardIssue' in link:
                    linked_issue_key = link['outwardIssue'].get('key')
                elif 'inwardIssue' in link:
                    linked_issue_key = link['inwardIssue'].get('key')
                
                if linked_issue_key:
                    # Check if the linked issue is in one of our target projects
                    project_key = linked_issue_key.split('-')[0] if '-' in linked_issue_key else ''
                    
                    if project_key in target_projects:
                        if self.debug:
                            print(f"🐛 DEBUG: Found linked issue {linked_issue_key} in project {project_key}")
                        
                        # Fetch the full issue details
                        linked_issue = self.fetch_single_issue(linked_issue_key)
                        if linked_issue:
                            # Apply project-specific filters
                            if self.should_include_issue(linked_issue, filter_criteria):
                                linked_issues.append(linked_issue)
        
        # Remove duplicates based on issue key
        seen_keys = set()
        unique_issues = []
        for issue in linked_issues:
            if issue.key not in seen_keys:
                seen_keys.add(issue.key)
                unique_issues.append(issue)
        
        return unique_issues

    def should_include_issue(self, issue: JiraIssue, filter_criteria: Dict = None) -> bool:
        """
        Check if an issue should be included based on filter criteria
        
        Args:
            issue: JiraIssue to check
            filter_criteria: Dict with project-specific filters
            
        Returns:
            True if issue should be included
        """
        if not filter_criteria:
            return True
        
        project_filters = filter_criteria.get(issue.project, {})
        
        # Check ROX project filters
        if issue.project == 'ROX':
            # Exclude closed bugs
            if project_filters.get('exclude_closed', False):
                closed_statuses = ['closed', 'done', 'resolved', 'cancelled', 'rejected']
                if issue.status.lower() in closed_statuses:
                    if self.debug:
                        print(f"🐛 DEBUG: Excluding closed ROX issue {issue.key} (status: {issue.status})")
                    return False
            
            # Only include bugs
            if project_filters.get('bugs_only', False):
                if 'bug' not in issue.issue_type.lower():
                    if self.debug:
                        print(f"🐛 DEBUG: Excluding non-bug ROX issue {issue.key} (type: {issue.issue_type})")
                    return False
        
        # Check RFE project filters
        elif issue.project == 'RFE':
            # Filter by component
            required_component = project_filters.get('component')
            if required_component:
                # Check if issue has the required component
                if not hasattr(issue, 'components') or not issue.components:
                    if self.debug:
                        print(f"🐛 DEBUG: Excluding RFE issue {issue.key} (no components)")
                    return False
                
                component_names = [comp.lower() for comp in issue.components]
                # Check if any component contains the required component (more flexible matching)
                if not any(required_component.lower() in comp_name for comp_name in component_names):
                    if self.debug:
                        print(f"🐛 DEBUG: Excluding RFE issue {issue.key} (components: {issue.components}, required: {required_component})")
                    return False
        
        return True

    def fetch_single_issue(self, issue_key: str) -> Optional[JiraIssue]:
        """
        Fetch a single issue by key
        
        Args:
            issue_key: Jira issue key
            
        Returns:
            JiraIssue object or None if not found
        """
        try:
            api_version = getattr(self, 'api_version', '2')
            
            fields = 'key,summary,description,issuetype,status,project,assignee,priority,created,updated,customfield_12313440,issuelinks,components'
            
            response = self.session.get(
                f"{self.jira_url}/rest/api/{api_version}/issue/{issue_key}",
                params={'fields': fields}
            )
            
            if response.status_code == 200:
                issue_data = response.json()
                fields = issue_data.get('fields', {})
                
                # Extract assignee
                assignee = fields.get('assignee')
                assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
                
                # Extract priority
                priority = fields.get('priority')
                priority_name = priority.get('name', 'None') if priority else 'None'
                
                # Extract CIPOE project from custom fields or description
                cipoe_project = ""
                custom_field_value = fields.get('customfield_12313440')
                if custom_field_value:
                    if isinstance(custom_field_value, dict):
                        cipoe_project = custom_field_value.get('key', custom_field_value.get('name', ''))
                    elif isinstance(custom_field_value, str):
                        cipoe_project = custom_field_value
                
                # Handle issue links
                issue_links = fields.get('issuelinks', [])
                impacts_account = self.check_impacts_account_link(issue_links)
                
                # Extract components
                components = []
                components_data = fields.get('components', [])
                if components_data:
                    components = [comp.get('name', '') for comp in components_data if comp.get('name')]
                
                return JiraIssue(
                    key=issue_data.get('key', ''),
                    summary=fields.get('summary', ''),
                    issue_type=fields.get('issuetype', {}).get('name', ''),
                    status=fields.get('status', {}).get('name', ''),
                    project=fields.get('project', {}).get('key', ''),
                    assignee=assignee_name,
                    priority=priority_name,
                    created=fields.get('created', ''),
                    updated=fields.get('updated', ''),
                    cipoe_project=cipoe_project,
                    description=fields.get('description', ''),
                    issue_links=issue_links,
                    impacts_account=impacts_account,
                    components=components
                )
            else:
                if self.debug:
                    print(f"🐛 DEBUG: Failed to fetch issue {issue_key}: {response.status_code}")
                return None
                
        except Exception as e:
            if self.debug:
                print(f"🐛 DEBUG: Error fetching issue {issue_key}: {e}")
            return None

    def fetch_issues(self, jql_query: str, description: str, fetch_links: bool = False) -> List[JiraIssue]:
        """
        Fetch issues using JQL query
        
        Args:
            jql_query: JQL query string
            description: Description for logging
            fetch_links: Whether to fetch issue links for each issue
            
        Returns:
            List of JiraIssue objects
        """
        print(f"🔍 {description}")
        print(f"   JQL: {jql_query}")
        
        issues = []
        start_at = 0
        max_results = 50
        
        # Use the API version that worked in connection test
        api_version = getattr(self, 'api_version', '2')
        
        while True:
            try:
                fields = 'key,summary,description,issuetype,status,project,assignee,priority,created,updated,customfield_12313440,components'
                if fetch_links:
                    fields += ',issuelinks'
                
                params = {
                    'jql': jql_query,
                    'startAt': start_at,
                    'maxResults': max_results,
                    'fields': fields
                }
                
                if self.debug:
                    print(f"🐛 DEBUG: Making request to {self.jira_url}/rest/api/{api_version}/search")
                    print(f"🐛 DEBUG: Params: {params}")
                
                response = self.session.get(
                    f"{self.jira_url}/rest/api/{api_version}/search",
                    params=params
                )
                
                if self.debug:
                    print(f"🐛 DEBUG: Response status: {response.status_code}")
                    print(f"🐛 DEBUG: Response headers: {dict(response.headers)}")
                
                response.raise_for_status()
                data = response.json()
                
                if self.debug:
                    print(f"🐛 DEBUG: Response data keys: {list(data.keys())}")
                    print(f"🐛 DEBUG: Total issues in response: {data.get('total', 'unknown')}")
                    print(f"🐛 DEBUG: Issues in this batch: {len(data.get('issues', []))}")
                
                batch_issues = data.get('issues', [])
                
                for issue in batch_issues:
                    fields = issue.get('fields', {})
                    assignee = fields.get('assignee')
                    assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
                    
                    priority = fields.get('priority')
                    priority_name = priority.get('name', 'None') if priority else 'None'
                    
                    # Try to extract CIPOE project from custom fields or description
                    cipoe_project = ""
                    custom_field_value = fields.get('customfield_12313440')  # This might be CIPOE project field
                    if custom_field_value:
                        if isinstance(custom_field_value, dict):
                            cipoe_project = custom_field_value.get('key', custom_field_value.get('name', ''))
                        elif isinstance(custom_field_value, str):
                            cipoe_project = custom_field_value
                    
                    # If not found in custom field, try to extract from description
                    if not cipoe_project:
                        description_text = fields.get('description', '')
                        if description_text and 'CIPOE' in description_text:
                            # Simple regex to find CIPOE-XXX pattern
                            import re
                            cipoe_match = re.search(r'CIPOE-\d+', description_text)
                            if cipoe_match:
                                cipoe_project = cipoe_match.group()
                    
                    # Handle issue links if requested
                    issue_links = []
                    impacts_account = False
                    if fetch_links:
                        issue_links = fields.get('issuelinks', [])
                        impacts_account = self.check_impacts_account_link(issue_links)
                    
                    # Extract components
                    components = []
                    components_data = fields.get('components', [])
                    if components_data:
                        components = [comp.get('name', '') for comp in components_data if comp.get('name')]
                    
                    jira_issue = JiraIssue(
                        key=issue.get('key', ''),
                        summary=fields.get('summary', ''),
                        issue_type=fields.get('issuetype', {}).get('name', ''),
                        status=fields.get('status', {}).get('name', ''),
                        project=fields.get('project', {}).get('key', ''),
                        assignee=assignee_name,
                        priority=priority_name,
                        created=fields.get('created', ''),
                        updated=fields.get('updated', ''),
                        cipoe_project=cipoe_project,
                        description=fields.get('description', ''),
                        issue_links=issue_links,
                        impacts_account=impacts_account,
                        components=components
                    )
                    issues.append(jira_issue)
                
                if len(batch_issues) < max_results:
                    break
                    
                start_at += max_results
                
            except Exception as e:
                print(f"❌ Error fetching issues: {e}")
                if hasattr(e, 'response') and e.response:
                    print(f"   Response status: {e.response.status_code}")
                    print(f"   Response text: {e.response.text}")
                    if self.debug:
                        print(f"🐛 DEBUG: Full response headers: {dict(e.response.headers)}")
                break
        
        print(f"📊 Found {len(issues)} issues")
        return issues
    
    def fetch_rox_bugs(self, impacts_account_only: bool = False) -> List[JiraIssue]:
        """
        Fetch all open bugs from ROX project
        
        Args:
            impacts_account_only: If True, only fetch bugs with 'impacts account' issue links
        """
        base_jql = 'project = ROX AND issuetype = Bug AND status in (Open, "In Progress", New, Assigned, "To Do", "In Review")'
        
        if impacts_account_only:
            # Try to use JQL filtering first (may not work on all Jira instances)
            jql = f'{base_jql} AND issueFunction in linkedIssuesOfRecursive("project = CIPOE", "impacts account")'
            description = "Fetching open ROX bugs with 'impacts account' links"
        else:
            jql = base_jql
            description = "Fetching open bugs from ROX project"
        
        # Always fetch links to allow post-processing filtering
        issues = self.fetch_issues(jql, description, fetch_links=True)
        
        # If JQL filtering failed or we want to double-check, filter manually
        if impacts_account_only:
            issues = [issue for issue in issues if issue.impacts_account]
        
        return issues
    
    def fetch_rfe_requests(self, impacts_account_only: bool = False) -> List[JiraIssue]:
        """
        Fetch all open RFEs from RFE project
        
        Args:
            impacts_account_only: If True, only fetch RFEs with 'impacts account' issue links
        """
        base_jql = 'project = RFE AND issuetype = RFE AND status in (Open, "In Progress", New, Assigned, "To Do", "In Review")'
        
        if impacts_account_only:
            # Try to use JQL filtering first (may not work on all Jira instances)
            jql = f'{base_jql} AND issueFunction in linkedIssuesOfRecursive("project = CIPOE", "impacts account")'
            description = "Fetching open RFEs with 'impacts account' links"
        else:
            jql = base_jql
            description = "Fetching open RFEs from RFE project"
        
        # Always fetch links to allow post-processing filtering
        issues = self.fetch_issues(jql, description, fetch_links=True)
        
        # If JQL filtering failed or we want to double-check, filter manually
        if impacts_account_only:
            issues = [issue for issue in issues if issue.impacts_account]
        
        return issues
    
    def filter_by_cipoe_project(self, issues: List[JiraIssue], cipoe_project: str) -> List[JiraIssue]:
        """
        Filter issues by CIPOE project key
        
        Args:
            issues: List of JiraIssue objects
            cipoe_project: CIPOE project key to filter by
            
        Returns:
            Filtered list of issues
        """
        if not cipoe_project:
            return issues
        
        print(f"🔍 Filtering by CIPOE project: {cipoe_project}")
        
        filtered_issues = []
        for issue in issues:
            # Check if CIPOE project matches (case-insensitive)
            if (issue.cipoe_project and cipoe_project.upper() in issue.cipoe_project.upper()) or \
               (issue.description and cipoe_project.upper() in issue.description.upper()) or \
               (issue.summary and cipoe_project.upper() in issue.summary.upper()):
                filtered_issues.append(issue)
        
        print(f"📊 Filtered to {len(filtered_issues)} issues matching CIPOE project {cipoe_project}")
        return filtered_issues
    
    def print_issues(self, issues: List[JiraIssue], title: str):
        """Print issues in a formatted way"""
        if not issues:
            print(f"\n{title}: No issues found")
            return
        
        print(f"\n{title} ({len(issues)} issues):")
        print("=" * 80)
        
        for issue in issues:
            print(f"🎫 {issue.key}: {issue.summary}")
            print(f"   Type: {issue.issue_type} | Status: {issue.status} | Priority: {issue.priority}")
            print(f"   Assignee: {issue.assignee}")
            if issue.components:
                print(f"   Components: {', '.join(issue.components)}")
            if issue.cipoe_project:
                print(f"   CIPOE Project: {issue.cipoe_project}")
            if issue.impacts_account:
                print(f"   🔗 Impacts Account: Yes")
            print(f"   Created: {issue.created[:10]} | Updated: {issue.updated[:10]}")
            print()
    
    def export_to_csv(self, rox_bugs: List[JiraIssue], rfe_requests: List[JiraIssue], filename: str = None):
        """Export issues to CSV file"""
        if not filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"rox_rfe_issues_{timestamp}.csv"
        
        print(f"📄 Exporting to CSV: {filename}")
        
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'Key', 'Summary', 'Type', 'Status', 'Priority', 'Project', 
                'Assignee', 'Components', 'CIPOE Project', 'Impacts Account', 'Created', 'Updated'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Write header
            writer.writeheader()
            
            # Write ROX bugs
            for issue in rox_bugs:
                writer.writerow({
                    'Key': issue.key,
                    'Summary': issue.summary,
                    'Type': issue.issue_type,
                    'Status': issue.status,
                    'Priority': issue.priority,
                    'Project': issue.project,
                    'Assignee': issue.assignee,
                    'Components': ', '.join(issue.components) if issue.components else '',
                    'CIPOE Project': issue.cipoe_project,
                    'Impacts Account': 'Yes' if issue.impacts_account else 'No',
                    'Created': issue.created[:10] if issue.created else '',
                    'Updated': issue.updated[:10] if issue.updated else ''
                })
            
            # Write RFE requests
            for issue in rfe_requests:
                writer.writerow({
                    'Key': issue.key,
                    'Summary': issue.summary,
                    'Type': issue.issue_type,
                    'Status': issue.status,
                    'Priority': issue.priority,
                    'Project': issue.project,
                    'Assignee': issue.assignee,
                    'Components': ', '.join(issue.components) if issue.components else '',
                    'CIPOE Project': issue.cipoe_project,
                    'Impacts Account': 'Yes' if issue.impacts_account else 'No',
                    'Created': issue.created[:10] if issue.created else '',
                    'Updated': issue.updated[:10] if issue.updated else ''
                })
        
        print(f"✅ CSV export completed: {filename}")
        return filename
    
    def export_to_json(self, rox_bugs: List[JiraIssue], rfe_requests: List[JiraIssue], filename: str = None):
        """Export issues to JSON file"""
        if not filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"rox_rfe_issues_{timestamp}.json"
        
        print(f"📄 Exporting to JSON: {filename}")
        
        data = {
            'export_timestamp': datetime.now().isoformat(),
            'rox_bugs': [
                {
                    'key': issue.key,
                    'summary': issue.summary,
                    'issue_type': issue.issue_type,
                    'status': issue.status,
                    'priority': issue.priority,
                    'project': issue.project,
                    'assignee': issue.assignee,
                    'components': issue.components,
                    'cipoe_project': issue.cipoe_project,
                    'impacts_account': issue.impacts_account,
                    'created': issue.created,
                    'updated': issue.updated
                }
                for issue in rox_bugs
            ],
            'rfe_requests': [
                {
                    'key': issue.key,
                    'summary': issue.summary,
                    'issue_type': issue.issue_type,
                    'status': issue.status,
                    'priority': issue.priority,
                    'project': issue.project,
                    'assignee': issue.assignee,
                    'components': issue.components,
                    'cipoe_project': issue.cipoe_project,
                    'impacts_account': issue.impacts_account,
                    'created': issue.created,
                    'updated': issue.updated
                }
                for issue in rfe_requests
            ]
        }
        
        with open(filename, 'w', encoding='utf-8') as jsonfile:
            json.dump(data, jsonfile, indent=2, ensure_ascii=False)
        
        print(f"✅ JSON export completed: {filename}")
        return filename
    
    def run(self, cipoe_project: str = None, output_format: str = 'console', impacts_account_only: bool = False, include_issues: List[str] = None):
        """
        Run the complete fetching process using CIPOE-first approach
        
        Args:
            cipoe_project: CIPOE project key to query (required for new approach)
            output_format: Output format ('console', 'csv', 'json', 'all')
            impacts_account_only: If True, only fetch issues with 'impacts account' links
            include_issues: List of specific issue keys to include regardless of linking (e.g., ['ROX-30293'])
        """
        print("🚀 Starting ROX Bugs and RFE Fetcher (CIPOE-first approach)")
        print("=" * 60)
        
        # Test connection
        if not self.test_connection():
            return
        
        # New approach: Start with CIPOE project
        if not cipoe_project:
            print("⚠️  CIPOE project key is required for the new approach")
            print("   Use --cipoe-project CIPOE-123 to specify a CIPOE issue")
            print("   Or use --cipoe-project CIPOE to fetch all CIPOE issues")
            return
        
        # Step 1: Fetch CIPOE issues
        print(f"\n📋 Step 1: Fetching CIPOE issues for {cipoe_project}")
        cipoe_issues = self.fetch_cipoe_issues(cipoe_project)
        
        if not cipoe_issues:
            print(f"❌ No CIPOE issues found for {cipoe_project}")
            return
        
        print(f"✅ Found {len(cipoe_issues)} CIPOE issue(s)")
        if self.debug:
            for issue in cipoe_issues:
                print(f"🐛 DEBUG: CIPOE issue {issue.key}: {issue.summary}")
        
        # Step 2: Find linked ROX bugs and RFE requests
        print(f"\n🔗 Step 2: Finding linked ROX bugs and RFE requests")
        
        link_types = ['impacts account'] if impacts_account_only else None
        
        # Set up filtering criteria
        filter_criteria = {
            'ROX': {
                'exclude_closed': True,  # Only non-closed bugs
                'bugs_only': True        # Only bugs, not other issue types
            },
            'RFE': {
                'component': 'rhacs'     # Only RFEs with 'rhacs' component
            }
        }
        
        linked_issues = self.find_linked_issues(cipoe_issues, ['ROX', 'RFE'], link_types, filter_criteria)
        
        # Add specific issues that should be included regardless of linking
        if include_issues:
            print(f"\n📌 Step 3: Adding specifically requested issues")
            for issue_key in include_issues:
                print(f"   Fetching {issue_key}...")
                specific_issue = self.fetch_single_issue(issue_key)
                if specific_issue:
                    # Check if it should be included based on filters
                    if self.should_include_issue(specific_issue, filter_criteria):
                        # Check if not already in linked_issues
                        if not any(issue.key == specific_issue.key for issue in linked_issues):
                            linked_issues.append(specific_issue)
                            print(f"   ✅ Added {issue_key}")
                        else:
                            print(f"   ℹ️  {issue_key} already included")
                    else:
                        print(f"   ⚠️  {issue_key} excluded by filters")
                else:
                    print(f"   ❌ Failed to fetch {issue_key}")
        
        # Separate ROX bugs and RFE requests
        rox_bugs = [issue for issue in linked_issues if issue.project == 'ROX' and 'bug' in issue.issue_type.lower()]
        rfe_requests = [issue for issue in linked_issues if issue.project == 'RFE' and 'rfe' in issue.issue_type.lower()]
        
        # Also include any ROX/RFE issues that might not be bugs/RFEs specifically
        other_rox = [issue for issue in linked_issues if issue.project == 'ROX' and 'bug' not in issue.issue_type.lower()]
        other_rfe = [issue for issue in linked_issues if issue.project == 'RFE' and 'rfe' not in issue.issue_type.lower()]
        
        if other_rox:
            print(f"ℹ️  Found {len(other_rox)} other ROX issues (not bugs): {[i.issue_type for i in other_rox]}")
            rox_bugs.extend(other_rox)
        
        if other_rfe:
            print(f"ℹ️  Found {len(other_rfe)} other RFE issues (not RFEs): {[i.issue_type for i in other_rfe]}")
            rfe_requests.extend(other_rfe)
        
        # Output results
        if output_format in ['console', 'all']:
            self.print_issues(rox_bugs, "ROX OPEN BUGS")
            self.print_issues(rfe_requests, "RFE OPEN REQUESTS")
        
        if output_format in ['csv', 'all']:
            self.export_to_csv(rox_bugs, rfe_requests)
        
        if output_format in ['json', 'all']:
            self.export_to_json(rox_bugs, rfe_requests)
        
        # Summary
        print("\n" + "=" * 60)
        print("📊 SUMMARY")
        print("=" * 60)
        print(f"ROX Bugs found: {len(rox_bugs)}")
        print(f"RFE Requests found: {len(rfe_requests)}")
        print(f"Total issues: {len(rox_bugs) + len(rfe_requests)}")
        if cipoe_project:
            print(f"Filtered by CIPOE project: {cipoe_project}")
        if impacts_account_only:
            print(f"Filtered by 'impacts account' links: Yes")


def main():
    """Main function with command line argument parsing"""
    parser = argparse.ArgumentParser(
        description='Fetch open bugs from ROX project and open RFEs from RFE project',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch ROX/RFE issues linked to specific CIPOE issue
  python rox_rfe_fetcher.py --cipoe-project CIPOE-129002
  
  # Only fetch issues with 'impacts account' links
  python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --impacts-account-only
  
  # Export to CSV
  python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --output csv
  
  # Include specific issues regardless of linking
  python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --include-issues ROX-30293
  
  # Export to all formats with debug info
  python rox_rfe_fetcher.py --cipoe-project CIPOE-129002 --output all --debug
        """
    )
    
    parser.add_argument(
        '--jira-url', 
        default='https://issues.redhat.com', 
        help='Jira base URL (default: https://issues.redhat.com)'
    )
    parser.add_argument(
        '--token', 
        default=os.getenv('JIRA_TOKEN', ''), 
        help='Your Jira API token (or set JIRA_TOKEN environment variable)'
    )
    parser.add_argument(
        '--cipoe-project', 
        required=True,
        help='CIPOE project key to query (e.g., CIPOE-129002 for specific issue, or CIPOE for all issues)'
    )
    parser.add_argument(
        '--output', 
        choices=['console', 'csv', 'json', 'all'], 
        default='console',
        help='Output format (default: console)'
    )
    parser.add_argument(
        '--impacts-account-only',
        action='store_true',
        help='Only fetch issues that have "impacts account" issue links'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode for troubleshooting'
    )
    parser.add_argument(
        '--include-issues',
        nargs='*',
        help='Specific issue keys to include regardless of linking (e.g., ROX-30293 RFE-1234)'
    )
    
    args = parser.parse_args()
    
    if not args.token:
        print("❌ Error: Jira API token is required")
        print("   Set JIRA_TOKEN environment variable or use --token argument")
        print("   Get your token from: https://issues.redhat.com/secure/ViewProfile.jspa?selectedTab=com.atlassian.pats.pats-plugin:jira-user-personal-access-tokens")
        sys.exit(1)
    
    try:
        fetcher = ROXRFEFetcher(
            jira_url=args.jira_url,
            api_token=args.token,
            debug=args.debug
        )
        
        fetcher.run(
            cipoe_project=args.cipoe_project,
            output_format=args.output,
            impacts_account_only=args.impacts_account_only,
            include_issues=args.include_issues
        )
        
    except KeyboardInterrupt:
        print("\n⚠️  Process interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

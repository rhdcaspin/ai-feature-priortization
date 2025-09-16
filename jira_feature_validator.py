#!/usr/bin/env python3
"""
Jira Feature Template Validator

This script connects to a Jira organization using API token authentication,
filters for 4.10 features, and validates them against a required template structure.
"""

import os
import re
import json
import csv
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import argparse
from dataclasses import dataclass


@dataclass
class TemplateSection:
    """Represents a required template section"""
    name: str
    header: str
    required: bool = True
    placeholder: str = "<your text here>"


class JiraFeatureValidator:
    """Validates Jira features against template requirements"""
    
    # Template sections to validate
    TEMPLATE_SECTIONS = [
        TemplateSection("goal_summary", "Goal Summary:", True, "<your text here>"),
        TemplateSection("goals_outcomes", "Goals and expected user outcomes:", True, "<your text here>"),
        TemplateSection("acceptance_criteria", "Acceptance Criteria:", True, "<enter general Feature acceptance here>"),
        TemplateSection("success_criteria", "Success Criteria or KPIs measured:", True, "<enter success criteria and/or KPIs here>"),
        TemplateSection("use_cases", "Use Cases (Optional):", False, "<your text here>"),
        TemplateSection("out_of_scope", "Out of Scope (Optional):", False, "<your text here>")
    ]
    
    def __init__(self, jira_url: str, email: str, api_token: str, project_key: str = None):
        """
        Initialize the Jira validator
        
        Args:
            jira_url: Base URL of your Jira instance (e.g., https://issues.redhat.com)
            email: Your Jira email address (for Red Hat Jira, can be optional)
            api_token: Your Jira API token
            project_key: Optional project key to filter by
        """
        self.jira_url = jira_url.rstrip('/')
        self.api_token = api_token
        self.email = email
        self.project_key = project_key
        self.session = requests.Session()
        
        # Set up authentication for Red Hat Jira (Bearer token)
        if 'redhat.com' in jira_url:
            self.session.headers.update({
                'Authorization': f'Bearer {api_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            })
        else:
            # Standard Atlassian Jira (Basic auth)
            self.session.auth = (email, api_token)
        
    def test_connection(self) -> bool:
        """Test the Jira connection"""
        try:
            # Try API v2 first for Red Hat Jira, then v3
            for api_version in ['2', '3']:
                try:
                    response = self.session.get(f"{self.jira_url}/rest/api/{api_version}/myself")
                    response.raise_for_status()
                    
                    # Check if response is JSON
                    try:
                        user_data = response.json()
                    except ValueError:
                        print(f"⚠️  API {api_version} returned non-JSON response: {response.text[:100]}")
                        continue
                        
                    print(f"✅ Connected to Jira as: {user_data.get('displayName', 'Unknown')}")
                    print(f"   Using API version: {api_version}")
                    # Store working API version
                    self.api_version = api_version
                    return True
                except requests.exceptions.HTTPError as e:
                    print(f"⚠️  API {api_version} failed with HTTP {e.response.status_code}")
                    continue
            
            print(f"❌ Failed to connect with both API v2 and v3")
            return False
        except Exception as e:
            print(f"❌ Failed to connect to Jira: {e}")
            return False
    
    def get_4_10_features(self) -> List[Dict]:
        """
        Retrieve all 4.10 features from Jira
        
        Returns:
            List of feature issues
        """
        print("🔍 Searching for 4.10 features...")
        
        # JQL query to find 4.10 features in ROX project
        jql_parts = [
            'project = rox',
            '"Target Version" = 4.10.0',
            'type = feature'
        ]
        
        jql = ' AND '.join(jql_parts)
        
        print(f"🔍 Using JQL: {jql}")
        
        features = []
        start_at = 0
        max_results = 50
        
        while True:
            try:
                params = {
                    'jql': jql,
                    'startAt': start_at,
                    'maxResults': max_results,
                    'fields': 'summary,description,key,status,assignee,created,updated,customfield_12316752,customfield_12319940'
                }
                
                # Use the API version that worked in connection test
                api_version = getattr(self, 'api_version', '2')
                response = self.session.get(
                    f"{self.jira_url}/rest/api/{api_version}/search",
                    params=params
                )
                response.raise_for_status()
                data = response.json()
                
                issues = data.get('issues', [])
                features.extend(issues)
                
                if len(issues) < max_results:
                    break
                    
                start_at += max_results
                
            except Exception as e:
                print(f"❌ Error fetching features: {e}")
                break
        
        print(f"📊 Found {len(features)} 4.10 features")
        return features
    
    def extract_template_sections(self, description: str) -> Dict[str, str]:
        """
        Extract template sections from feature description
        
        Args:
            description: The feature description text
            
        Returns:
            Dictionary mapping section names to their content
        """
        if not description:
            return {}
        
        sections = {}
        
        for section in self.TEMPLATE_SECTIONS:
            # Create regex pattern to find the section
            pattern = rf"{re.escape(section.header)}\s*(.*?)(?=\n\n[A-Z][^:]*:|$)"
            match = re.search(pattern, description, re.DOTALL | re.IGNORECASE)
            
            if match:
                content = match.group(1).strip()
                sections[section.name] = content
            else:
                sections[section.name] = ""
        
        return sections
    
    def validate_section(self, section: TemplateSection, content: str) -> Tuple[bool, str]:
        """
        Validate a template section
        
        Args:
            section: The template section definition
            content: The actual content from the feature
            
        Returns:
            Tuple of (is_valid, validation_message)
        """
        if not content:
            if section.required:
                return False, f"❌ Missing required section: {section.header}"
            else:
                return True, f"⚠️  Optional section not present: {section.header}"
        
        # Check if content is just the placeholder
        if content.strip() == section.placeholder or content.strip() in ["<your text here>", "<enter general Feature acceptance here>", "<enter success criteria and/or KPIs here>"]:
            if section.required:
                return False, f"❌ Section has placeholder text: {section.header}"
            else:
                return True, f"⚠️  Optional section has placeholder text: {section.header}"
        
        # Check for minimum content length (adjust as needed)
        if len(content.strip()) < 10:
            return False, f"❌ Section content too short: {section.header}"
        
        return True, f"✅ Section complete: {section.header}"
    
    def validate_feature(self, feature: Dict) -> Dict:
        """
        Validate a single feature against the template
        
        Args:
            feature: Jira feature issue data
            
        Returns:
            Validation report dictionary
        """
        key = feature.get('key', 'Unknown')
        summary = feature.get('fields', {}).get('summary', 'No summary')
        description = feature.get('fields', {}).get('description', '')
        
        # Extract sections from description
        sections = self.extract_template_sections(description)
        
        # Validate each section
        validation_results = []
        required_missing = 0
        optional_missing = 0
        
        for section in self.TEMPLATE_SECTIONS:
            content = sections.get(section.name, '')
            is_valid, message = self.validate_section(section, content)
            
            validation_results.append({
                'section': section.name,
                'header': section.header,
                'required': section.required,
                'valid': is_valid,
                'message': message,
                'content_preview': content[:100] + '...' if len(content) > 100 else content
            })
            
            if not is_valid and section.required:
                required_missing += 1
            elif not is_valid and not section.required:
                optional_missing += 1
        
        return {
            'key': key,
            'summary': summary,
            'validation_results': validation_results,
            'required_missing': required_missing,
            'optional_missing': optional_missing,
            'overall_valid': required_missing == 0
        }
    
    def generate_csv_report(self, features: List[Dict]) -> str:
        """
        Generate a CSV report with specified fields
        
        Args:
            features: List of feature issues from Jira
            
        Returns:
            CSV filename
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f"rox_4_10_features_report_{timestamp}.csv"
        
        print(f"📄 Generating CSV report: {csv_filename}")
        
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['Key', 'Summary', 'Assignee', 'Product Manager', 'Target Version']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Write header
            writer.writeheader()
            
            # Write data rows
            for feature in features:
                fields = feature.get('fields', {})
                assignee = fields.get('assignee')
                assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
                
                # Extract custom fields
                product_manager = fields.get('customfield_12316752')
                product_manager_name = ''
                if product_manager:
                    if isinstance(product_manager, dict):
                        product_manager_name = product_manager.get('displayName', '')
                    elif isinstance(product_manager, str):
                        product_manager_name = product_manager
                
                target_version = fields.get('customfield_12319940')
                target_version_name = ''
                if target_version:
                    if isinstance(target_version, list) and len(target_version) > 0:
                        # If it's a list, take the first item
                        first_version = target_version[0]
                        if isinstance(first_version, dict):
                            target_version_name = first_version.get('name', str(first_version))
                        else:
                            target_version_name = str(first_version)
                    elif isinstance(target_version, dict):
                        target_version_name = target_version.get('name', str(target_version))
                    else:
                        target_version_name = str(target_version)
                
                row = {
                    'Key': feature.get('key', ''),
                    'Summary': fields.get('summary', ''),
                    'Assignee': assignee_name,
                    'Product Manager': product_manager_name,
                    'Target Version': target_version_name
                }
                writer.writerow(row)
        
        print(f"✅ CSV report saved with {len(features)} features")
        return csv_filename

    def generate_report(self, validation_results: List[Dict]) -> str:
        """
        Generate a comprehensive validation report
        
        Args:
            validation_results: List of feature validation results
            
        Returns:
            Formatted report string
        """
        report = []
        report.append("=" * 80)
        report.append("🎯 JIRA 4.10 FEATURE TEMPLATE VALIDATION REPORT")
        report.append("=" * 80)
        report.append(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Summary statistics
        total_features = len(validation_results)
        fully_compliant = sum(1 for r in validation_results if r['overall_valid'])
        partially_compliant = sum(1 for r in validation_results if not r['overall_valid'] and r['required_missing'] == 0)
        non_compliant = total_features - fully_compliant
        
        report.append("📊 SUMMARY STATISTICS")
        report.append("-" * 40)
        report.append(f"Total Features Analyzed: {total_features}")
        report.append(f"✅ Fully Compliant: {fully_compliant} ({fully_compliant/total_features*100:.1f}%)")
        report.append(f"❌ Non-Compliant: {non_compliant} ({non_compliant/total_features*100:.1f}%)")
        report.append("")
        
        # Detailed results
        report.append("📋 DETAILED VALIDATION RESULTS")
        report.append("-" * 40)
        
        for result in validation_results:
            report.append(f"\n🎫 {result['key']}: {result['summary']}")
            report.append(f"   Overall Status: {'✅ COMPLIANT' if result['overall_valid'] else '❌ NON-COMPLIANT'}")
            report.append(f"   Required sections missing: {result['required_missing']}")
            report.append(f"   Optional sections missing: {result['optional_missing']}")
            
            for validation in result['validation_results']:
                report.append(f"   {validation['message']}")
                if not validation['valid'] and validation['content_preview']:
                    report.append(f"      Preview: {validation['content_preview']}")
        
        # Recommendations
        report.append("\n" + "=" * 80)
        report.append("💡 RECOMMENDATIONS")
        report.append("=" * 80)
        
        if non_compliant > 0:
            report.append("1. Review non-compliant features and ensure all required sections are completed")
            report.append("2. Replace placeholder text with actual feature information")
            report.append("3. Ensure each section has sufficient detail (minimum 10 characters)")
            report.append("4. Consider adding optional sections for better feature documentation")
        else:
            report.append("🎉 All features are compliant with the template requirements!")
        
        return "\n".join(report)
    
    def run_validation(self) -> None:
        """Run the complete validation process"""
        print("🚀 Starting ROX 4.10 Feature Analysis and Template Validation")
        print("=" * 60)
        
        # Test connection
        if not self.test_connection():
            return
        
        # Get features
        features = self.get_4_10_features()
        if not features:
            print("⚠️  No 4.10 features found")
            return
        
        # Generate CSV report with requested fields
        csv_filename = self.generate_csv_report(features)
        
        # Validate features against template
        print("🔍 Validating features against template...")
        validation_results = []
        
        for i, feature in enumerate(features, 1):
            print(f"   Processing {i}/{len(features)}: {feature.get('key', 'Unknown')}")
            result = self.validate_feature(feature)
            validation_results.append(result)
        
        # Generate and save validation report
        report = self.generate_report(validation_results)
        
        # Save validation report to file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_filename = f"jira_feature_validation_report_{timestamp}.txt"
        
        with open(report_filename, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"\n📄 Validation report saved to: {report_filename}")
        print(f"📄 CSV report saved to: {csv_filename}")
        print("\n" + "=" * 60)
        print("📊 QUICK SUMMARY")
        print("=" * 60)
        
        # Print quick summary
        total = len(validation_results)
        compliant = sum(1 for r in validation_results if r['overall_valid'])
        print(f"Features analyzed: {total}")
        print(f"Compliant: {compliant} ({compliant/total*100:.1f}%)")
        print(f"Non-compliant: {total-compliant} ({(total-compliant)/total*100:.1f}%)")


def main():
    """Main function with command line argument parsing"""
    parser = argparse.ArgumentParser(description='Generate CSV report for ROX 4.10 features with template validation')
    parser.add_argument('--jira-url', default='https://issues.redhat.com', help='Jira base URL (default: https://issues.redhat.com)')
    parser.add_argument('--email', help='Your Jira email address (optional for Red Hat Jira)')
    parser.add_argument('--token', default=os.getenv('JIRA_TOKEN', ''), help='Your Jira API token')
    
    args = parser.parse_args()
    
    try:
        validator = JiraFeatureValidator(
            jira_url=args.jira_url,
            email=args.email or "",  # Email can be empty for Red Hat Jira
            api_token=args.token,
            project_key="ROX"  # Hardcoded to ROX project
        )
        
        validator.run_validation()
        
    except KeyboardInterrupt:
        print("\n⚠️  Validation interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # You can also run directly with environment variables
    if len(os.sys.argv) == 1:  # No command line arguments
        # Try to get from environment variables
        jira_url = os.getenv('JIRA_URL', 'https://issues.redhat.com')
        email = os.getenv('JIRA_EMAIL', '')
        token = os.getenv('JIRA_TOKEN', '')
        project = os.getenv('JIRA_PROJECT')
        
        if jira_url and token:
            print("🔧 Using configuration for Red Hat Jira ROX project")
            validator = JiraFeatureValidator(jira_url, email, token, "ROX")
            validator.run_validation()
        else:
            print("💡 Usage examples:")
            print("\n1. Quick start (Red Hat Jira with provided token):")
            print("   python jira_feature_validator.py")
            print("\n2. Command line arguments:")
            print("   python jira_feature_validator.py --project RHEL")
            print("\n3. Environment variables:")
            print("   export JIRA_URL=https://issues.redhat.com")
            print("   export JIRA_EMAIL=your@redhat.com")
            print("   export JIRA_TOKEN=your_api_token")
            print("   export JIRA_PROJECT=RHEL  # optional")
            print("   python jira_feature_validator.py")
    else:
        main()


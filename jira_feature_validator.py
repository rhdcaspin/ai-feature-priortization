#!/usr/bin/env python3
"""
Jira Feature Template Validator

This script connects to a Jira organization using API token authentication,
filters features by target version, and validates them against a required template structure.

Usage:
    python3 jira_feature_validator.py --target-version 4.11.0
    python3 jira_feature_validator.py --target-version 4.12.0
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
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


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
    
    def __init__(self, jira_url: str, email: str, api_token: str,
                 project_key: str = "ROX", target_version: str = "4.11.0"):
        self.jira_url = jira_url.rstrip('/')
        self.api_token = api_token
        self.email = email
        self.project_key = project_key
        self.target_version = target_version
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
    
    def get_features(self) -> List[Dict]:
        """Retrieve features for the configured target version from Jira."""
        print(f"🔍 Searching for {self.target_version} features...")
        
        jql_parts = [
            f'project = {self.project_key}',
            f'"Target Version" = {self.target_version}',
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
        
        print(f"📊 Found {len(features)} {self.target_version} features")
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
    
    def _extract_display_name(self, field_value) -> str:
        if not field_value:
            return ""
        if isinstance(field_value, dict):
            return field_value.get("displayName", "")
        return str(field_value)

    def _extract_version_name(self, field_value) -> str:
        if not field_value:
            return ""
        if isinstance(field_value, list) and field_value:
            item = field_value[0]
            return item.get("name", str(item)) if isinstance(item, dict) else str(item)
        if isinstance(field_value, dict):
            return field_value.get("name", str(field_value))
        return str(field_value)

    def generate_compliance_csv(self, features: List[Dict],
                                validation_results: List[Dict]) -> str:
        """Generate a single CSV combining feature info and per-section compliance."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        version_tag = self.target_version.replace(".", "_")
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        csv_path = output_dir / f"rox_{version_tag}_compliance_{timestamp}.csv"

        section_headers = [s.header for s in self.TEMPLATE_SECTIONS]
        fieldnames = [
            "Key", "Summary", "Status", "Assignee", "Product Manager",
            "Target Version", "Compliant", "Required Missing",
        ] + section_headers

        feature_map = {f.get("key"): f for f in features}

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for result in validation_results:
                feature = feature_map.get(result["key"], {})
                fields = feature.get("fields", {})

                row = {
                    "Key": result["key"],
                    "Summary": result["summary"],
                    "Status": (fields.get("status") or {}).get("name", ""),
                    "Assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
                    "Product Manager": self._extract_display_name(fields.get("customfield_12316752")),
                    "Target Version": self._extract_version_name(fields.get("customfield_12319940")),
                    "Compliant": "Yes" if result["overall_valid"] else "No",
                    "Required Missing": result["required_missing"],
                }

                for v in result["validation_results"]:
                    if v["valid"]:
                        row[v["header"]] = "PASS"
                    elif not v["required"]:
                        row[v["header"]] = "SKIP (optional)"
                    elif not v["content_preview"]:
                        row[v["header"]] = "MISSING"
                    else:
                        row[v["header"]] = f"FAIL: {v['content_preview']}"

                writer.writerow(row)

        print(f"✅ Compliance CSV saved: {csv_path}")
        return str(csv_path)
    
    def run_validation(self) -> None:
        """Run the complete validation process"""
        print(f"🚀 Starting ROX {self.target_version} Feature Analysis and Template Validation")
        print("=" * 60)

        if not self.test_connection():
            return

        features = self.get_features()
        if not features:
            print(f"⚠️  No {self.target_version} features found")
            return

        print("🔍 Validating features against template...")
        validation_results = []
        for i, feature in enumerate(features, 1):
            print(f"   Processing {i}/{len(features)}: {feature.get('key', 'Unknown')}")
            validation_results.append(self.validate_feature(feature))

        csv_path = self.generate_compliance_csv(features, validation_results)

        total = len(validation_results)
        compliant = sum(1 for r in validation_results if r["overall_valid"])
        print(f"\n{'=' * 60}")
        print("📊 QUICK SUMMARY")
        print(f"{'=' * 60}")
        print(f"Features analyzed: {total}")
        print(f"Compliant: {compliant} ({compliant / total * 100:.1f}%)")
        print(f"Non-compliant: {total - compliant} ({(total - compliant) / total * 100:.1f}%)")
        print(f"\n📄 Report: {csv_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Validate ROX features against the required template structure'
    )
    parser.add_argument('--target-version', default='4.11.0',
                        help='Target version to validate (default: 4.11.0)')
    parser.add_argument('--jira-url', default=os.getenv('JIRA_BASE_URL', 'https://issues.redhat.com'),
                        help='Jira base URL')
    parser.add_argument('--email', default=os.getenv('JIRA_EMAIL', ''),
                        help='Jira email (optional for Red Hat Jira)')
    parser.add_argument('--token', default=os.getenv('JIRA_TOKEN', ''),
                        help='Jira API token (default: JIRA_TOKEN from .env)')

    args = parser.parse_args()

    if not args.token:
        print("❌ JIRA_TOKEN not set. Provide --token or set it in .env")
        return 1

    try:
        validator = JiraFeatureValidator(
            jira_url=args.jira_url,
            email=args.email,
            api_token=args.token,
            project_key="ROX",
            target_version=args.target_version,
        )
        validator.run_validation()
    except KeyboardInterrupt:
        print("\n⚠️  Validation interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()


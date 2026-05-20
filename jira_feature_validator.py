#!/usr/bin/env python3
"""
Jira Feature Template Validator

This script connects to a Jira organization using API token authentication,
filters features by target version (excluding issues in the Done status category, for example Closed),
and validates them against a required template structure.

Usage:
    python3 jira_feature_validator.py --target-version 5.0.0
    python3 jira_feature_validator.py --target-version 4.12.0
    python3 jira_feature_validator.py --target-version 5.0.0 --update-sheet   # also push CSV to Google Sheets
    python3 jira_feature_validator.py --target-version 5.0.0 --no-rice-comments   # skip Jira comments for missing RICE

Validates Reach, Impact, Confidence, Effort, and RICE Score (custom fields from ``JIRA_RICE_*`` or auto-discovery).
Issues with any missing RICE value get a comment @mentioning Product Manager and Assignee (Jira Cloud).

Each feature must carry **at least one** product-pillar Jira label:
``unified-workload-protection``, ``frictionless-security-runtime-observability``,
``ai-driven-vuln-risk-management``, or ``enterprise-scalability-support``
(same set as ``rox_feature_category_labels.py``). Version tags like ``5.0.0`` alone do not satisfy this.
"""

import os
import re
import json
import csv
import subprocess
import urllib.parse
import requests
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import argparse
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Prefer values from project .env over inherited shell env (avoids stale JIRA_* exports).
load_dotenv(Path(__file__).parent / ".env", override=True)

from jira_auth import is_jira_cloud_url, jira_api_token_from_env  # noqa: E402


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

    # Product pillar slugs (same as rox_feature_category_labels.py); exported in CSV as "Pillar classification".
    PILLAR_CLASSIFICATION_LABELS = (
        "unified-workload-protection",
        "frictionless-security-runtime-observability",
        "ai-driven-vuln-risk-management",
        "enterprise-scalability-support",
    )

    @staticmethod
    def pillar_classification_from_labels(labels: Optional[List[str]]) -> str:
        """Return pillar label(s) present on the issue, fixed order; empty if none."""
        if not labels:
            return ""
        label_set = {str(x).strip() for x in labels if x}
        found = [p for p in JiraFeatureValidator.PILLAR_CLASSIFICATION_LABELS if p in label_set]
        return " | ".join(found)

    def __init__(self, jira_url: str, email: str, api_token: str,
                 project_key: str = "ROX", target_version: str = "5.0.0"):
        self.jira_url = jira_url.rstrip('/')
        self.api_token = api_token
        self.email = email
        self.project_key = project_key
        self.target_version = target_version
        self.session = requests.Session()
        
        # Jira Cloud (atlassian.net) uses Basic Auth; Jira Server uses Bearer
        if is_jira_cloud_url(jira_url):
            self.session.auth = (email, api_token)
            self.session.headers.update({
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            })
        else:
            self.session.headers.update({
                'Authorization': f'Bearer {api_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            })

    def product_manager_field(self) -> str:
        """Custom field id for Product Manager (Cloud vs Server differ)."""
        fid = os.getenv("JIRA_PRODUCT_MANAGER_FIELD", "").strip()
        if fid:
            return fid
        return "customfield_10469" if is_jira_cloud_url(self.jira_url) else "customfield_12316752"

    def target_version_field(self) -> str:
        """Custom field id for Target Version (Cloud vs Server differ)."""
        fid = os.getenv("JIRA_TARGET_VERSION_FIELD", "").strip()
        if fid:
            return fid
        return "customfield_10855" if is_jira_cloud_url(self.jira_url) else "customfield_12319940"

    _rice_cache: Optional[Dict[str, str]] = None

    # Exact Jira field names to match for each RICE dimension.
    _RICE_FIELD_NAMES: Dict[str, List[str]] = {
        "reach": ["Reach"],
        "impact": ["Impact (migrated)", "Impact"],
        "confidence": ["Confidence"],
        "effort": ["Effort"],
    }

    def _discover_rice_fields(self) -> Dict[str, str]:
        """Auto-discover RICE custom field ids from Jira /rest/api/*/field."""
        api_version = getattr(self, "api_version", "3" if is_jira_cloud_url(self.jira_url) else "2")
        try:
            resp = self.session.get(f"{self.jira_url}/rest/api/{api_version}/field")
            resp.raise_for_status()
            all_fields = resp.json()
        except Exception as e:
            print(f"   ⚠️  Could not fetch Jira fields for RICE discovery: {e}")
            return {k: "" for k in self._RICE_FIELD_NAMES}

        name_to_id: Dict[str, str] = {}
        for f in all_fields:
            if f.get("custom"):
                name_to_id[f.get("name", "")] = f.get("id", "")

        result: Dict[str, str] = {}
        for rice_key, candidates in self._RICE_FIELD_NAMES.items():
            fid = ""
            for cand in candidates:
                if cand in name_to_id:
                    fid = name_to_id[cand]
                    break
            result[rice_key] = fid

        found = {k: v for k, v in result.items() if v}
        if found:
            print(f"   🔎 RICE fields discovered: {', '.join(f'{k}={v}' for k, v in found.items())}")
        return result

    def rice_field_ids(self) -> Dict[str, str]:
        """Jira custom field ids for RICE. Uses JIRA_RICE_* env vars, or auto-discovers from Jira."""
        if self._rice_cache is not None:
            return self._rice_cache

        env_ids = {
            "reach": os.getenv("JIRA_RICE_REACH_FIELD", "").strip(),
            "impact": os.getenv("JIRA_RICE_IMPACT_FIELD", "").strip(),
            "confidence": os.getenv("JIRA_RICE_CONFIDENCE_FIELD", "").strip(),
            "effort": os.getenv("JIRA_RICE_EFFORT_FIELD", "").strip(),
        }
        if all(env_ids.values()):
            self._rice_cache = env_ids
            return self._rice_cache

        discovered = self._discover_rice_fields()
        merged = {k: env_ids[k] or discovered.get(k, "") for k in env_ids}
        self._rice_cache = merged
        return self._rice_cache

    _rank_field_id: Optional[str] = None

    def rank_field_id(self) -> str:
        """Jira custom field id for Rank (LexoRank on Cloud). Env or match by field name."""
        env = (os.getenv("JIRA_RANK_FIELD") or "").strip()
        if env:
            return env
        if self._rank_field_id is not None:
            return self._rank_field_id
        api_version = getattr(self, "api_version", "3" if is_jira_cloud_url(self.jira_url) else "2")
        try:
            resp = self.session.get(f"{self.jira_url}/rest/api/{api_version}/field")
            resp.raise_for_status()
            for f in resp.json():
                if f.get("custom") and (f.get("name") or "").strip().lower() == "rank":
                    self._rank_field_id = f.get("id", "")
                    print(f"   🔎 Rank field: {self._rank_field_id}")
                    break
            else:
                self._rank_field_id = ""
        except Exception as e:
            print(f"   ⚠️  Could not discover Rank field: {e}")
            self._rank_field_id = ""
        return self._rank_field_id or ""

    def rice_score_field_id(self) -> str:
        """Jira custom field id for the **RICE Score** field. Env or match by field name in Jira."""
        env = (os.getenv("JIRA_RICE_SCORE_FIELD") or "").strip()
        if env:
            return env
        if getattr(self, "_rice_score_field_resolved", False):
            return getattr(self, "_rice_score_field_id", "")
        self._rice_score_field_resolved = True
        self._rice_score_field_id = ""
        api_version = getattr(self, "api_version", "3" if is_jira_cloud_url(self.jira_url) else "2")
        try:
            resp = self.session.get(f"{self.jira_url}/rest/api/{api_version}/field")
            resp.raise_for_status()
            name_to_id: Dict[str, str] = {}
            for f in resp.json():
                if f.get("custom"):
                    name_to_id[f.get("name", "")] = f.get("id", "")
            for cand in ("RICE Score", "Rice Score", "RICE score"):
                if cand in name_to_id:
                    self._rice_score_field_id = name_to_id[cand]
                    print(f"   🔎 RICE Score field: {self._rice_score_field_id}")
                    break
        except Exception as e:
            print(f"   ⚠️  Could not discover RICE Score field: {e}")
        return self._rice_score_field_id

    def get_issue_fields_param(self) -> str:
        base = (
            "summary,description,key,status,assignee,created,updated,labels,"
            f"{self.product_manager_field()},{self.target_version_field()}"
        )
        extra = [fid for fid in self.rice_field_ids().values() if fid]
        rs = self.rice_score_field_id()
        if rs and rs not in extra:
            extra.append(rs)
        if extra:
            base = f"{base},{','.join(extra)}"
        return base

    def test_connection(self) -> bool:
        """Test the Jira connection"""
        self.is_cloud = is_jira_cloud_url(self.jira_url)
        api_version = "3" if self.is_cloud else "2"
        try:
            response = self.session.get(f"{self.jira_url}/rest/api/{api_version}/myself")
            response.raise_for_status()
            user_data = response.json()
            print(f"✅ Connected to Jira as: {user_data.get('displayName', 'Unknown')}")
            print(f"   Using API version: {api_version}")
            self.api_version = api_version
            return True
        except requests.HTTPError as e:
            err_s = str(e)
            print(f"❌ Failed to connect to Jira: {e}")
            if "atlassian.net" in err_s and "/rest/api/2/" in err_s:
                print(
                    "   Atlassian Cloud was called with API v2 (wrong mode). Use the latest script, "
                    "check JIRA_BASE_URL in .env, and run `printenv JIRA_BASE_URL` — old shell exports "
                    "used to win before .env; this script now prefers .env."
                )
            resp = e.response
            if resp is not None and resp.status_code == 401 and self.is_cloud:
                if not (self.email or "").strip():
                    print(
                        "   Hint: set JIRA_EMAIL to the Atlassian account email paired with your API token."
                    )
                else:
                    print(
                        "   Hint: confirm JIRA_TOKEN (or JIRA_API_TOKEN) is a current API token for that email; "
                        "revoke old tokens and create a new one if unsure."
                    )
            return False
        except Exception as e:
            print(f"❌ Failed to connect to Jira: {e}")
            return False
    
    def get_features(self) -> List[Dict]:
        """Retrieve features for the configured target version from Jira."""
        print(f"🔍 Searching for {self.target_version} features...")
        
        jql_parts = [
            f'project = {self.project_key}',
            f'"Target Version" = "{self.target_version}"',
            'type = feature',
            'statusCategory != Done',
        ]
        
        jql = ' AND '.join(jql_parts)
        
        print(f"🔍 Using JQL: {jql}")
        
        features = []
        max_results = 50
        fields_param = self.get_issue_fields_param()
        is_cloud = getattr(self, "is_cloud", is_jira_cloud_url(self.jira_url))
        api_version = getattr(self, 'api_version', '3')
        
        try:
            if is_cloud:
                search_url = f"{self.jira_url}/rest/api/3/search/jql"
                next_token = None
                while True:
                    params: dict = {
                        'jql': jql, 'maxResults': max_results,
                        'fields': fields_param,
                    }
                    if next_token:
                        params['nextPageToken'] = next_token
                    response = self.session.get(search_url, params=params)
                    response.raise_for_status()
                    data = response.json()
                    features.extend(data.get('issues', []))
                    if data.get('isLast', True):
                        break
                    next_token = data.get('nextPageToken')
                    if not next_token:
                        break
            else:
                search_url = f"{self.jira_url}/rest/api/{api_version}/search"
                start_at = 0
                while True:
                    response = self.session.get(search_url, params={
                        'jql': jql, 'startAt': start_at,
                        'maxResults': max_results, 'fields': fields_param,
                    })
                    response.raise_for_status()
                    data = response.json()
                    issues = data.get('issues', [])
                    features.extend(issues)
                    if len(issues) < max_results:
                        break
                    start_at += max_results
        except Exception as e:
            print(f"❌ Error fetching features: {e}")
        
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

    @staticmethod
    def description_to_plain_text(description) -> str:
        """Convert Jira description to plain text (handles ADF on Jira Cloud)."""
        if description is None:
            return ""
        if isinstance(description, str):
            return description
        if isinstance(description, dict):
            if description.get("type") == "doc":
                parts: List[str] = []

                def walk(node: dict) -> None:
                    if not isinstance(node, dict):
                        return
                    if node.get("type") == "text" and "text" in node:
                        parts.append(node["text"])
                    for child in node.get("content") or []:
                        walk(child)
                    if node.get("type") in ("paragraph", "heading", "bulletList", "orderedList"):
                        parts.append("\n")

                walk(description)
                return "".join(parts).strip()
            return str(description)
        return str(description)

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
        fields = feature.get('fields', {})
        key = feature.get('key', 'Unknown')
        summary = fields.get('summary', 'No summary')
        raw_desc = fields.get('description', '')
        description = self.description_to_plain_text(raw_desc)

        rice_missing = self.missing_rice_dimensions(fields)
        raw_labels = fields.get("labels") or []
        issue_labels = raw_labels if isinstance(raw_labels, list) else []
        pillar_class = self.pillar_classification_from_labels(issue_labels)
        pillar_labels_valid = bool(pillar_class.strip())

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
        
        rice_valid = len(rice_missing) == 0
        return {
            'key': key,
            'summary': summary,
            'validation_results': validation_results,
            'required_missing': required_missing,
            'optional_missing': optional_missing,
            'rice_missing': rice_missing,
            'rice_valid': rice_valid,
            'pillar_labels_valid': pillar_labels_valid,
            'overall_valid': (required_missing == 0) and rice_valid and pillar_labels_valid,
        }
    
    def _extract_display_name(self, field_value) -> str:
        if not field_value:
            return ""
        if isinstance(field_value, dict):
            return (
                field_value.get("displayName")
                or field_value.get("name")
                or field_value.get("emailAddress")
                or field_value.get("accountId")
                or ""
            )
        return str(field_value)

    def _extract_version_name(self, field_value) -> str:
        if not field_value:
            return ""
        if isinstance(field_value, list):
            names: List[str] = []
            for item in field_value:
                if isinstance(item, dict):
                    n = item.get("name")
                    if n:
                        names.append(n)
                elif item:
                    names.append(str(item))
            return " | ".join(names)
        if isinstance(field_value, dict):
            return field_value.get("name", str(field_value))
        return str(field_value)

    def missing_rice_dimensions(self, fields: Dict[str, Any]) -> List[str]:
        """Return human-readable labels for RICE dimensions that are unset (empty)."""
        rice_ids = self.rice_field_ids()
        dim_labels = [
            ("reach", "Reach"),
            ("impact", "Impact"),
            ("confidence", "Confidence"),
            ("effort", "Effort"),
        ]
        missing: List[str] = []
        for key, label in dim_labels:
            fid = rice_ids.get(key) or ""
            if not fid:
                continue
            raw = fields.get(fid)
            if not self._extract_custom_scalar(raw).strip():
                missing.append(label)
        score_id = self.rice_score_field_id()
        if score_id:
            raw = fields.get(score_id)
            if not self._extract_custom_scalar(raw).strip():
                missing.append("RICE Score")
        return missing

    def add_issue_comment(self, issue_key: str, *, adf_body: Optional[Dict] = None,
                          plain_body: Optional[str] = None) -> bool:
        """Post a comment on an issue. Cloud: ADF; Server: plain string body."""
        api_version = getattr(self, "api_version", "3" if is_jira_cloud_url(self.jira_url) else "2")
        url = f"{self.jira_url}/rest/api/{api_version}/issue/{issue_key}/comment"
        if is_jira_cloud_url(self.jira_url) and adf_body is not None:
            payload = {"body": adf_body}
        elif plain_body is not None:
            if api_version == "3" and is_jira_cloud_url(self.jira_url):
                payload = {
                    "body": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": plain_body}],
                            }
                        ],
                    }
                }
            else:
                payload = {"body": plain_body}
        else:
            return False
        try:
            r = self.session.post(url, json=payload, timeout=30)
            if r.status_code in (200, 201):
                return True
            print(f"   ⚠️  Comment on {issue_key} failed: {r.status_code} {r.text[:400]}")
        except Exception as e:
            print(f"   ⚠️  Comment on {issue_key} failed: {e}")
        return False

    def _adf_rice_missing_comment(
        self,
        missing_labels: List[str],
        pm_user: Optional[Dict],
        assignee_user: Optional[Dict],
    ) -> Dict[str, Any]:
        """Build ADF document with @mentions for Product Manager and Assignee."""
        parts: List[Dict[str, Any]] = []
        intro = (
            "[Automated ROX template validation] Missing RICE field(s): "
            + ", ".join(missing_labels)
            + ". "
        )
        parts.append({"type": "text", "text": intro})

        def append_mention(
            user: Optional[Dict],
            role: str,
            empty_hint: str,
        ) -> None:
            if user and isinstance(user, dict):
                aid = user.get("accountId")
                dn = user.get("displayName") or role
                if aid:
                    parts.append(
                        {
                            "type": "mention",
                            "attrs": {"id": aid, "text": f"@{dn}"},
                        }
                    )
                    parts.append({"type": "text", "text": " "})
                    return
            parts.append({"type": "text", "text": f"{role}: {empty_hint} "})

        append_mention(pm_user, "Product Manager", "(not set)")
        append_mention(assignee_user, "Assignee", "(unassigned)")
        parts.append(
            {
                "type": "text",
                "text": "Please fill in the missing RICE values on this feature.",
            }
        )
        return {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": parts}],
        }

    def post_rice_missing_comment(self, feature: Dict, missing_labels: List[str]) -> bool:
        """Notify Product Manager and Assignee via issue comment when RICE fields are incomplete."""
        issue_key = feature.get("key")
        if not issue_key or not missing_labels:
            return False
        fields = feature.get("fields") or {}
        pm_field = self.product_manager_field()
        pm_raw = fields.get(pm_field)
        pm_user = pm_raw if isinstance(pm_raw, dict) else None
        assignee_raw = fields.get("assignee")
        assignee_user = assignee_raw if isinstance(assignee_raw, dict) else None

        if is_jira_cloud_url(self.jira_url):
            adf = self._adf_rice_missing_comment(missing_labels, pm_user, assignee_user)
            ok = self.add_issue_comment(issue_key, adf_body=adf)
        else:
            pm_name = self._extract_display_name(pm_raw) or "(not set)"
            asg_name = self._extract_display_name(assignee_raw) or "(unassigned)"
            plain = (
                "[Automated ROX template validation] Missing RICE field(s): "
                + ", ".join(missing_labels)
                + f".\n\nProduct Manager: {pm_name}\nAssignee: {asg_name}\n\n"
                "Please fill in the missing RICE values on this feature."
            )
            ok = self.add_issue_comment(issue_key, plain_body=plain)
        if ok:
            print(f"   💬 Comment posted on {issue_key} (missing RICE: {', '.join(missing_labels)})")
        return ok

    @staticmethod
    def _extract_custom_scalar(field_value) -> str:
        """String for CSV export from a number, string, or Jira option-style custom field."""
        if field_value is None:
            return ""
        if isinstance(field_value, bool):
            return "true" if field_value else "false"
        if isinstance(field_value, int):
            return str(field_value)
        if isinstance(field_value, float):
            return str(int(field_value)) if field_value == int(field_value) else str(field_value)
        if isinstance(field_value, str):
            return field_value.strip()
        if isinstance(field_value, dict):
            if "value" in field_value and field_value["value"] is not None:
                v = field_value["value"]
                if isinstance(v, (int, float)):
                    return str(int(v)) if isinstance(v, float) and v == int(v) else str(v)
                return str(v).strip()
            if "name" in field_value:
                return str(field_value["name"]).strip()
        if isinstance(field_value, list) and field_value:
            parts = [JiraFeatureValidator._extract_custom_scalar(x) for x in field_value]
            return " | ".join(p for p in parts if p)
        return str(field_value).strip()

    def generate_compliance_csv(self, features: List[Dict],
                                validation_results: List[Dict]) -> str:
        """Generate a single CSV combining feature info and per-section compliance."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        version_tag = self.target_version.replace(".", "_")
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        csv_path = output_dir / f"rox_{version_tag}_compliance_{timestamp}.csv"

        section_headers = [s.header for s in self.TEMPLATE_SECTIONS]
        rice_cols = ["Reach", "Impact", "Confidence", "Effort", "RICE Score"]
        fieldnames = [
            "Key", "Summary", "Status", "Assignee", "Product Manager",
            "Target Version", "Labels", "Pillar classification",
            "Has pillar label",
            *rice_cols,
            "Compliant", "Required Missing", "RICE Missing",
        ] + section_headers

        feature_map = {f.get("key"): f for f in features}
        rice_ids = self.rice_field_ids()
        rice_score_id = self.rice_score_field_id()

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
                    "Product Manager": self._extract_display_name(
                        fields.get(self.product_manager_field())
                    ),
                    "Target Version": self._extract_version_name(
                        fields.get(self.target_version_field())
                    ),
                    "Labels": " | ".join(fields.get("labels") or []),
                    "Pillar classification": self.pillar_classification_from_labels(
                        fields.get("labels") or []
                    ),
                    "Has pillar label": "Yes" if result.get("pillar_labels_valid") else "No",
                    "Reach": self._extract_custom_scalar(
                        fields.get(rice_ids["reach"]) if rice_ids["reach"] else None
                    ),
                    "Impact": self._extract_custom_scalar(
                        fields.get(rice_ids["impact"]) if rice_ids["impact"] else None
                    ),
                    "Confidence": self._extract_custom_scalar(
                        fields.get(rice_ids["confidence"]) if rice_ids["confidence"] else None
                    ),
                    "Effort": self._extract_custom_scalar(
                        fields.get(rice_ids["effort"]) if rice_ids["effort"] else None
                    ),
                    "RICE Score": self._extract_custom_scalar(
                        fields.get(rice_score_id) if rice_score_id else None
                    ),
                    "Compliant": "Yes" if result["overall_valid"] else "No",
                    "Required Missing": result["required_missing"],
                    "RICE Missing": ", ".join(result.get("rice_missing") or []),
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
    
    def run_validation(self, post_rice_comments: bool = True) -> Optional[str]:
        """Run the complete validation process. Returns CSV path on success.

        When ``post_rice_comments`` is True (default), posts a Jira comment on issues
        with incomplete RICE fields, @mentioning Product Manager and Assignee on Cloud.
        """
        print(f"🚀 Starting ROX {self.target_version} Feature Analysis and Template Validation")
        print("=" * 60)
        _cloud = is_jira_cloud_url(self.jira_url)
        print(
            f"🔗 Jira: {self.jira_url}  "
            f"({'Cloud — API v3 + email/token' if _cloud else 'Server/Data Center — API v2 + Bearer'})"
        )

        if not self.test_connection():
            return None

        features = self.get_features()
        if not features:
            print(f"⚠️  No {self.target_version} features found")
            return None

        print("🔍 Validating template, RICE fields, and product-pillar labels...")
        validation_results = []
        for i, feature in enumerate(features, 1):
            key = feature.get("key", "Unknown")
            print(f"   Processing {i}/{len(features)}: {key}")
            rep = self.validate_feature(feature)
            validation_results.append(rep)
            if post_rice_comments and rep.get("rice_missing"):
                self.post_rice_missing_comment(feature, rep["rice_missing"])

        csv_path = self.generate_compliance_csv(features, validation_results)

        total = len(validation_results)
        compliant = sum(1 for r in validation_results if r["overall_valid"])
        rice_incomplete = sum(1 for r in validation_results if not r.get("rice_valid", True))
        pillar_missing = sum(1 for r in validation_results if not r.get("pillar_labels_valid", True))
        print(f"\n{'=' * 60}")
        print("📊 QUICK SUMMARY")
        print(f"{'=' * 60}")
        print(f"Features analyzed: {total}")
        print(f"Compliant (template + RICE + pillar label): {compliant} ({compliant / total * 100:.1f}%)")
        print(f"Non-compliant: {total - compliant} ({(total - compliant) / total * 100:.1f}%)")
        print(f"Incomplete RICE fields: {rice_incomplete}")
        print(f"Missing product-pillar label: {pillar_missing}")
        print(f"\n📄 Report: {csv_path}")
        return csv_path


DEFAULT_SPREADSHEET_ID = "1pLLm_1VrQHFpWCg7Z6JZXJGInlMtA9vB1I9Hsw97Fy8"
DEFAULT_SHEET_NAME = "5.0 Plan"


def _jira_issue_hyperlink_formula(jira_base_url: str, issue_key: str) -> str:
    """Google Sheets formula: show issue key, link to Jira browse URL."""
    key = (issue_key or "").strip()
    base = (jira_base_url or "").rstrip("/")
    url = f"{base}/browse/{key}" if key else base
    url_esc = url.replace('"', '""')
    key_esc = key.replace('"', '""')
    return f'=HYPERLINK("{url_esc}","{key_esc}")'


def _get_gcloud_access_token() -> Optional[str]:
    """Get a Google access token from gcloud CLI (requires --enable-gdrive-access)."""
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        ).strip()
        return token if token else None
    except Exception:
        return None


def _ensure_google_sheet_tab(
    spreadsheet_id: str,
    sheet_name: str,
    headers: Dict[str, str],
) -> bool:
    """Create a worksheet tab if it does not exist. Returns False on API failure."""
    title = (sheet_name or "").strip()
    if not title:
        return False
    meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}?fields=sheets.properties"
    try:
        r = requests.get(meta_url, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"⚠️  Could not read spreadsheet metadata: {r.status_code} {r.text[:200]}")
            return False
        for sh in r.json().get("sheets") or []:
            props = sh.get("properties") or {}
            if props.get("title") == title:
                return True
    except Exception as e:
        print(f"⚠️  Spreadsheet metadata error: {e}")
        return False

    batch_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
    body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
    try:
        r2 = requests.post(batch_url, headers=headers, json=body, timeout=30)
        if r2.status_code == 200:
            print(f"   📑 Created sheet tab: {title!r}")
            return True
        print(f"⚠️  Could not create tab {title!r}: {r2.status_code} {r2.text[:300]}")
    except Exception as e:
        print(f"⚠️  addSheet failed: {e}")
    return False


def upload_csv_to_google_sheet(
    csv_path: str,
    spreadsheet_id: str,
    sheet_name: str,
    *,
    jira_base_url: Optional[str] = None,
) -> bool:
    """Replace a Google Sheets tab with the contents of a CSV file.

    Uses the gcloud CLI access token (requires prior
    ``gcloud auth login --enable-gdrive-access``).

    The ``Key`` column is written as a ``HYPERLINK`` to
    ``{jira_base_url}/browse/<KEY>`` (default base from ``JIRA_BASE_URL`` or
    ``https://redhat.atlassian.net``). Requires ``valueInputOption=USER_ENTERED``
    so Sheets parses formulas.
    """
    token = _get_gcloud_access_token()
    if not token:
        print(
            "⚠️  Could not get gcloud access token for Google Sheets.\n"
            "   Run: gcloud auth login --enable-gdrive-access"
        )
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    base = "https://sheets.googleapis.com/v4/spreadsheets"

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        print("⚠️  CSV is empty, skipping Sheets upload")
        return False

    browse_base = (jira_base_url or os.getenv("JIRA_BASE_URL", "https://redhat.atlassian.net")).strip()
    if browse_base:
        header = rows[0]
        try:
            key_col = header.index("Key")
        except ValueError:
            key_col = None
        if key_col is not None:
            for i in range(1, len(rows)):
                while len(rows[i]) <= key_col:
                    rows[i].append("")
                raw_key = (rows[i][key_col] or "").strip()
                if raw_key:
                    rows[i][key_col] = _jira_issue_hyperlink_formula(browse_base, raw_key)

    encoded_sheet = urllib.parse.quote(sheet_name, safe="")
    if not _ensure_google_sheet_tab(spreadsheet_id, sheet_name, headers):
        return False
    # Clear the whole tab (A:ZZ) so manual edits anywhere are removed; sheet names
    # with spaces must be URL-encoded or the clear request can miss the tab.
    clear_range = f"{encoded_sheet}!A:ZZ"
    clear_url = f"{base}/{spreadsheet_id}/values/{clear_range}:clear"
    resp = requests.post(clear_url, headers=headers)
    if resp.status_code == 403:
        print(
            "⚠️  Google Sheets 403 — run: gcloud auth login --enable-gdrive-access"
        )
        return False
    if resp.status_code not in (200, 201):
        print(f"⚠️  Clear failed: {resp.status_code} {resp.text[:200]}")
        return False

    # Write new data (USER_ENTERED so Key column HYPERLINK formulas apply)
    write_url = (
        f"{base}/{spreadsheet_id}/values/{encoded_sheet}!A1"
        f"?valueInputOption=USER_ENTERED"
    )
    resp = requests.put(write_url, headers=headers, json={"values": rows})
    if resp.status_code == 200:
        result = resp.json()
        print(
            f"✅ Google Sheet updated: {result.get('updatedRows')} rows × "
            f"{result.get('updatedColumns')} cols"
        )
        return True

    print(f"⚠️  Write failed: {resp.status_code} {resp.text[:200]}")
    return False


def main():
    parser = argparse.ArgumentParser(
        description='Validate ROX features against the required template structure'
    )
    parser.add_argument('--target-version', default='5.0.0',
                        help='Target version to validate (default: 5.0.0)')
    parser.add_argument(
        '--jira-url',
        default=os.getenv('JIRA_BASE_URL', 'https://redhat.atlassian.net'),
        help='Jira base URL (default: RH Cloud; set JIRA_BASE_URL=https://issues.redhat.com for legacy PAT-only)',
    )
    parser.add_argument('--email', default=os.getenv('JIRA_EMAIL', ''),
                        help='Atlassian account email (required for *.atlassian.net; optional for Bearer/Jira Server)')
    parser.add_argument('--token', default=jira_api_token_from_env(),
                        help='Jira API token (default: JIRA_TOKEN or JIRA_API_TOKEN from .env)')
    parser.add_argument(
        '--no-rice-comments',
        action='store_true',
        help='Do not post Jira comments when RICE fields are missing (default: comment + @mention PM & Assignee)',
    )
    parser.add_argument('--update-sheet', action='store_true',
                        help='Upload compliance CSV to Google Sheets after generation')
    parser.add_argument('--sheet-id',
                        default=os.getenv('GOOGLE_SHEET_ID', DEFAULT_SPREADSHEET_ID),
                        help='Google Spreadsheet ID to update')
    parser.add_argument('--sheet-name',
                        default=os.getenv('GOOGLE_SHEET_NAME', DEFAULT_SHEET_NAME),
                        help='Sheet tab name to replace (default: "5.0 Plan")')

    args = parser.parse_args()

    if not args.token:
        print("❌ JIRA_TOKEN / JIRA_API_TOKEN not set. Provide --token or set one in .env")
        return 1

    if is_jira_cloud_url(args.jira_url) and not (args.email or "").strip():
        print(
            "❌ Jira Cloud (atlassian.net) requires JIRA_EMAIL: your Atlassian account email "
            "(the same account you used to create the API token). Set JIRA_EMAIL in .env or pass --email."
        )
        return 1

    try:
        validator = JiraFeatureValidator(
            jira_url=args.jira_url,
            email=args.email,
            api_token=args.token,
            project_key="ROX",
            target_version=args.target_version,
        )
        csv_path = validator.run_validation(post_rice_comments=not args.no_rice_comments)

        if csv_path and args.update_sheet:
            print(f"\n📤 Uploading to Google Sheets...")
            upload_csv_to_google_sheet(
                csv_path,
                args.sheet_id,
                args.sheet_name,
                jira_base_url=args.jira_url.rstrip("/"),
            )
    except KeyboardInterrupt:
        print("\n⚠️  Validation interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()


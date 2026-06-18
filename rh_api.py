"""Shared Red Hat Customer Portal / Hydra API helpers."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import requests

RH_SSO_TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
RH_HYDRA_CASE_URL = "https://access.redhat.com/hydra/rest/cases"


def get_rh_access_token(offline_token: str) -> Optional[str]:
    """Exchange a Red Hat offline token for a short-lived access token."""
    try:
        resp = requests.post(
            RH_SSO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": "rhsm-api",
                "refresh_token": offline_token,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
        print(f"  Red Hat SSO token exchange failed: {resp.status_code}")
    except Exception as e:
        print(f"  Red Hat SSO error: {e}")
    return None


def fetch_case_account_name(
    case_number: str,
    access_token: str,
    cache: Dict[str, str],
) -> str:
    """Look up the account/customer name for a support case via the Hydra API."""
    if case_number in cache:
        return cache[case_number]
    try:
        resp = requests.get(
            f"{RH_HYDRA_CASE_URL}/{case_number}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            account = (
                data.get("accountName")
                or data.get("account", {}).get("name", "")
                or data.get("contactName", "")
            )
            cache[case_number] = account
            return account
        elif resp.status_code == 404:
            cache[case_number] = ""
        else:
            print(f"  Hydra API {resp.status_code} for case {case_number}")
    except Exception:
        pass
    cache[case_number] = ""
    return ""


def extract_sfdc_case_ids(
    rfe_fields: Dict,
    rfe_key: str,
    jira_url: str,
    session: requests.Session,
    api_version: str,
) -> List[str]:
    """Extract SFDC case IDs from custom fields and remote links of an RFE."""
    seen: set = set()
    case_ids: list = []

    sfdc_field = rfe_fields.get("customfield_12313441")
    if sfdc_field:
        for case_num in re.split(r"[,\s;|]+", str(sfdc_field).strip()):
            case_num = case_num.strip()
            if case_num and case_num not in seen:
                seen.add(case_num)
                case_ids.append(case_num)

    try:
        resp = session.get(
            f"{jira_url}/rest/api/{api_version}/issue/{rfe_key}/remotelink",
            timeout=15,
        )
        if resp.status_code == 200:
            for link in resp.json():
                obj = link.get("object", {}) or {}
                url_str = obj.get("url", "") or ""
                title = obj.get("title", "") or ""
                summary = obj.get("summary", "") or ""
                combined = f"{url_str} {title} {summary}"
                if not re.search(r"salesforce|force\.com|sfdc", combined, re.IGNORECASE):
                    continue
                for text in [url_str, title, summary]:
                    m = re.search(r"500[a-zA-Z0-9]{12,15}", text)
                    if m and m.group() not in seen:
                        seen.add(m.group())
                        case_ids.append(m.group())
                        break
                else:
                    for text in [url_str, title, summary]:
                        m = re.search(r"\b(\d{7,10})\b", text)
                        if m and m.group() not in seen:
                            seen.add(m.group())
                            case_ids.append(m.group())
                            break
    except Exception:
        pass

    return case_ids

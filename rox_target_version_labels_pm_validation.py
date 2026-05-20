#!/usr/bin/env python3
"""
ROX target-version label sync, missing Product Manager report, and template validation.

For all ROX Features with a given Target Version (default 5.0.0):
  - Ensures the version label exists on the issue (e.g. "5.0.0") — adds via Jira API if missing
  - Writes a full CSV: labels, PM, template compliance (same rules as jira_feature_validator.py)
  - Writes a CSV of only issues missing Product Manager
  - Optional: query NotebookLM (ACS RICE framework) for Reach, Impact, Confidence, Effort per feature

Uses JIRA_TOKEN (or JIRA_API_TOKEN) / JIRA_BASE_URL / JIRA_EMAIL from .env (same as jira_feature_validator.py).
NotebookLM RICE: prefers **nlm** (``pip install notebooklm-mcp-cli`` + ``nlm login``); else **notebooklm-py**
(``pip install 'notebooklm-py[browser]'`` + ``notebooklm login``). By default each line is **key + Jira summary**
(so issues missing from uploaded sources—e.g. new ROX tickets—still have context). Use ``NOTEBOOKLM_RICE_SUMMARY_MAX=0``
for keys-only prompts. Pass ``--rice-jira-context`` to also embed description excerpts from Jira.

Usage:
    python3 rox_target_version_labels_pm_validation.py
    python3 rox_target_version_labels_pm_validation.py --target-version 5.0.0
    python3 rox_target_version_labels_pm_validation.py --dry-run   # no label updates
    python3 rox_target_version_labels_pm_validation.py --notebooklm-rice
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from jira_auth import jira_api_token_from_env  # noqa: E402
from jira_feature_validator import JiraFeatureValidator  # noqa: E402
from notebooklm_upload import find_notebook_id_by_title  # noqa: E402

try:
    from notebooklm import NotebookLMClient

    NOTEBOOKLM_AVAILABLE = True
except ImportError:
    NOTEBOOKLM_AVAILABLE = False

DEFAULT_NOTEBOOK_NAME = "The Big Notebook for RHACS Product Management"
RICE_KEYS = ("reach", "impact", "confidence", "effort")
RICE_COLUMNS = ["Reach", "Impact", "Confidence", "Effort"]


def add_label_to_issue(
    session,
    jira_url: str,
    api_version: str,
    issue_key: str,
    label: str,
) -> bool:
    """Add a label using Jira REST update semantics. Returns True on success."""
    url = f"{jira_url.rstrip('/')}/rest/api/{api_version}/issue/{issue_key}"
    payload = {"update": {"labels": [{"add": label}]}}
    resp = session.put(url, json=payload)
    if resp.status_code in (200, 204):
        return True
    print(f"   ⚠️  {issue_key}: could not add label — {resp.status_code} {resp.text[:200]}")
    return False


def _feature_description_excerpt(
    validator: JiraFeatureValidator, feature: Dict, max_chars: int = 1800,
) -> str:
    if max_chars <= 0:
        return ""
    raw = feature.get("fields", {}).get("description", "")
    text = JiraFeatureValidator.description_to_plain_text(raw)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


def _parse_rice_json_array(answer: str) -> List[Dict[str, Any]]:
    if not answer or not answer.strip():
        return []
    text = answer.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _normalize_rice_row(obj: Any) -> Optional[Tuple[str, Dict[str, str]]]:
    if not isinstance(obj, dict):
        return None
    key = obj.get("key") or obj.get("Key")
    if not key or not isinstance(key, str):
        return None
    key = key.strip().upper()
    out: Dict[str, str] = {}
    for k in RICE_KEYS:
        v = obj.get(k) if k in obj else obj.get(k.capitalize())
        if v is None:
            out[k] = ""
        elif isinstance(v, (int, float)):
            out[k] = str(int(v)) if isinstance(v, float) and v == int(v) else str(v)
        else:
            s = str(v).strip()
            out[k] = s
    return key, out


def _rice_summary_max_keys_mode() -> int:
    """Max chars of Jira summary per issue when not using full ``--rice-jira-context``. 0 = omit summaries."""
    raw = (os.getenv("NOTEBOOKLM_RICE_SUMMARY_MAX") or "500").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 500


def _rice_summary_line(feat: Dict, max_chars: int) -> str:
    s = ((feat.get("fields") or {}).get("summary") or "").strip()
    s = re.sub(r"\s+", " ", s)
    if max_chars > 0 and len(s) > max_chars:
        s = s[: max_chars - 3] + "..."
    return s


def _build_rice_batch_prompt(
    batch: List[Dict],
    validator: JiraFeatureValidator,
    target_version: str,
    desc_max_chars: int = 1800,
    keys_only: bool = True,
) -> str:
    lines = [
        "You are assisting with product prioritization using the **ACS RICE Scoring and "
        "Prioritization Framework** defined in this notebook's sources (The Big Notebook "
        "for RHACS Product Management).",
        "",
        f"These are ROX Jira **Features** with Target Version **{target_version}**.",
        "",
    ]
    sum_max = _rice_summary_max_keys_mode() if keys_only else 0
    if keys_only:
        if sum_max > 0:
            lines.extend([
                "Below each line is **Jira issue key — summary** (summary is copied from Jira for this request).",
                "Use **this notebook's uploaded sources** for the RICE framework and for **extra** context when a key "
                "also appears there; if a key is missing from sources, rely on the inline summary.",
                "",
                "For EACH feature, assign integer scores **exactly as your framework defines** for "
                "Reach, Impact, Confidence, and Effort (same scales and meanings as in the notebook).",
                "",
            ])
        else:
            lines.extend([
                "Below are **only Jira issue keys**. Use **this notebook's uploaded sources** to "
                "find each feature's full context (summary, description, etc.) by key.",
                "",
                "For EACH key, assign integer scores **exactly as your framework defines** for "
                "Reach, Impact, Confidence, and Effort (same scales and meanings as in the notebook).",
                "",
            ])
    else:
        lines.extend([
            "For EACH feature below, assign integer scores **exactly as your framework defines** "
            "for Reach, Impact, Confidence, and Effort (use the same scales and meanings as in "
            "the notebook — do not invent a different RICE definition).",
            "",
        ])
    lines.extend([
        "Respond with **ONLY** a valid JSON array (no markdown code fences, no commentary). "
        "One object per feature, **in the same numbered order** as below. Each object:",
        '{"key":"ROX-nnnn","reach":<int>,"impact":<int>,"confidence":<int>,"effort":<int>}',
        "",
        "Features:",
    ])
    for i, feat in enumerate(batch, 1):
        k = feat.get("key", "")
        if keys_only:
            if sum_max > 0:
                summ = _rice_summary_line(feat, sum_max)
                lines.append(f"{i}. {k} — {summ}" if summ else f"{i}. {k}")
            else:
                lines.append(f"{i}. {k}")
            continue
        summ = (feat.get("fields") or {}).get("summary") or ""
        body = _feature_description_excerpt(validator, feat, max_chars=desc_max_chars)
        lines.append(f'{i}. {k} — {summ}')
        if body:
            lines.append(f"   Description: {body}")
        lines.append("")
    return "\n".join(lines).strip()


def _merge_rice_answer_for_batch(
    batch: List[Dict],
    answer: str,
    merged: Dict[str, Dict[str, str]],
) -> None:
    """Parse JSON RICE array from model answer and merge into ``merged`` by issue key."""
    parsed = _parse_rice_json_array(answer)
    by_key: Dict[str, Dict[str, str]] = {}
    for item in parsed:
        norm = _normalize_rice_row(item)
        if norm:
            ik, row = norm
            by_key[ik] = row

    for i, feat in enumerate(batch):
        k = feat.get("key")
        if not k:
            continue
        ku = k.strip().upper()
        row = by_key.get(ku)
        if row is None and i < len(parsed):
            norm = _normalize_rice_row(parsed[i])
            if norm:
                row = norm[1]
        if row is None:
            print(f"   ⚠️  No RICE parsed for {k}; answer preview: {answer[:120]!r}...")
            row = {x: "" for x in RICE_KEYS}
        merged[k] = row


def _nlm_query_notebook_answer(
    notebook_id: str, question: str, timeout_sec: int = 600,
) -> tuple[str, str]:
    """Run ``nlm query notebook``. Returns (answer_text, error_message). error_message is \"\" on success."""
    r = subprocess.run(
        [
            "nlm",
            "query",
            "notebook",
            notebook_id,
            question,
            "--timeout",
            str(timeout_sec),
        ],
        capture_output=True,
        text=True,
        timeout=min(timeout_sec + 120, 900),
    )
    raw_err = (r.stderr or r.stdout or "").strip()
    if r.returncode != 0:
        err_msg = raw_err
        try:
            ej = json.loads(raw_err)
            if isinstance(ej, dict):
                err_msg = str(ej.get("error", raw_err))
        except json.JSONDecodeError:
            pass
        print(f"   ⚠️  nlm query failed (exit {r.returncode}): {err_msg[:450]}")
        return "", err_msg
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(f"   ⚠️  nlm query: stdout is not JSON: {r.stdout[:200]!r}")
        return "", "invalid stdout"
    val = data.get("value") or {}
    if isinstance(val, dict) and val.get("status") == "error":
        err_msg = str(val.get("error", "unknown error"))
        print(f"   ⚠️  nlm query API error: {err_msg[:450]}")
        return "", err_msg
    ans = val.get("answer", "")
    if isinstance(ans, str):
        return ans, ""
    if ans is not None:
        return json.dumps(ans), ""
    return "", ""


def _nlm_is_invalid_argument(err: str) -> bool:
    if not err:
        return False
    e = err.lower()
    return "invalid_argument" in e or "invalid argument" in e


def _rice_desc_max_nlm(override: Optional[int]) -> int:
    if override is not None:
        return max(0, override)
    return max(0, int(os.getenv("NOTEBOOKLM_RICE_DESC_MAX", "600")))


def _rice_desc_max_py(override: Optional[int]) -> int:
    if override is not None:
        return max(0, override)
    return max(0, int(os.getenv("NOTEBOOKLM_RICE_DESC_MAX_PY", "1800")))


def _rice_prompt_keys_only(rice_jira_context_flag: bool) -> bool:
    """Default: keys only (notebook sources). Opt in with flag or NOTEBOOKLM_RICE_INCLUDE_JIRA_CONTEXT."""
    if rice_jira_context_flag:
        return False
    env = (os.getenv("NOTEBOOKLM_RICE_INCLUDE_JIRA_CONTEXT") or "").strip().lower()
    if env in ("1", "true", "yes"):
        return False
    return True


def _nlm_try_batch_rice(
    nid: str,
    batch: List[Dict],
    validator: JiraFeatureValidator,
    target_version: str,
    merged: Dict[str, Dict[str, str]],
    timeout: int,
    desc_max: int,
    keys_only: bool,
    depth: int = 0,
) -> None:
    """One nlm query; on INVALID_ARGUMENT split batch; optionally shorten Jira excerpt retries."""
    if not batch:
        return
    prompt = _build_rice_batch_prompt(
        batch,
        validator,
        target_version,
        desc_max_chars=desc_max,
        keys_only=keys_only,
    )
    answer, err = _nlm_query_notebook_answer(nid, prompt, timeout_sec=timeout)
    if answer.strip():
        _merge_rice_answer_for_batch(batch, answer, merged)
        return
    invalid = _nlm_is_invalid_argument(err)
    if invalid and len(batch) > 1:
        mid = len(batch) // 2
        pad = "  " * min(depth + 1, 5)
        print(f"   {pad}↳ Split batch ({len(batch)} → {mid}+{len(batch) - mid}) after INVALID_ARGUMENT")
        _nlm_try_batch_rice(
            nid,
            batch[:mid],
            validator,
            target_version,
            merged,
            timeout,
            desc_max,
            keys_only,
            depth + 1,
        )
        _nlm_try_batch_rice(
            nid,
            batch[mid:],
            validator,
            target_version,
            merged,
            timeout,
            desc_max,
            keys_only,
            depth + 1,
        )
        return
    if not keys_only and invalid and len(batch) == 1 and desc_max > 250:
        k = batch[0].get("key", "?")
        print(f"   ↳ Retry {k} with shorter excerpt ({desc_max} → 250 chars)")
        _nlm_try_batch_rice(
            nid, batch, validator, target_version, merged, timeout, 250, keys_only, depth + 1,
        )
        return
    if not keys_only and invalid and len(batch) == 1 and desc_max > 0:
        k = batch[0].get("key", "?")
        print(f"   ↳ Retry {k} summary-only (no description body)")
        _nlm_try_batch_rice(
            nid, batch, validator, target_version, merged, timeout, 0, keys_only, depth + 1,
        )
        return


def fetch_rice_from_nlm_cli(
    notebook_name: str,
    features: List[Dict],
    validator: JiraFeatureValidator,
    target_version: str,
    batch_size: int,
    delay_sec: float,
    desc_max_chars: int,
    keys_only: bool,
) -> Dict[str, Dict[str, str]]:
    """RICE scores via ``nlm query notebook`` (same auth as NLM MCP)."""
    merged: Dict[str, Dict[str, str]] = {}
    nid = find_notebook_id_by_title(notebook_name)
    if not nid:
        print(f"❌ nlm: no notebook titled {notebook_name!r}. Run: nlm notebook list")
        return merged

    print(f"📓 NotebookLM via nlm: {notebook_name!r} ({nid})")
    print(
        "   RICE prompt: "
        + (
            "keys + Jira summaries (set NOTEBOOKLM_RICE_SUMMARY_MAX=0 for keys-only)"
            if keys_only
            else "includes Jira summary/description"
        ),
    )
    timeout = int(os.getenv("NOTEBOOKLM_RICE_QUERY_TIMEOUT", "600"))

    for start in range(0, len(features), batch_size):
        batch = features[start : start + batch_size]
        keys = [f.get("key") for f in batch]
        print(
            f"   RICE batch {start // batch_size + 1}: {keys[0]} … {keys[-1]} "
            f"({len(batch)} issues)"
        )
        _nlm_try_batch_rice(
            nid,
            batch,
            validator,
            target_version,
            merged,
            timeout,
            desc_max_chars,
            keys_only,
        )

        if delay_sec > 0 and start + batch_size < len(features):
            time.sleep(delay_sec)

    return merged


async def fetch_rice_from_notebooklm(
    notebook_name: str,
    features: List[Dict],
    validator: JiraFeatureValidator,
    target_version: str,
    batch_size: int,
    delay_sec: float,
    desc_max_chars: int = 1800,
    keys_only: bool = True,
) -> Dict[str, Dict[str, str]]:
    """Ask NotebookLM in batches; return map issue key -> {reach, impact, confidence, effort}."""
    merged: Dict[str, Dict[str, str]] = {}
    async with await NotebookLMClient.from_storage() as client:
        notebooks = await client.notebooks.list()
        nb = None
        for n in notebooks:
            if n.title == notebook_name:
                nb = n
                break
        if nb is None:
            print(f"❌ NotebookLM notebook not found: {notebook_name!r}")
            return merged

        print(f"📓 NotebookLM: {notebook_name} (id {nb.id})")
        print(
            "   RICE prompt: "
            + (
                "keys + Jira summaries (set NOTEBOOKLM_RICE_SUMMARY_MAX=0 for keys-only)"
                if keys_only
                else "includes Jira summary/description"
            ),
        )
        for start in range(0, len(features), batch_size):
            batch = features[start : start + batch_size]
            keys = [f.get("key") for f in batch]
            print(
                f"   RICE batch {start // batch_size + 1}: {keys[0]} … {keys[-1]} "
                f"({len(batch)} issues)"
            )
            prompt = _build_rice_batch_prompt(
                batch,
                validator,
                target_version,
                desc_max_chars=desc_max_chars,
                keys_only=keys_only,
            )
            try:
                result = await client.chat.ask(nb.id, prompt)
            except Exception as e:
                print(f"   ⚠️  NotebookLM ask failed: {e}")
                for k in keys:
                    if k:
                        merged[k] = {x: "" for x in RICE_KEYS}
                continue

            answer = (result.answer or "").strip()
            _merge_rice_answer_for_batch(batch, answer, merged)

            if delay_sec > 0 and start + batch_size < len(features):
                await asyncio.sleep(delay_sec)

    return merged


def write_full_report_csv(
    path: Path,
    validator: JiraFeatureValidator,
    validation_results: List[Dict],
    label_actions: Dict[str, str],
    version_label: str,
    feature_map: Dict[str, Dict],
    rice_scores: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    section_headers = [s.header for s in JiraFeatureValidator.TEMPLATE_SECTIONS]
    fieldnames = [
        "Key",
        "Summary",
        "Status",
        "Assignee",
        "Labels",
        f"Has_{version_label.replace('.', '_')}_Label",
        "Label_Action",
        "Product_Manager",
        "Missing_Product_Manager",
        "Target_Version",
        "Compliant",
        "Required_Missing",
    ]
    if rice_scores is not None:
        fieldnames.extend(RICE_COLUMNS)
    fieldnames.extend(section_headers)

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in validation_results:
            key = result["key"]
            feature = feature_map.get(key, {})
            fields = feature.get("fields", {})
            labels = fields.get("labels") or []
            labels_str = " | ".join(labels) if labels else ""
            pm = validator._extract_display_name(
                fields.get(validator.product_manager_field())
            )
            tv = validator._extract_version_name(
                fields.get(validator.target_version_field())
            )
            has_lbl = version_label in labels
            row = {
                "Key": key,
                "Summary": result["summary"],
                "Status": (fields.get("status") or {}).get("name", ""),
                "Assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
                "Labels": labels_str,
                f"Has_{version_label.replace('.', '_')}_Label": "Yes" if has_lbl else "No",
                "Label_Action": label_actions.get(key, ""),
                "Product_Manager": pm,
                "Missing_Product_Manager": "Yes" if not pm.strip() else "No",
                "Target_Version": tv,
                "Compliant": "Yes" if result["overall_valid"] else "No",
                "Required_Missing": result["required_missing"],
            }
            if rice_scores is not None:
                rs = rice_scores.get(key, {})
                row["Reach"] = rs.get("reach", "")
                row["Impact"] = rs.get("impact", "")
                row["Confidence"] = rs.get("confidence", "")
                row["Effort"] = rs.get("effort", "")
            for vr in result["validation_results"]:
                h = vr["header"]
                if vr["valid"]:
                    row[h] = "PASS"
                elif not vr["required"]:
                    row[h] = "SKIP (optional)"
                elif not vr["content_preview"]:
                    row[h] = "MISSING"
                else:
                    row[h] = f"FAIL: {vr['content_preview']}"
            writer.writerow(row)


def write_missing_pm_csv(
    path: Path,
    validator: JiraFeatureValidator,
    validation_results: List[Dict],
    label_actions: Dict[str, str],
    feature_map: Dict[str, Dict],
) -> None:
    rows = []
    for result in validation_results:
        key = result["key"]
        feature = feature_map.get(key, {})
        fields = feature.get("fields", {})
        pm = validator._extract_display_name(
            fields.get(validator.product_manager_field())
        )
        if pm.strip():
            continue
        labels = fields.get("labels") or []
        rows.append({
            "Key": key,
            "Summary": result["summary"],
            "Status": (fields.get("status") or {}).get("name", ""),
            "Assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
            "Labels": " | ".join(labels) if labels else "",
            "Label_Action": label_actions.get(key, ""),
            "Compliant": "Yes" if result["overall_valid"] else "No",
            "Required_Missing": result["required_missing"],
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        if not rows:
            f.write("Key,Summary,Status,Assignee,Labels,Label_Action,Compliant,Required_Missing\n")
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync version label, report missing PM, validate ROX feature templates",
    )
    parser.add_argument(
        "--target-version",
        default="5.0.0",
        help='Target Version value and label to enforce (default: 5.0.0)',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not add labels in Jira; still write reports",
    )
    parser.add_argument(
        "--jira-url",
        default=os.getenv("JIRA_BASE_URL", "https://issues.redhat.com"),
    )
    parser.add_argument("--email", default=os.getenv("JIRA_EMAIL", ""))
    parser.add_argument("--token", default=jira_api_token_from_env())
    parser.add_argument(
        "--notebooklm-rice",
        action="store_true",
        help=(
            "Query NotebookLM (ACS RICE framework) for Reach, Impact, Confidence, Effort "
            "and add columns to the full validation CSV"
        ),
    )
    parser.add_argument(
        "--notebook-name",
        default=os.getenv("NOTEBOOKLM_NOTEBOOK_NAME", DEFAULT_NOTEBOOK_NAME),
        help=f"NotebookLM notebook title (default: {DEFAULT_NOTEBOOK_NAME})",
    )
    parser.add_argument(
        "--rice-batch-size",
        type=int,
        default=2,
        help="Features per top-level NotebookLM batch (default: 2; nlm splits further on errors)",
    )
    parser.add_argument(
        "--rice-jira-context",
        action="store_true",
        help=(
            "Include Jira summary and description excerpts in RICE prompts (full context). "
            "Default without this flag: each line is key + Jira summary (NOTEBOOKLM_RICE_SUMMARY_MAX); "
            "set NOTEBOOKLM_RICE_INCLUDE_JIRA_CONTEXT=1 for the same without this flag"
        ),
    )
    parser.add_argument(
        "--rice-desc-max",
        type=int,
        default=None,
        metavar="N",
        help=(
            "With --rice-jira-context: max Jira description chars per feature (0 = summary only). "
            "Default: NOTEBOOKLM_RICE_DESC_MAX (600) for nlm, NOTEBOOKLM_RICE_DESC_MAX_PY (1800) for notebooklm-py"
        ),
    )
    parser.add_argument(
        "--rice-delay",
        type=float,
        default=2.0,
        help="Seconds to wait between NotebookLM batches (default: 2)",
    )
    args = parser.parse_args()

    if not args.token:
        print("❌ JIRA_TOKEN or JIRA_API_TOKEN not set")
        return 1

    tv = args.target_version.strip()
    version_label = tv

    validator = JiraFeatureValidator(
        jira_url=args.jira_url,
        email=args.email,
        api_token=args.token,
        project_key="ROX",
        target_version=tv,
    )

    print(f"🚀 ROX Target Version: {tv} — label sync, PM gap report, template validation")
    print("=" * 60)
    if not validator.test_connection():
        return 1

    features = validator.get_features()
    if not features:
        print(f"⚠️  No features found for Target Version {tv}")
        return 0

    feature_map = {f.get("key"): f for f in features}
    label_actions: Dict[str, str] = {}

    for i, feature in enumerate(features, 1):
        key = feature.get("key", "")
        fields = feature.get("fields", {})
        labels = list(fields.get("labels") or [])
        print(f"   [{i}/{len(features)}] {key} labels={labels}")

        if version_label in labels:
            label_actions[key] = "already_present"
            continue

        if args.dry_run:
            label_actions[key] = "would_add (dry-run)"
            continue

        if add_label_to_issue(
            validator.session,
            validator.jira_url,
            validator.api_version,
            key,
            version_label,
        ):
            label_actions[key] = "added"
            labels.append(version_label)
            fields["labels"] = labels
        else:
            label_actions[key] = "add_failed"

    print("🔍 Validating descriptions (jira_feature_validator logic)...")
    validation_results = []
    for i, feature in enumerate(features, 1):
        print(f"   {i}/{len(features)}: {feature.get('key')}")
        validation_results.append(validator.validate_feature(feature))

    rice_scores: Optional[Dict[str, Dict[str, str]]] = None
    if args.notebooklm_rice:
        has_nlm = shutil.which("nlm") is not None
        if not NOTEBOOKLM_AVAILABLE and not has_nlm:
            print(
                "❌ NotebookLM RICE needs either:\n"
                "   • notebooklm-mcp-cli + `nlm login` (recommended, same as NLM MCP), or\n"
                "   • pip install 'notebooklm-py[browser]' + `notebooklm login`"
            )
            return 1
        print("🤖 NotebookLM RICE scoring (batched chat)...")
        rice_keys_only = _rice_prompt_keys_only(args.rice_jira_context)
        if NOTEBOOKLM_AVAILABLE:
            rice_scores = asyncio.run(
                fetch_rice_from_notebooklm(
                    args.notebook_name,
                    features,
                    validator,
                    tv,
                    max(1, args.rice_batch_size),
                    max(0.0, args.rice_delay),
                    desc_max_chars=_rice_desc_max_py(args.rice_desc_max),
                    keys_only=rice_keys_only,
                )
            )
        else:
            rice_scores = fetch_rice_from_nlm_cli(
                args.notebook_name,
                features,
                validator,
                tv,
                max(1, args.rice_batch_size),
                max(0.0, args.rice_delay),
                desc_max_chars=_rice_desc_max_nlm(args.rice_desc_max),
                keys_only=rice_keys_only,
            )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ver_tag = tv.replace(".", "_")
    out_dir = Path(__file__).parent / "output"
    full_path = out_dir / f"rox_{ver_tag}_labels_pm_validation_{ts}.csv"
    missing_pm_path = out_dir / f"rox_{ver_tag}_missing_product_manager_{ts}.csv"

    write_full_report_csv(
        full_path,
        validator,
        validation_results,
        label_actions,
        version_label,
        feature_map,
        rice_scores=rice_scores,
    )
    write_missing_pm_csv(
        missing_pm_path, validator, validation_results, label_actions, feature_map
    )

    added = sum(1 for a in label_actions.values() if a == "added")
    failed = sum(1 for a in label_actions.values() if a == "add_failed")
    pm_key = validator.product_manager_field()
    missing_pm = sum(
        1
        for r in validation_results
        if not validator._extract_display_name(
            feature_map.get(r["key"], {}).get("fields", {}).get(pm_key)
        ).strip()
    )
    compliant = sum(1 for r in validation_results if r["overall_valid"])

    print(f"\n{'=' * 60}")
    print("📊 SUMMARY")
    print(f"{'=' * 60}")
    print(f"Features (Target Version {tv}): {len(features)}")
    print(f"Labels added: {added}" + (" (dry-run — no Jira changes)" if args.dry_run else ""))
    if failed:
        print(f"Label updates failed: {failed}")
    print(f"Missing Product Manager: {missing_pm}")
    print(f"Template compliant: {compliant} / {len(features)}")
    if rice_scores is not None:
        filled = sum(
            1
            for r in rice_scores.values()
            if any(r.get(k, "").strip() for k in RICE_KEYS)
        )
        print(f"NotebookLM RICE rows with any score: {filled} / {len(features)}")
    print(f"\n📄 Full report: {full_path}")
    print(f"📄 Missing PM only: {missing_pm_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

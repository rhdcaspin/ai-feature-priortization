#!/usr/bin/env python3
"""
Assign exactly one product-pillar label per ROX Feature using an LLM, then sync Jira.

Categories (mutually exclusive pillars):
  - unified-workload-protection
  - frictionless-security-runtime-observability
  - ai-driven-vuln-risk-management

Additionally, each issue may get the **enterprise_ready** label (independent of pillar):
true when the text describes GA / production / broad enterprise deployment readiness; false otherwise.

For each issue: AI picks one pillar category. If that label is missing, it is added.
If another pillar label from the set is already present and differs from the AI choice,
the old pillar label(s) are removed so only one pillar remains (use --additive-only
to never remove existing pillar labels).

After batch + single-issue JSON retries, small local models get an extra pass
without ``format=json``, then a **plain-line** answer (no JSON). Disable with
``--no-plain-fallback``.

Uses **Ollama** only: run ``ollama serve`` locally and set ``OLLAMA_MODEL`` (or
``--ollama-model``). Optional: ``OLLAMA_BASE_URL`` / ``--ollama-url``. If
``/api/chat`` returns 404 (older Ollama), the script falls back to ``/api/generate``.

Same Jira env as other scripts.

Usage:
    OLLAMA_MODEL=llama3.2 python3 rox_feature_category_labels.py --dry-run
    python3 rox_feature_category_labels.py --apply --ollama-model mistral
    python3 rox_feature_category_labels.py --apply --jql 'project = ROX AND type = feature AND "Target Version" = "5.0.0"'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from jira_auth import is_jira_cloud_url, jira_api_token_from_env  # noqa: E402
from jira_feature_validator import JiraFeatureValidator  # noqa: E402

PILLAR_LABELS = (
    "unified-workload-protection",
    "frictionless-security-runtime-observability",
    "ai-driven-vuln-risk-management",
)

ENTERPRISE_READY_LABEL = "enterprise_ready"

ENTERPRISE_READY_GUIDANCE = """
For each feature, also set boolean "enterprise_ready":
  true — GA or production-ready framing, explicit enterprise rollout, supported for typical
         customer production use, or clearly not experimental/preview-only.
  false — preview/tech-preview/experimental, narrow/internal use, insufficient description,
         or clearly not positioned as broadly enterprise-deployable.
"""

PILLAR_GUIDANCE = """
Classify each Red Hat Advanced Cluster Security (ACS / RHACS) **Feature** into exactly ONE pillar:

1) unified-workload-protection
   Workload-centric security: policies tied to deployments/workloads, admission control,
   network policies, runtime enforcement on workloads, identity of workloads, compliance
   as it applies to what runs in the cluster, namespace/team scoping, security profiles
   for workloads.

2) frictionless-security-runtime-observability
   Operational experience, platform/runtime visibility, central & secured cluster connectivity,
   observability, metrics/logging/tracing for security workflows, reducing friction in install
   upgrade and day-2 ops, roxctl/CLI ergonomics, UI flows that are about using the product
   smoothly, performance and reliability of the control plane / sensor path when framed as
   "making security usable and visible", not vuln-specific.

3) ai-driven-vuln-risk-management
   Vulnerability management, image/registry scanning, risk scoring and prioritization,
   SBOM, CVE workflows, remediation guidance, AI/ML applied to vuln or risk data,
   scanner integration, deferral/exceptions for CVEs.

If two pillars seem to apply, pick the **primary** customer outcome described in the text.
Return ONLY valid JSON: an array of objects
{"key": "<ROX-nnnn>", "category": "<one of the three pillar slugs>", "enterprise_ready": true|false}.
"""


def _parse_enterprise_ready_flag(raw: Any) -> Optional[bool]:
    """Normalize LLM enterprise_ready to bool, or None if absent/unknown."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        if raw == 1:
            return True
        if raw == 0:
            return False
        return None
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("true", "yes", "y", "1"):
            return True
        if s in ("false", "no", "n", "0"):
            return False
        return None
    return None


def _rows_from_classification_list(rows: List[Any]) -> Dict[str, Dict[str, Any]]:
    """Map issue key -> {"category": pillar slug, "enterprise_ready": bool|None}."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        k = (row.get("key") or row.get("Key") or row.get("issue") or row.get("id") or "").strip()
        c = (
            row.get("category")
            or row.get("Category")
            or row.get("label")
            or row.get("pillar")
            or row.get("slug")
            or row.get("bucket")
            or row.get("theme")
            or ""
        )
        if isinstance(c, str):
            c = c.strip()
        else:
            c = str(c).strip() if c is not None else ""
        er = _parse_enterprise_ready_flag(row.get("enterprise_ready"))
        if k and c in PILLAR_LABELS:
            out[k.upper()] = {"category": c, "enterprise_ready": er}
    return out


def _extract_classification_rows(parsed: Any) -> List[dict]:
    """Normalize assorted LLM JSON shapes into a list of row dicts."""
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]

    if not isinstance(parsed, dict):
        return []

    # Map ROX-123 -> pillar slug (some small models emit this)
    if parsed:
        keys_ok = all(
            isinstance(k, str) and re.match(r"^ROX-\d+$", k.strip(), re.I)
            for k in parsed.keys()
        )
        vals_ok = all(isinstance(v, str) and v.strip() in PILLAR_LABELS for v in parsed.values())
        if keys_ok and vals_ok:
            return [{"key": k, "category": v.strip(), "enterprise_ready": None} for k, v in parsed.items()]

    # Single object: {"key":"ROX-1","category":"..."} (or typo keys)
    k0 = (parsed.get("key") or parsed.get("Key") or "").strip()
    c0 = (parsed.get("category") or parsed.get("Category") or "").strip()
    if k0 and c0 and re.match(r"^ROX-\d+$", k0, re.I):
        return [parsed]

    for name in ("classification", "result", "entry", "feature"):
        v = parsed.get(name)
        if isinstance(v, dict) and (v.get("key") or v.get("Key")):
            return [v]

    # {"0": {...}, "1": {...}} — numbered or arbitrary keys, values are row objects
    if parsed:
        vals = [v for v in parsed.values() if isinstance(v, dict)]
        if vals and len(vals) == len(parsed):
            if all((v.get("key") or v.get("Key") or v.get("issue")) for v in vals):
                return list(vals)

    known_lists = (
        "classifications",
        "items",
        "results",
        "data",
        "features",
        "issues",
        "rows",
        "output",
        "response",
        "answer",
        "features_classified",
        "labels",
    )
    for name in known_lists:
        v = parsed.get(name)
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            return list(v)

    # Stringified JSON array inside an object (some models do this)
    for v in parsed.values():
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                try:
                    inner = json.loads(s)
                    got = _extract_classification_rows(inner)
                    if got:
                        return got
                except json.JSONDecodeError:
                    pass

    # First top-level list of dicts that looks like classifications
    for v in parsed.values():
        if isinstance(v, list) and len(v) >= 1 and all(isinstance(x, dict) for x in v):
            if any(
                str(x.get("key") or x.get("Key") or "").upper().startswith("ROX-")
                for x in v[: min(5, len(v))]
            ):
                return list(v)

    # Nested object (one level)
    for v in parsed.values():
        if isinstance(v, dict):
            inner = _extract_classification_rows(v)
            if inner:
                return inner
        if isinstance(v, list) and v and isinstance(v[0], dict):
            inner = _extract_classification_rows(v)
            if inner:
                return inner

    return []


def _coerce_classification_json(content: str) -> Dict[str, Dict[str, Any]]:
    """Parse LLM output into map issue_key -> {category, enterprise_ready}."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)

    parsed = json.loads(text)

    # Plain array (possibly after stripping noise)
    if isinstance(parsed, list):
        rows = _extract_classification_rows(parsed)
        if not rows:
            raise ValueError("LLM returned an empty or invalid JSON array")
        return _rows_from_classification_list(rows)

    if not isinstance(parsed, dict):
        raise ValueError("LLM returned JSON that is neither object nor array")

    rows = _extract_classification_rows(parsed)
    if not rows:
        # Last resort: brace matching for inner array (model added prose keys)
        if not text.lstrip().startswith("["):
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end > start:
                try:
                    inner = json.loads(text[start : end + 1])
                    rows = _extract_classification_rows(inner)
                except json.JSONDecodeError:
                    rows = []
    if not rows:
        raise ValueError(
            "LLM returned JSON without a usable list of {key, category} objects; "
            "try --ollama-no-json-format or a larger model"
        )
    return _rows_from_classification_list(rows)


def _truncate(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _ollama_messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    """Flatten chat messages into one prompt for /api/generate."""
    parts = [(m.get("content") or "").strip() for m in messages]
    return "\n\n".join(p for p in parts if p)


def _ollama_complete(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    *,
    timeout_sec: int,
    temperature: float,
    json_format: bool,
) -> str:
    """Call Ollama: POST /api/chat, or on 404 POST /api/generate (older builds without /api/chat)."""
    base = base_url.rstrip("/")
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if json_format:
        body["format"] = "json"

    r = requests.post(f"{base}/api/chat", json=body, timeout=timeout_sec)
    if r.status_code != 404:
        r.raise_for_status()
        return (r.json().get("message") or {}).get("content") or ""

    gen: Dict[str, Any] = {
        "model": model,
        "prompt": _ollama_messages_to_prompt(messages),
        "stream": False,
        "options": {"temperature": temperature},
    }
    if json_format:
        gen["format"] = "json"
    r2 = requests.post(f"{base}/api/generate", json=gen, timeout=timeout_sec)
    r2.raise_for_status()
    return (r2.json().get("response") or "").strip()


def _ollama_classify_batch(
    base_url: str,
    model: str,
    items: List[Dict[str, str]],
    timeout_sec: int,
    json_format: bool,
) -> Dict[str, Dict[str, Any]]:
    """Classify via local Ollama (/api/chat or /api/generate fallback)."""
    user_payload = json.dumps(
        [{"key": x["key"], "summary": x["summary"], "description": x["description"]} for x in items],
        ensure_ascii=False,
    )
    system = (
        "You are a product taxonomy assistant for RHACS. "
        + PILLAR_GUIDANCE
        + ENTERPRISE_READY_GUIDANCE
        + "\nThe three allowed category values are exactly:\n"
        + "\n".join(f"  - {p}" for p in PILLAR_LABELS)
    )
    user_msg = (
        "Classify each feature (pillar + enterprise_ready). "
        "Respond with ONLY a JSON array, no markdown or explanation.\n\n"
        + user_payload
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    content = _ollama_complete(
        base_url,
        model,
        messages,
        timeout_sec=timeout_sec,
        temperature=0.1,
        json_format=json_format,
    )
    return _coerce_classification_json(content)


def _parse_slug_from_freeform_text(content: str) -> Optional[str]:
    """Extract a pillar slug if it appears anywhere in the model output."""
    if not content:
        return None
    low = content.lower()
    for slug in sorted(PILLAR_LABELS, key=len, reverse=True):
        if slug in low:
            return slug
    return None


def _first_line_slug(content: str) -> Optional[str]:
    """If the first non-empty line is exactly a slug, return it."""
    for line in (content or "").strip().splitlines():
        s = line.strip().strip('"`').strip().rstrip(".,;:")
        if not s or s.startswith("#"):
            continue
        if s in PILLAR_LABELS:
            return s
        low = s.lower()
        if low in PILLAR_LABELS:
            return low
    return None


def classify_feature_plain_line(
    feature: Dict[str, Any],
    *,
    ollama_url: str,
    ollama_model: str,
    ollama_timeout: int,
) -> Optional[str]:
    """Ask Ollama for a single-line answer (no JSON). Works better with small local models."""
    key = feature.get("key") or ""
    summary = feature.get("summary") or ""
    desc = feature.get("description") or ""
    opts = "\n".join(PILLAR_LABELS)
    user = (
        f"You classify Red Hat ACS (RHACS) Jira features into exactly one pillar.\n\n"
        f"Issue: {key}\nSummary: {summary}\n\nDescription:\n{desc[:12000]}\n\n"
        f"Reply with EXACTLY ONE LINE. That line must be ONLY one of these strings "
        f"(copy verbatim, no quotes, no JSON, no explanation):\n{opts}"
    )
    raw = _ollama_complete(
        ollama_url,
        ollama_model,
        [{"role": "user", "content": user}],
        timeout_sec=ollama_timeout,
        temperature=0.0,
        json_format=False,
    )

    cat = _first_line_slug(raw) or _parse_slug_from_freeform_text(raw)
    return cat if cat in PILLAR_LABELS else None


def fetch_features_jql(
    validator: JiraFeatureValidator,
    jql: str,
    desc_max: int,
) -> List[Dict[str, Any]]:
    session = validator.session
    jira_url = validator.jira_url
    is_cloud = getattr(validator, "is_cloud", is_jira_cloud_url(jira_url))
    api_version = getattr(validator, "api_version", "3" if is_cloud else "2")
    fields_param = "summary,description,labels"

    issues: List[Dict] = []
    max_results = 50
    if is_cloud:
        url = f"{jira_url}/rest/api/3/search/jql"
        token = None
        while True:
            params: Dict[str, Any] = {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields_param,
            }
            if token:
                params["nextPageToken"] = token
            resp = session.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            issues.extend(data.get("issues") or [])
            if data.get("isLast", True):
                break
            token = data.get("nextPageToken")
            if not token:
                break
    else:
        url = f"{jira_url}/rest/api/{api_version}/search"
        start = 0
        while True:
            resp = session.get(
                url,
                params={
                    "jql": jql,
                    "startAt": start,
                    "maxResults": max_results,
                    "fields": fields_param,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            chunk = data.get("issues") or []
            issues.extend(chunk)
            if len(chunk) < max_results:
                break
            start += max_results

    out: List[Dict[str, Any]] = []
    for issue in issues:
        key = issue.get("key") or ""
        fields = issue.get("fields") or {}
        summary = fields.get("summary") or ""
        raw_desc = fields.get("description")
        desc = JiraFeatureValidator.description_to_plain_text(raw_desc)
        labels = list(fields.get("labels") or [])
        out.append(
            {
                "key": key,
                "summary": summary,
                "description": _truncate(desc, desc_max),
                "labels": labels,
            }
        )
    return out


def compute_label_updates(
    desired: str,
    current: List[str],
    additive_only: bool,
) -> Tuple[str, List[Dict[str, str]]]:
    """Return (status, updates) where status is already_correct | skip | would_update."""
    current_set = set(current)
    pillars_on = [p for p in PILLAR_LABELS if p in current_set]
    wrong = [p for p in pillars_on if p != desired]

    if not wrong and desired in current_set:
        return "already_correct", []

    if additive_only:
        if pillars_on and desired not in current_set:
            return "skip_other_pillar_present", []
        if desired in current_set:
            return "already_correct", []
        return "would_update", [{"add": desired}]

    updates: List[Dict[str, str]] = []
    for p in PILLAR_LABELS:
        if p in current_set and p != desired:
            updates.append({"remove": p})
    if desired not in current_set:
        updates.append({"add": desired})
    if not updates:
        return "already_correct", []
    return "would_update", updates


def enterprise_ready_label_updates(
    desired: Optional[bool], current: List[str]
) -> List[Dict[str, str]]:
    """Jira label edits for enterprise_ready. ``desired is None`` means leave unchanged."""
    if desired is None:
        return []
    cur = set(str(x) for x in (current or []))
    has = ENTERPRISE_READY_LABEL in cur
    if desired and not has:
        return [{"add": ENTERPRISE_READY_LABEL}]
    if not desired and has:
        return [{"remove": ENTERPRISE_READY_LABEL}]
    return []


def _lookup_classification(
    mapped: Dict[str, Dict[str, Any]], issue_key: str
) -> Optional[Tuple[str, Optional[bool]]]:
    """Resolve LLM map entry to (pillar_slug, enterprise_ready_or_none)."""
    ku = issue_key.upper().strip()
    row = mapped.get(ku)
    if isinstance(row, dict) and row.get("category"):
        c = str(row["category"]).strip()
        if c in PILLAR_LABELS:
            return (c, _parse_enterprise_ready_flag(row.get("enterprise_ready")))
    for mk, row in mapped.items():
        if not mk or not row:
            continue
        if not isinstance(row, dict):
            continue
        c = row.get("category")
        if not c or str(c).strip() not in PILLAR_LABELS:
            continue
        c = str(c).strip()
        mku = str(mk).upper().strip()
        if mku == ku or (mku.isdigit() and ku == f"ROX-{mku}"):
            return (c, _parse_enterprise_ready_flag(row.get("enterprise_ready")))
    return None


def apply_label_updates(
    session: requests.Session,
    jira_url: str,
    api_version: str,
    issue_key: str,
    updates: List[Dict[str, str]],
) -> Tuple[bool, str]:
    url = f"{jira_url.rstrip('/')}/rest/api/{api_version}/issue/{issue_key}"
    resp = session.put(url, json={"update": {"labels": updates}})
    if resp.status_code in (200, 204):
        return True, "updated"
    return False, f"{resp.status_code} {resp.text[:300]}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI-classify ROX features into pillar labels + enterprise_ready and sync Jira",
    )
    parser.add_argument(
        "--jql",
        default='project = ROX AND type = feature AND "Target Version" = "5.0.0"',
        help="JQL selecting features to classify",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes only (no Jira updates)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform Jira updates (required to write; omit for dry-run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Features per LLM request (default: 8)",
    )
    parser.add_argument(
        "--desc-max",
        type=int,
        default=3500,
        help="Max description characters per feature sent to the model",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between LLM batches",
    )
    parser.add_argument(
        "--no-single-retry",
        action="store_true",
        help="Do not re-call the LLM one issue at a time when a batch omits keys",
    )
    parser.add_argument(
        "--no-plain-fallback",
        action="store_true",
        help="Do not use single-line (non-JSON) LLM fallback after JSON retries fail",
    )
    parser.add_argument(
        "--additive-only",
        action="store_true",
        help="Only add missing pillar label; do not remove other pillar labels",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        help="Ollama server base URL (default: OLLAMA_BASE_URL or http://127.0.0.1:11434)",
    )
    parser.add_argument(
        "--ollama-model",
        default=os.getenv("OLLAMA_MODEL", ""),
        help="Ollama model name (default: OLLAMA_MODEL)",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=int,
        default=int(os.getenv("OLLAMA_TIMEOUT", "600")),
        help="Seconds to wait for Ollama per batch (default: 600)",
    )
    parser.add_argument(
        "--ollama-no-json-format",
        action="store_true",
        help="Do not use Ollama format=json (try if the model returns invalid JSON)",
    )
    args = parser.parse_args()

    if args.dry_run and args.apply:
        print("⚠️  --dry-run wins over --apply; no Jira updates will be made")
    do_apply = bool(args.apply) and not args.dry_run

    ollama_model = (args.ollama_model or "").strip()
    if not ollama_model:
        print("❌ Set OLLAMA_MODEL in .env or pass --ollama-model (e.g. llama3.2)")
        return 1

    if not do_apply:
        print("ℹ️  Preview mode: no Jira writes. Pass --apply to add or fix labels.\n")

    jira_token = jira_api_token_from_env()
    if not jira_token:
        print("❌ JIRA_TOKEN or JIRA_API_TOKEN not set")
        return 1

    validator = JiraFeatureValidator(
        jira_url=os.getenv("JIRA_BASE_URL", "https://issues.redhat.com"),
        email=os.getenv("JIRA_EMAIL", ""),
        api_token=jira_token,
        project_key="ROX",
        target_version="5.0.0",
    )
    if not validator.test_connection():
        return 1

    print(f"🔍 JQL: {args.jql}")
    features = fetch_features_jql(validator, args.jql, args.desc_max)
    print(f"📊 Fetched {len(features)} issues")

    if not features:
        return 0

    print(
        f"🦙 Ollama: {args.ollama_url.rstrip('/')}  model={ollama_model}  "
        f"json_format={not args.ollama_no_json_format}"
    )
    classify_fn = lambda batch: _ollama_classify_batch(
        args.ollama_url,
        ollama_model,
        batch,
        args.ollama_timeout,
        json_format=not args.ollama_no_json_format,
    )

    key_to_category: Dict[str, str] = {}
    key_to_enterprise_ready: Dict[str, bool] = {}
    bs = max(1, args.batch_size)
    for i in range(0, len(features), bs):
        batch = features[i : i + bs]
        keys = [f["key"] for f in batch]
        print(f"   🤖 LLM batch {i // bs + 1}: {keys[0]} … {keys[-1]} ({len(batch)})")
        try:
            mapped = classify_fn(batch)
        except Exception as e:
            print(f"   ⚠️  Batch failed: {e}")
            mapped = {}
        for f in batch:
            got = _lookup_classification(mapped, f["key"])
            if got:
                cat, er = got
                key_to_category[f["key"]] = cat
                if er is not None:
                    key_to_enterprise_ready[f["key"]] = er
        missing = [f for f in batch if f["key"] not in key_to_category]
        if missing and not args.no_single_retry:
            for f in missing:
                print(f"   ↳ single-issue retry: {f['key']}")
                cat: Optional[str] = None
                via = ""

                jf_order: List[bool] = []
                if not args.ollama_no_json_format:
                    jf_order.append(True)
                jf_order.append(False)
                for use_json_fmt in jf_order:
                    try:
                        one_o = _ollama_classify_batch(
                            args.ollama_url,
                            ollama_model,
                            [f],
                            args.ollama_timeout,
                            json_format=use_json_fmt,
                        )
                        got = _lookup_classification(one_o, f["key"])
                        if got:
                            cat, er_g = got
                            if er_g is not None:
                                key_to_enterprise_ready[f["key"]] = er_g
                            if cat:
                                via = "JSON" if use_json_fmt else "JSON(no format=)"
                                break
                    except Exception as e:
                        tag = "format=json" if use_json_fmt else "no format="
                        print(f"      ⚠️  Ollama {tag} {f['key']}: {e}")

                if not cat and not args.no_plain_fallback:
                    try:
                        cat = classify_feature_plain_line(
                            f,
                            ollama_url=args.ollama_url,
                            ollama_model=ollama_model,
                            ollama_timeout=args.ollama_timeout,
                        )
                        if cat:
                            via = "plain-line"
                    except Exception as e:
                        print(f"      ⚠️  plain-line fallback failed {f['key']}: {e}")

                if cat:
                    key_to_category[f["key"]] = cat
                    er_show = key_to_enterprise_ready.get(f["key"])
                    er_part = f" enterprise_ready={er_show}" if er_show is not None else ""
                    print(f"      ✓ {f['key']} → {cat}{er_part} ({via})")
                else:
                    print(f"      ⚠️  still no classification for {f['key']}")

                if args.delay > 0:
                    time.sleep(min(args.delay, 3.0))
        elif missing:
            for f in missing:
                print(f"   ⚠️  No classification for {f['key']}")
        if i + bs < len(features) and args.delay > 0:
            time.sleep(args.delay)

    print(f"\n✅ Classified {len(key_to_category)} / {len(features)} issues")

    api_version = getattr(validator, "api_version", "3")
    updated = 0
    skipped = 0
    failed = 0
    unclassified = 0

    for f in features:
        key = f["key"]
        desired = key_to_category.get(key)
        if not desired:
            print(f"   ⚠️  {key}: unclassified, skipping")
            unclassified += 1
            continue
        labels = f["labels"]
        er_desired: Optional[bool] = key_to_enterprise_ready.get(key)
        status, pillar_updates = compute_label_updates(desired, labels, args.additive_only)
        er_updates = enterprise_ready_label_updates(er_desired, labels)
        updates = pillar_updates + er_updates
        pillars = [p for p in PILLAR_LABELS if p in labels]
        has_er = ENTERPRISE_READY_LABEL in set(labels)

        if status == "already_correct" and not er_updates:
            print(f"   ✓ {key}: {desired} (already correct)")
            skipped += 1
            continue
        if status == "skip_other_pillar_present" and not er_updates:
            print(
                f"   — {key}: AI→{desired} skipped (--additive-only; "
                f"already has {pillars})"
            )
            skipped += 1
            continue

        if not updates:
            skipped += 1
            continue

        if not do_apply:
            er_note = (
                f" enterprise_ready→{er_desired!r} (now {has_er}) er_updates={er_updates}"
                if er_desired is not None
                else ""
            )
            print(
                f"   [dry-run] {key}: pillar→{desired} "
                f"(now: {pillars or 'none'})  pillar_updates={pillar_updates}{er_note}"
            )
            updated += 1
            continue

        ok, err = apply_label_updates(
            validator.session, validator.jira_url, api_version, key, updates
        )
        if ok:
            er_note = f" enterprise_ready={er_desired}" if er_desired is not None else ""
            print(f"   ✅ {key}: pillar→{desired}{er_note}  {updates}")
            updated += 1
        else:
            print(f"   ❌ {key}: {err}")
            failed += 1

    print(f"\n{'=' * 50}")
    if do_apply:
        print(f"Updated: {updated}  Skipped: {skipped}  Failed: {failed}")
        if unclassified:
            print(f"Unclassified (skipped): {unclassified}")
        return 0 if failed == 0 else 1

    print(f"Planned changes: {updated}  Skipped: {skipped}  Unclassified: {unclassified}")
    print("Re-run with --apply to update Jira (omit --dry-run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

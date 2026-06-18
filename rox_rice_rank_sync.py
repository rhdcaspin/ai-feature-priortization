#!/usr/bin/env python3
"""
Sync Jira Rank (LexoRank) for ROX Features from RICE Score — higher score = higher on the backlog.
Equal RICE Score → higher Reach ranks higher.

Target version defaults to 5.0.0. Uses the Agile Rank API on Jira Cloud (Rank field is LexoRank).

Manual overrides (skipped on normal runs):
  - Jira label ``rice-rank-manual`` (always honored unless ``--force-all``), or
  - Auto-detect: Rank changed in Jira since the last script sync while RICE Score was unchanged
    (ignored only with ``--force-rank``).

State is stored in ``.rox_rice_rank_state.json`` (override with ``--state-file``).

Usage:
    python3 rox_rice_rank_sync.py --dry-run
    python3 rox_rice_rank_sync.py --apply
    python3 rox_rice_rank_sync.py --apply --target-version 5.0.0
    python3 rox_rice_rank_sync.py --apply --target-version 5.0.0 --only-if-changed
    python3 rox_rice_rank_sync.py --clear-manual ROX-31439 --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from jira_auth import is_jira_cloud_url, jira_api_token_from_env  # noqa: E402
from jira_feature_validator import JiraFeatureValidator  # noqa: E402

DEFAULT_TARGET_VERSION = "5.0.0"
DEFAULT_STATE_FILE = Path(__file__).parent / ".rox_rice_rank_state.json"
DEFAULT_MANUAL_LABEL = "rice-rank-manual"
DEFAULT_COHORT = "target-version"
RICE_EPSILON = 1e-9
# Jira Software Rank API rejects more than 50 issues per request
RANK_API_MAX_ISSUES = 50


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_rice_score(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    if isinstance(raw, dict):
        v = raw.get("value")
        if v is not None:
            return _parse_rice_score(v)
    return None


def _rice_scores_equal(a: Optional[float], b: Optional[float]) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= RICE_EPSILON


def _parse_reach_score(raw: Any) -> Optional[float]:
    """Parse Reach custom field (numeric)."""
    return _parse_rice_score(raw)


def _reach_tiebreaker_desc(
    key: str,
    reach_by_key: Optional[Dict[str, Optional[float]]],
) -> float:
    """Sort key for descending Reach among equal RICE (higher Reach → lower value → earlier)."""
    if not reach_by_key:
        return 0.0
    reach = reach_by_key.get(key)
    if reach is None:
        return 1e18  # missing Reach sorts after scored ties
    return -reach


def _reach_tiebreaker_asc(
    key: str,
    reach_by_key: Optional[Dict[str, Optional[float]]],
) -> float:
    """Sort key for ascending Reach among equal RICE (higher Reach → later in asc chain)."""
    if not reach_by_key:
        return 0.0
    reach = reach_by_key.get(key)
    if reach is None:
        return 1e18
    return reach


def build_blocks_map(
    features: List[Dict[str, Any]],
    cohort_keys: set[str],
) -> Dict[str, set[str]]:
    """Parse Jira issuelinks to find in-cohort "Blocks" relationships.

    Returns {blocker_key: {blocked_key, ...}}.
    """
    blocks: Dict[str, set[str]] = {}
    for issue in features:
        key = issue.get("key") or ""
        if not key or key not in cohort_keys:
            continue
        for link in (issue.get("fields") or {}).get("issuelinks") or []:
            link_type = (link.get("type") or {}).get("name", "")
            if link_type != "Blocks":
                continue
            out = (link.get("outwardIssue") or {}).get("key")
            if out and out in cohort_keys:
                blocks.setdefault(key, set()).add(out)
            inw = (link.get("inwardIssue") or {}).get("key")
            if inw and inw in cohort_keys:
                blocks.setdefault(inw, set()).add(key)
    return blocks


def promote_blockers(
    desc_ordered: List[str],
    blocks_map: Optional[Dict[str, set[str]]],
) -> Tuple[List[str], List[Tuple[str, int, int, str]]]:
    """Promote prerequisite issues above their dependents in a desc-sorted list.

    Returns (adjusted_list, [(blocker, old_pos, new_pos, blocked_key), ...]).
    """
    if not blocks_map:
        return desc_ordered, []
    result = list(desc_ordered)
    promotions: List[Tuple[str, int, int, str]] = []
    changed = True
    while changed:
        changed = False
        pos = {k: i for i, k in enumerate(result)}
        for blocker, blocked_set in blocks_map.items():
            if blocker not in pos:
                continue
            for blocked in blocked_set:
                if blocked not in pos:
                    continue
                if pos[blocker] > pos[blocked]:
                    old_pos = pos[blocker]
                    result.remove(blocker)
                    new_pos = result.index(blocked)
                    result.insert(new_pos, blocker)
                    promotions.append((blocker, old_pos + 1, new_pos + 1, blocked))
                    changed = True
                    break
            if changed:
                break
    return result, promotions


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  Could not read state file {path}: {e}")
        return {}


def save_state(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def version_bucket(state: Dict[str, Any], target_version: str) -> Dict[str, Any]:
    tv = target_version.strip()
    if tv not in state or not isinstance(state.get(tv), dict):
        state[tv] = {"issues": {}}
    bucket = state[tv]
    if "issues" not in bucket or not isinstance(bucket["issues"], dict):
        bucket["issues"] = {}
    return bucket


def issue_state_entry(bucket: Dict[str, Any], key: str) -> Dict[str, Any]:
    issues = bucket["issues"]
    if key not in issues or not isinstance(issues[key], dict):
        issues[key] = {}
    return issues[key]


def has_manual_label(labels: List[str], manual_label: str) -> bool:
    ml = (manual_label or "").strip()
    if not ml:
        return False
    return ml in {str(x).strip() for x in (labels or [])}


def is_rank_locked(
    entry: Dict[str, Any],
    labels: List[str],
    manual_label: str,
    *,
    force_state: bool = False,
    force_all: bool = False,
) -> bool:
    """
    Return True if this issue must not be moved by auto rank sync.

    ``rice-rank-manual`` (or ``JIRA_RANK_MANUAL_LABEL``) is always honored unless
    ``--force-all``. ``--force-rank`` only ignores auto-detected state locks
    (rank changed in Jira while RICE unchanged).
    """
    if force_all:
        return False
    if has_manual_label(labels, manual_label):
        return True
    if entry.get("manual_override") and not force_state:
        return True
    return False


def detect_manual_overrides(
    features: List[Dict[str, Any]],
    bucket: Dict[str, Any],
    rice_field: str,
    rank_field: str,
    manual_label: str,
) -> List[str]:
    """Mark issues whose Rank was changed manually (RICE unchanged since last sync)."""
    newly_locked: List[str] = []
    for issue in features:
        key = (issue.get("key") or "").strip()
        if not key:
            continue
        fields = issue.get("fields") or {}
        labels = list(fields.get("labels") or [])
        entry = issue_state_entry(bucket, key)

        if has_manual_label(labels, manual_label):
            if not entry.get("manual_override"):
                entry["manual_override"] = True
                entry["manual_reason"] = f"label:{manual_label}"
                newly_locked.append(key)
            continue

        if entry.get("manual_override"):
            continue

        prev_rank = entry.get("lexorank")
        if not prev_rank:
            continue

        current_rank = fields.get(rank_field)
        if not current_rank or current_rank == prev_rank:
            continue

        prev_rice = entry.get("rice_score")
        current_rice = _parse_rice_score(fields.get(rice_field))
        if _rice_scores_equal(prev_rice, current_rice):
            entry["manual_override"] = True
            entry["manual_reason"] = "rank_changed_rice_unchanged"
            newly_locked.append(key)

    return newly_locked


def detect_rice_score_changes(
    bucket: Dict[str, Any],
    rice_by_key: Dict[str, Optional[float]],
) -> List[Tuple[str, Optional[float], Optional[float]]]:
    """
    Compare current RICE scores to the last saved state.

    Returns (key, previous, current) for each change. ``previous`` is None when
    the issue was not in state or had no stored score (first score seen).
    """
    changes: List[Tuple[str, Optional[float], Optional[float]]] = []
    for key, current in sorted(rice_by_key.items()):
        entry = issue_state_entry(bucket, key)
        if "rice_score" not in entry and current is None:
            continue
        previous = entry.get("rice_score") if "rice_score" in entry else None
        if _rice_scores_equal(previous, current):
            continue
        changes.append((key, previous, current))
    return changes


def unlock_state_on_rice_change(
    bucket: Dict[str, Any],
    rice_changes: List[Tuple[str, Optional[float], Optional[float]]],
) -> List[str]:
    """
    Clear auto-detected rank locks when RICE changed so daily sync can re-rank.

    Does not touch issues locked via ``rice-rank-manual`` label (those are not
    in this list for re-rank anyway).
    """
    unlocked: List[str] = []
    changed_keys = {k for k, _, _ in rice_changes}
    for key in changed_keys:
        entry = issue_state_entry(bucket, key)
        reason = (entry.get("manual_reason") or "").strip()
        if reason.startswith("label:"):
            continue
        if entry.get("manual_override"):
            entry["manual_override"] = False
            entry.pop("manual_reason", None)
            unlocked.append(key)
    return unlocked


def write_rice_change_report(
    path: Path,
    target_version: str,
    changes: List[Tuple[str, Optional[float], Optional[float]]],
    *,
    run_at: str,
) -> None:
    """Append-friendly daily report of RICE score deltas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# RICE changes — {target_version} — {run_at}",
        f"count={len(changes)}",
        "",
        "key,previous,current",
    ]
    for key, prev, cur in changes:
        lines.append(f"{key},{prev if prev is not None else ''},{cur if cur is not None else ''}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sort_keys_by_rice_desc(
    keys: List[str],
    rice_by_key: Dict[str, Optional[float]],
    reach_by_key: Optional[Dict[str, Optional[float]]] = None,
) -> List[str]:
    """Highest RICE first; equal RICE → higher Reach first; missing RICE last."""

    def sort_key(k: str) -> Tuple[Any, ...]:
        score = rice_by_key.get(k)
        reach_tie = _reach_tiebreaker_desc(k, reach_by_key)
        if score is None:
            return (2, 0.0, reach_tie, k)
        if abs(score) <= RICE_EPSILON:
            return (1, 0.0, reach_tie, k)
        return (0, -score, reach_tie, k)

    return sorted(keys, key=sort_key)


def iter_rank_segments(
    rank_order: List[str],
    keys_to_rank: set[str],
    label_anchors: set[str],
) -> List[Dict[str, Any]]:
    """
    Split issues to reorder into runs between ``rice-rank-manual`` anchors.

    Issues not in ``keys_to_rank`` (state locks, etc.) are left untouched and break
    a segment without becoming anchors. Only ``label_anchors`` stay fixed in Jira.
    """
    segments: List[Dict[str, Any]] = []
    current: List[str] = []
    above_anchor: Optional[str] = None
    for key in rank_order:
        if key in label_anchors:
            if current:
                segments.append({"keys": list(current), "above": above_anchor, "below": key})
                current = []
            above_anchor = key
        elif key in keys_to_rank:
            current.append(key)
        else:
            if current:
                segments.append({"keys": list(current), "above": above_anchor, "below": None})
                current = []
    if current:
        segments.append({"keys": list(current), "above": above_anchor, "below": None})
    return segments


def rank_segment_after_anchor(
    session: requests.Session,
    jira_url: str,
    segment_keys: List[str],
    anchor: str,
    rank_field_id: str,
    rice_by_key: Dict[str, Optional[float]],
    reach_by_key: Optional[Dict[str, Optional[float]]] = None,
    blocks_map: Optional[Dict[str, set[str]]] = None,
) -> Tuple[bool, str]:
    """Place *segment_keys* directly under *anchor* (locked), highest RICE at top of segment."""
    if not segment_keys:
        return True, "empty"
    # High → low: first issue sits just below anchor (row # anchor+1 in Rank ASC).
    chain = sort_keys_by_rice_desc(segment_keys, rice_by_key, reach_by_key)
    chain, _ = promote_blockers(chain, blocks_map)
    prev = anchor
    for key in chain:
        ok, msg = rank_issues_batch(
            session, jira_url, [key], rank_field_id, rank_after=prev
        )
        if not ok:
            return False, f"segment after {anchor}, {key}: {msg}"
        prev = key
    return True, "ok"


def rank_segment_before_anchor(
    session: requests.Session,
    jira_url: str,
    segment_keys: List[str],
    anchor: str,
    rank_field_id: str,
    rice_by_key: Dict[str, Optional[float]],
    reach_by_key: Optional[Dict[str, Optional[float]]] = None,
    blocks_map: Optional[Dict[str, set[str]]] = None,
) -> Tuple[bool, str]:
    """Place *segment_keys* directly above *anchor* (locked), highest RICE at top of segment."""
    if not segment_keys:
        return True, "empty"
    desc = sort_keys_by_rice_desc(segment_keys, rice_by_key, reach_by_key)
    desc, _ = promote_blockers(desc, blocks_map)
    chain = list(reversed(desc))
    next_below = anchor
    for key in chain:
        ok, msg = rank_issues_batch(
            session, jira_url, [key], rank_field_id, rank_before=next_below
        )
        if not ok:
            return False, f"segment before {anchor}, {key}: {msg}"
        next_below = key
    return True, "ok"


def _redistribute_for_segments(
    rank_order: List[str],
    keys_to_rank: set[str],
    rice_by_key: Dict[str, Optional[float]],
    reach_by_key: Optional[Dict[str, Optional[float]]] = None,
    blocks_map: Optional[Dict[str, set[str]]] = None,
) -> List[str]:
    """Reassign rankable issues to RICE-correct segments before splitting.

    Anchors and state-locked issues stay at their current positions.
    Rankable issue slots are filled in RICE-descending order so each issue
    lands in the segment that matches its score, not its stale Jira position.
    """
    rice_sorted = sort_keys_by_rice_desc(list(keys_to_rank), rice_by_key, reach_by_key)
    rice_sorted, _ = promote_blockers(rice_sorted, blocks_map)
    rice_iter = iter(rice_sorted)
    result = []
    for key in rank_order:
        if key in keys_to_rank:
            nxt = next(rice_iter, None)
            if nxt is not None:
                result.append(nxt)
        else:
            result.append(key)
    for remaining in rice_iter:
        result.append(remaining)
    return result


def apply_rice_rank_segments(
    session: requests.Session,
    jira_url: str,
    rank_order: List[str],
    keys_to_rank: set[str],
    label_anchors: set[str],
    rank_field_id: str,
    rice_by_key: Dict[str, Optional[float]],
    cohort_jql: str,
    is_cloud: bool,
    dry_run: bool,
    reach_by_key: Optional[Dict[str, Optional[float]]] = None,
    blocks_map: Optional[Dict[str, set[str]]] = None,
) -> Tuple[bool, str]:
    """
    Reorder only ``keys_to_rank``; issues with ``rice-rank-manual`` stay fixed as anchors.
    """
    rank_order = _redistribute_for_segments(
        rank_order, keys_to_rank, rice_by_key, reach_by_key, blocks_map,
    )
    segments = iter_rank_segments(rank_order, keys_to_rank, label_anchors)
    unlocked_count = sum(len(s["keys"]) for s in segments)
    if unlocked_count == 0:
        return True, "no unlocked issues to rank"

    if dry_run:
        print(
            f"   [dry-run] segmented rank: {len(segments)} segment(s), "
            f"{unlocked_count} to rank, {len(label_anchors)} {DEFAULT_MANUAL_LABEL!r} anchor(s)"
        )
        for i, seg in enumerate(segments, 1):
            keys = seg["keys"]
            if not keys:
                continue
            seg_desc = sort_keys_by_rice_desc(keys, rice_by_key, reach_by_key)
            seg_desc, _ = promote_blockers(seg_desc, blocks_map)
            top = seg_desc[0]
            top_reach = (reach_by_key or {}).get(top)
            reach_note = f", Reach={top_reach}" if top_reach is not None else ""
            print(
                f"      segment {i}: {len(keys)} issues, "
                f"anchor above={seg['above'] or '-'}, below={seg['below'] or '-'}, "
                f"top RICE={top} ({rice_by_key.get(top)}{reach_note})"
            )
        return True, "dry-run"

    ranked = 0
    for seg in segments:
        keys = seg["keys"]
        if not keys:
            continue
        if seg["above"]:
            ok, msg = rank_segment_after_anchor(
                session,
                jira_url,
                keys,
                seg["above"],
                rank_field_id,
                rice_by_key,
                reach_by_key,
                blocks_map,
            )
        elif seg["below"]:
            ok, msg = rank_segment_before_anchor(
                session,
                jira_url,
                keys,
                seg["below"],
                rank_field_id,
                rice_by_key,
                reach_by_key,
                blocks_map,
            )
        else:
            seg_desc = sort_keys_by_rice_desc(keys, rice_by_key, reach_by_key)
            seg_desc, _ = promote_blockers(seg_desc, blocks_map)
            ok, msg = apply_rice_rank_order(
                session,
                jira_url,
                seg_desc,
                cohort_jql,
                is_cloud,
                rank_field_id,
                rice_by_key,
                dry_run=False,
                rank_view="asc",
                reach_by_key=reach_by_key,
                blocks_map=blocks_map,
            )
        if not ok:
            return False, msg
        ranked += len(keys)
    return True, (
        f"segment-ranked {ranked} issues ({len(label_anchors)} anchor(s) unchanged)"
    )


def sort_keys_by_rice_asc(
    keys: List[str],
    rice_by_key: Dict[str, Optional[float]],
    reach_by_key: Optional[Dict[str, Optional[float]]] = None,
) -> List[str]:
    """
    Bottom-of-backlog → top-of-backlog order for ``rankBeforeIssue`` batches.

    Jira places the **last** issue in the array highest on the board. We want:
    no RICE / 0 at the bottom, highest RICE at the top; equal RICE → higher Reach later.
    """

    def sort_key(k: str) -> Tuple[Any, ...]:
        score = rice_by_key.get(k)
        reach_tie = _reach_tiebreaker_asc(k, reach_by_key)
        if score is None:
            return (0, 0.0, reach_tie, k)  # bottom
        if abs(score) <= RICE_EPSILON:
            return (1, 0.0, reach_tie, k)
        return (2, score, reach_tie, k)  # ascending positive scores

    return sorted(keys, key=sort_key)


def _rank_custom_field_param(rank_field_id: str) -> Optional[str]:
    """Agile Rank API expects numeric id (e.g. 10019), not customfield_10019."""
    fid = (rank_field_id or "").strip()
    if not fid:
        return None
    if fid.startswith("customfield_"):
        return fid.replace("customfield_", "", 1)
    return fid


def rank_issues_batch(
    session: requests.Session,
    jira_url: str,
    issue_keys: List[str],
    rank_field_id: str,
    *,
    rank_after: Optional[str] = None,
    rank_before: Optional[str] = None,
) -> Tuple[bool, str]:
    """Rank issues in array order via Jira Software Rank API (one request)."""
    if not issue_keys:
        return True, "empty"
    url = f"{jira_url.rstrip('/')}/rest/agile/1.0/issue/rank"
    body: Dict[str, Any] = {"issues": issue_keys}
    cf = _rank_custom_field_param(rank_field_id)
    if cf:
        body["rankCustomFieldId"] = cf
    if rank_before:
        body["rankBeforeIssue"] = rank_before
    elif rank_after:
        body["rankAfterIssue"] = rank_after
    else:
        return False, "need rank_before or rank_after"
    resp = session.put(url, json=body, timeout=120)
    if resp.status_code in (200, 204):
        return True, "ok"
    return False, f"{resp.status_code} {resp.text[:400]}"


def cohort_label_for_version(target_version: str) -> str:
    """Jira label used for plan cohorts (e.g. 5.0.0)."""
    return target_version.strip()


def build_cohort_jql(project_key: str, target_version: str, cohort: str) -> str:
    """
    JQL for the backlog cohort.

    ``target-version`` (default): ``Target Version`` field only.

    ``both``: Target Version OR matching ``labels`` value.
    """
    tv = target_version.strip()
    label = cohort_label_for_version(tv)
    base = (
        f'project = {project_key} AND type = feature AND statusCategory != Done'
    )
    mode = (cohort or DEFAULT_COHORT).strip().lower()
    if mode == "target-version":
        return f'{base} AND "Target Version" = "{tv}"'
    if mode == "label":
        return f'{base} AND labels = "{label}"'
    if mode == "both":
        return f'{base} AND ("Target Version" = "{tv}" OR labels = "{label}")'
    raise ValueError(f"Unknown cohort mode: {cohort!r} (use both, target-version, or label)")


def fetch_cohort_features(
    session: requests.Session,
    jira_url: str,
    jql: str,
    fields_param: str,
    is_cloud: bool,
) -> List[Dict[str, Any]]:
    """Paginate JQL and return issue dicts."""
    features: List[Dict[str, Any]] = []
    if is_cloud:
        url = f"{jira_url.rstrip('/')}/rest/api/3/search/jql"
        token = None
        while True:
            params: Dict[str, Any] = {
                "jql": jql,
                "maxResults": 50,
                "fields": fields_param,
            }
            if token:
                params["nextPageToken"] = token
            r = session.get(url, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            features.extend(data.get("issues") or [])
            if data.get("isLast", True):
                break
            token = data.get("nextPageToken")
            if not token:
                break
    else:
        url = f"{jira_url.rstrip('/')}/rest/api/2/search"
        start = 0
        while True:
            r = session.get(
                url,
                params={
                    "jql": jql,
                    "startAt": start,
                    "maxResults": 50,
                    "fields": fields_param,
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            chunk = data.get("issues") or []
            features.extend(chunk)
            if len(chunk) < 50:
                break
            start += 50
    return features


def _search_jql_all_keys(
    session: requests.Session,
    jira_url: str,
    jql: str,
    is_cloud: bool,
    fields: str = "key",
) -> List[str]:
    """Paginate JQL and return issue keys in result order."""
    keys: List[str] = []
    if is_cloud:
        url = f"{jira_url.rstrip('/')}/rest/api/3/search/jql"
        token = None
        while True:
            params: Dict[str, Any] = {"jql": jql, "maxResults": 50, "fields": fields}
            if token:
                params["nextPageToken"] = token
            r = session.get(url, params=params, timeout=60)
            if r.status_code != 200:
                break
            data = r.json()
            for iss in data.get("issues") or []:
                k = iss.get("key")
                if k:
                    keys.append(k)
            if data.get("isLast", True):
                break
            token = data.get("nextPageToken")
            if not token:
                break
    else:
        url = f"{jira_url.rstrip('/')}/rest/api/2/search"
        start = 0
        while True:
            r = session.get(
                url,
                params={"jql": jql, "startAt": start, "maxResults": 50, "fields": fields},
                timeout=60,
            )
            if r.status_code != 200:
                break
            data = r.json()
            chunk = data.get("issues") or []
            for iss in chunk:
                k = iss.get("key")
                if k:
                    keys.append(k)
            if len(chunk) < 50:
                break
            start += 50
    return keys


def cohort_rank_edge_keys(
    session: requests.Session,
    jira_url: str,
    cohort_jql: str,
    is_cloud: bool,
) -> Tuple[str, str]:
    """Return (board_top_key, board_bottom_key) for the cohort via JQL Rank order."""
    desc = _search_jql_all_keys(
        session, jira_url, f"{cohort_jql} ORDER BY Rank DESC", is_cloud, fields="key"
    )
    asc = _search_jql_all_keys(
        session, jira_url, f"{cohort_jql} ORDER BY Rank ASC", is_cloud, fields="key"
    )
    return (desc[0] if desc else "", asc[0] if asc else "")


def load_keys_from_plan_csv(path: Path) -> List[str]:
    """Load issue keys from a Jira plan export (``Work item key`` column)."""
    keys: List[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("Work item key") or "").strip()
            if key:
                keys.append(key)
    return keys


def apply_rice_rank_order(
    session: requests.Session,
    jira_url: str,
    ordered_top_to_bottom: List[str],
    cohort_jql: str,
    is_cloud: bool,
    rank_field_id: str,
    rice_by_key: Dict[str, Optional[float]],
    dry_run: bool,
    *,
    rank_view: str = "asc",
    reach_by_key: Optional[Dict[str, Optional[float]]] = None,
    blocks_map: Optional[Dict[str, set[str]]] = None,
) -> Tuple[bool, str]:
    """
    Order Rank to match RICE (high → low); equal RICE → higher Reach first.

    **asc** (default): Jira Product Discovery global plan / ``ORDER BY Rank ASC``.
    No-RICE keys are batched to the plan tail, then scored keys are chained ``rankAfterIssue``
    from the tail in ``sort_keys_by_rice_desc`` order (highest RICE → row #1), then no-RICE
    keys are chained again after the last scored issue.

    **desc**: ``ORDER BY Rank DESC`` boards; batched ascending before refreshed Rank-DESC #1.
    """
    keys = ordered_top_to_bottom
    if len(keys) < 2:
        return True, "only one issue — nothing to reorder"

    scored_keys = [k for k in keys if rice_by_key.get(k) is not None]
    positive_rice_keys = [
        k for k in scored_keys if (rice_by_key.get(k) or 0) > RICE_EPSILON
    ]
    zero_rice_keys = sort_keys_by_rice_asc(
        [k for k in scored_keys if abs(rice_by_key.get(k) or 0) <= RICE_EPSILON],
        rice_by_key,
        reach_by_key,
    )
    rice_keys_asc = sort_keys_by_rice_asc(positive_rice_keys, rice_by_key, reach_by_key)
    no_rice_keys = sort_keys_by_rice_asc(
        [k for k in keys if rice_by_key.get(k) is None], rice_by_key, reach_by_key
    )
    rice_keys_desc = list(reversed(rice_keys_asc))
    view = (rank_view or "asc").strip().lower()

    if view == "desc":
        anchor, _ = cohort_rank_edge_keys(session, jira_url, cohort_jql, is_cloud)
        anchor_label = "Rank-DESC #1"
        expected_top = rice_keys_desc[0] if rice_keys_desc else keys[0]
        move_no_rice = False
    else:
        _, anchor = cohort_rank_edge_keys(session, jira_url, cohort_jql, is_cloud)
        anchor_label = "Rank-ASC #1"
        expected_top = rice_keys_desc[0] if rice_keys_desc else keys[0]
        move_no_rice = False  # no-RICE pass disrupts ASC ordering; leave at plan bottom

    if dry_run:
        print(
            f"   [dry-run] view={view}: rankAfter chain {len(keys)} keys (RICE desc), "
            f"then {len(no_rice_keys)} no-RICE to plan tail"
        )
        print(
            f"   [dry-run] {len(no_rice_keys)} without RICE — "
            f"{'rankAfter tail' if move_no_rice else 'unchanged'}"
        )
        print(f"   [dry-run] expected row #1 (highest RICE): {expected_top}")
        return True, "dry-run"

    if not anchor:
        return False, f"could not determine {anchor_label} anchor for cohort"

    ranked = 0
    if view == "asc":
        scored_chain = sort_keys_by_rice_desc(
            [k for k in keys if rice_by_key.get(k) is not None],
            rice_by_key,
            reach_by_key,
        )
        scored_chain, _ = promote_blockers(scored_chain, blocks_map)
        _, tail_anchor = cohort_rank_edge_keys(
            session, jira_url, cohort_jql, is_cloud
        )
        if not tail_anchor:
            return False, "could not determine Rank-DESC #1 (plan tail) for rankAfter chain"
        # New no-RICE issues often land at row #1; park them at the tail before scored reorder.
        if no_rice_keys:
            ok, msg = rank_issues_batch(
                session,
                jira_url,
                no_rice_keys,
                rank_field_id,
                rank_after=tail_anchor,
            )
            if not ok:
                return False, f"no-RICE pre-pass after {tail_anchor}: {msg}"
            ranked += len(no_rice_keys)
            print(
                f"   … no-RICE pre-pass: {len(no_rice_keys)} issues to plan tail "
                f"(before scored reorder)"
            )
            _, tail_anchor = cohort_rank_edge_keys(
                session, jira_url, cohort_jql, is_cloud
            )
            if not tail_anchor:
                return False, "could not determine plan tail after no-RICE pre-pass"
        prev_anchor = tail_anchor
        total = len(scored_chain)
        for i, key in enumerate(scored_chain, 1):
            ok, msg = rank_issues_batch(
                session,
                jira_url,
                [key],
                rank_field_id,
                rank_after=prev_anchor,
            )
            if not ok:
                return False, f"rank {key} after {prev_anchor}: {msg}"
            ranked += 1
            prev_anchor = key
            if i == 1 or i == total or i % 15 == 0:
                print(
                    f"   … rankAfter chain {i}/{total}: {key} "
                    f"(RICE={rice_by_key.get(key)})"
                )
        if no_rice_keys:
            anchor = prev_anchor  # last issue placed by scored chain
            for key in sorted(no_rice_keys):
                ok, msg = rank_issues_batch(
                    session,
                    jira_url,
                    [key],
                    rank_field_id,
                    rank_after=anchor,
                )
                if not ok:
                    return False, f"no-RICE {key} after {anchor}: {msg}"
                ranked += 1
                anchor = key
            print(
                f"   … no-RICE: {len(no_rice_keys)} issues chained after "
                f"{prev_anchor} (last scored)"
            )
    else:
        for start in range(0, len(rice_keys_asc), RANK_API_MAX_ISSUES):
            chunk = rice_keys_asc[start : start + RANK_API_MAX_ISSUES]
            batch_anchor, _ = cohort_rank_edge_keys(
                session, jira_url, cohort_jql, is_cloud
            )
            if not batch_anchor:
                return False, f"could not determine {anchor_label} for batch"
            ok, msg = rank_issues_batch(
                session, jira_url, chunk, rank_field_id, rank_before=batch_anchor
            )
            if not ok:
                return False, f"RICE batch before {batch_anchor}: {msg}"
            ranked += len(chunk)
            print(
                f"   … RICE batch {start // RANK_API_MAX_ISSUES + 1}: "
                f"{ranked}/{len(rice_keys_asc)} (highest in batch: {chunk[-1]})"
            )

    print(
        f"   ✅ Ranked {ranked} issues (view={view}); "
        f"expected row #1: {expected_top}"
    )
    return True, f"ranked {ranked} issues; top RICE {expected_top}"


def count_rice_rank_misalignment(
    session: requests.Session,
    jira_url: str,
    keys: List[str],
    rice_by_key: Dict[str, Optional[float]],
    rank_field: str,
    rice_field: str,
    is_cloud: bool,
    reach_field: str = "",
) -> int:
    """Count adjacent pairs out of RICE (then Reach) order among unlocked issues."""
    if len(keys) < 2:
        return 0
    fields_param = f"{rank_field},{rice_field}"
    if reach_field:
        fields_param += f",{reach_field}"
    jql = "key in (" + ",".join(keys) + ") ORDER BY Rank ASC"
    ordered_keys = _search_jql_all_keys(session, jira_url, jql, is_cloud, fields_param)
    fresh_rice: Dict[str, Optional[float]] = {}
    fresh_reach: Dict[str, Optional[float]] = {}
    if ordered_keys:
        if is_cloud:
            url = f"{jira_url.rstrip('/')}/rest/api/3/search/jql"
            r = session.get(
                url,
                params={"jql": jql, "maxResults": len(keys) + 5, "fields": fields_param},
                timeout=60,
            )
        else:
            url = f"{jira_url.rstrip('/')}/rest/api/2/search"
            r = session.get(
                url,
                params={"jql": jql, "maxResults": len(keys) + 5, "fields": fields_param},
                timeout=60,
            )
        if r.status_code == 200:
            fresh_rice = {}
            fresh_reach = {}
            for iss in r.json().get("issues") or []:
                k = iss.get("key")
                if k:
                    fld = iss.get("fields") or {}
                    fresh_rice[k] = _parse_rice_score(fld.get(rice_field))
                    if reach_field:
                        fresh_reach[k] = _parse_reach_score(fld.get(reach_field))

    bad = 0
    for i in range(len(ordered_keys) - 1):
        a, b = ordered_keys[i], ordered_keys[i + 1]
        ra, rb = fresh_rice.get(a), fresh_rice.get(b)
        if ra is None or rb is None:
            continue
        if ra + RICE_EPSILON < rb:
            bad += 1
            continue
        if _rice_scores_equal(ra, rb) and reach_field:
            rha, rhb = fresh_reach.get(a), fresh_reach.get(b)
            if rha is not None and rhb is not None and rha + RICE_EPSILON < rhb:
                bad += 1
    return bad


def refresh_issue_fields(
    validator: JiraFeatureValidator,
    keys: List[str],
    fields_param: str,
) -> Dict[str, Dict[str, Any]]:
    """Fetch fresh rank + rice for given keys."""
    out: Dict[str, Dict[str, Any]] = {}
    api_version = getattr(validator, "api_version", "3")
    for key in keys:
        url = f"{validator.jira_url.rstrip('/')}/rest/api/{api_version}/issue/{key}"
        try:
            r = validator.session.get(url, params={"fields": fields_param}, timeout=30)
            if r.status_code == 200:
                out[key] = r.json().get("fields") or {}
        except Exception as e:
            print(f"   ⚠️  Refresh {key}: {e}")
    return out


def update_state_from_features(
    bucket: Dict[str, Any],
    features: List[Dict[str, Any]],
    rice_field: str,
    rank_field: str,
) -> None:
    for issue in features:
        key = (issue.get("key") or "").strip()
        if not key:
            continue
        fields = issue.get("fields") or {}
        entry = issue_state_entry(bucket, key)
        entry["rice_score"] = _parse_rice_score(fields.get(rice_field))
        entry["lexorank"] = fields.get(rank_field) or ""
        entry["last_seen_at"] = _utc_now_iso()
        if entry.get("manual_override"):
            entry["last_synced_at"] = entry.get("last_synced_at") or entry["last_seen_at"]
        else:
            entry["last_synced_at"] = _utc_now_iso()
    bucket["last_run_at"] = _utc_now_iso()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Order ROX Feature Rank by RICE Score (respect manual rank overrides)",
    )
    parser.add_argument(
        "--target-version",
        default=DEFAULT_TARGET_VERSION,
        help=f"Target Version filter (default: {DEFAULT_TARGET_VERSION})",
    )
    parser.add_argument(
        "--cohort",
        choices=("both", "target-version", "label"),
        default=os.getenv("JIRA_RANK_COHORT", DEFAULT_COHORT),
        help=(
            "Which issues to include: target-version field only (default), "
            "labels only, or both Target Version and labels"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned rank order only (no Jira updates)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply rank updates via Jira Rank API",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"JSON state for manual-override tracking (default: {DEFAULT_STATE_FILE.name})",
    )
    parser.add_argument(
        "--manual-label",
        default=os.getenv("JIRA_RANK_MANUAL_LABEL", DEFAULT_MANUAL_LABEL),
        help=f"Jira label that locks rank (default: {DEFAULT_MANUAL_LABEL})",
    )
    parser.add_argument(
        "--clear-manual",
        action="append",
        default=[],
        metavar="ROX-NNNN",
        help="Clear manual override for issue key(s) before run (repeatable)",
    )
    parser.add_argument(
        "--force-rank",
        action="store_true",
        help=(
            "Ignore auto-detected manual overrides in state (rank changed, RICE unchanged). "
            "Does not override the rice-rank-manual label."
        ),
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help=(
            "Dangerous: rank every issue in the cohort, ignoring rice-rank-manual labels "
            "and state locks. Use only when you intend to reset the whole plan."
        ),
    )
    parser.add_argument(
        "--only-if-changed",
        action="store_true",
        help=(
            "With --apply: skip Rank API unless RICE scores changed since last run or "
            "unlocked issues are still out of RICE order (for daily automation)."
        ),
    )
    parser.add_argument(
        "--change-report",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write CSV of RICE score changes vs last state (default with --only-if-changed: "
            "output/rice_changes_<version>_<date>.csv)"
        ),
    )
    parser.add_argument(
        "--rank-view",
        choices=("asc", "desc"),
        default=os.getenv("JIRA_RANK_VIEW", "asc"),
        help="Plan sort direction: asc = JPD global plan # column (default); desc = Rank DESC boards",
    )
    parser.add_argument(
        "--plan-csv",
        type=Path,
        default=None,
        metavar="PATH",
        help="Jira plan export CSV — rank only keys listed (matches global plan scope)",
    )
    parser.add_argument(
        "--ignore-links",
        action="store_true",
        help="Do not promote prerequisites above their dependents (ignore Jira Blocks links)",
    )
    parser.add_argument(
        "--jira-url",
        default=os.getenv("JIRA_BASE_URL", "https://redhat.atlassian.net"),
    )
    parser.add_argument("--email", default=os.getenv("JIRA_EMAIL", ""))
    parser.add_argument("--token", default=jira_api_token_from_env())
    args = parser.parse_args()

    if not args.token:
        print("❌ JIRA_TOKEN or JIRA_API_TOKEN not set")
        return 1
    if is_jira_cloud_url(args.jira_url) and not (args.email or "").strip():
        print("❌ Jira Cloud requires JIRA_EMAIL")
        return 1
    if args.dry_run and args.apply:
        print("⚠️  --dry-run wins over --apply")
    if args.force_all and args.force_rank:
        print("⚠️  --force-all wins over --force-rank")
    do_apply = bool(args.apply) and not args.dry_run
    force_state = bool(args.force_rank) and not args.force_all
    force_all = bool(args.force_all)

    if not is_jira_cloud_url(args.jira_url):
        print(
            "❌ Rank sync uses the Jira Software Rank API (LexoRank). "
            "Point JIRA_BASE_URL at your Cloud site (atlassian.net)."
        )
        return 1

    validator = JiraFeatureValidator(
        jira_url=args.jira_url,
        email=args.email,
        api_token=args.token,
        project_key="ROX",
        target_version=args.target_version,
    )
    if not validator.test_connection():
        return 1

    rank_field = validator.rank_field_id()
    rice_field = validator.rice_score_field_id()
    if not rank_field:
        print("❌ Rank field not found. Set JIRA_RANK_FIELD in .env")
        return 1
    if not rice_field:
        print("❌ RICE Score field not found. Set JIRA_RICE_SCORE_FIELD in .env")
        return 1

    state_path = args.state_file
    state = load_state(state_path)
    bucket = version_bucket(state, args.target_version)

    for raw_key in args.clear_manual:
        k = raw_key.strip().upper()
        if not k:
            continue
        entry = issue_state_entry(bucket, k)
        entry["manual_override"] = False
        entry.pop("manual_reason", None)
        print(f"🔓 Cleared manual override for {k}")

    is_cloud = getattr(validator, "is_cloud", is_jira_cloud_url(validator.jira_url))
    plan_csv_keys: Optional[List[str]] = None
    if args.plan_csv:
        plan_path = args.plan_csv.expanduser()
        if not plan_path.is_file():
            print(f"❌ Plan CSV not found: {plan_path}")
            return 1
        plan_csv_keys = load_keys_from_plan_csv(plan_path)
        if not plan_csv_keys:
            print(f"❌ No issue keys in plan CSV: {plan_path}")
            return 1
        cohort_jql = "key in (" + ",".join(plan_csv_keys) + ")"
        print(f"🔍 Plan CSV ({plan_path.name}): {len(plan_csv_keys)} issues")
    else:
        try:
            cohort_jql = build_cohort_jql(
                validator.project_key, args.target_version, args.cohort
            )
        except ValueError as e:
            print(f"❌ {e}")
            return 1
        print(f"🔍 Cohort ({args.cohort}): {cohort_jql}")

    reach_field = (validator.rice_field_ids().get("reach") or "").strip()
    fields_param = f"labels,{rank_field},{rice_field}"
    if not args.ignore_links:
        fields_param += ",issuelinks"
    if reach_field:
        fields_param += f",{reach_field}"
    else:
        print("   ⚠️  Reach field not found — tie-break by Reach disabled")
    try:
        features = fetch_cohort_features(
            validator.session,
            validator.jira_url,
            cohort_jql,
            fields_param,
            is_cloud,
        )
    except Exception as e:
        print(f"❌ Error fetching cohort: {e}")
        return 1
    print(f"📊 Found {len(features)} features in cohort")
    if plan_csv_keys:
        plan_set = set(plan_csv_keys)
        features = [iss for iss in features if iss.get("key") in plan_set]
        print(f"   Matched {len(features)} plan CSV keys in Jira")
    if not features:
        print("⚠️  No features found")
        return 0
    rice_by_key: Dict[str, Optional[float]] = {}
    reach_by_key: Dict[str, Optional[float]] = {}
    lexo_by_key: Dict[str, str] = {}
    labels_by_key: Dict[str, List[str]] = {}

    for issue in features:
        key = issue.get("key") or ""
        fields = issue.get("fields") or {}
        rice_by_key[key] = _parse_rice_score(fields.get(rice_field))
        if reach_field:
            reach_by_key[key] = _parse_reach_score(fields.get(reach_field))
        lexo_by_key[key] = (fields.get(rank_field) or "").strip()
        labels_by_key[key] = list(fields.get("labels") or [])

    blocks_map: Optional[Dict[str, set[str]]] = None
    if not args.ignore_links:
        blocks_map = build_blocks_map(features, set(rice_by_key.keys()))

    rice_changes = detect_rice_score_changes(bucket, rice_by_key)
    unlocked = unlock_state_on_rice_change(bucket, rice_changes)
    if rice_changes:
        print(f"\n📈 RICE score changes since last run: {len(rice_changes)}")
        for key, prev, cur in rice_changes[:20]:
            prev_s = "—" if prev is None else str(prev)
            print(f"   {key}: {prev_s} → {cur}")
        if len(rice_changes) > 20:
            print(f"   … and {len(rice_changes) - 20} more")
    else:
        print("\n📈 RICE score changes since last run: none")
    if unlocked:
        print(f"   🔓 Cleared state rank lock (RICE changed): {', '.join(unlocked)}")

    report_path = args.change_report
    if report_path is None and args.only_if_changed:
        date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
        ver_tag = args.target_version.replace(".", "_")
        report_path = (
            Path(__file__).parent / "output" / f"rice_changes_{ver_tag}_{date_tag}.csv"
        )
    if report_path is not None:
        write_rice_change_report(
            report_path.expanduser(),
            args.target_version,
            rice_changes,
            run_at=_utc_now_iso(),
        )
        print(f"   📄 Change report: {report_path}")

    newly_locked = detect_manual_overrides(
        features, bucket, rice_field, rank_field, args.manual_label
    )
    if newly_locked:
        print(
            f"🔒 Detected manual rank change (RICE unchanged) — will not auto-rank: "
            + ", ".join(newly_locked)
        )
        print(f"   Clear with: python3 rox_rice_rank_sync.py --clear-manual KEY --apply")

    auto_keys: List[str] = []
    skipped_manual: List[str] = []
    skipped_no_rice: List[str] = []

    label_locked: List[str] = []
    state_locked: List[str] = []
    for key in rice_by_key:
        entry = issue_state_entry(bucket, key)
        labels = labels_by_key.get(key, [])
        if has_manual_label(labels, args.manual_label):
            label_locked.append(key)
        elif entry.get("manual_override"):
            state_locked.append(key)

    for key in rice_by_key:
        entry = issue_state_entry(bucket, key)
        labels = labels_by_key.get(key, [])
        if is_rank_locked(
            entry, labels, args.manual_label, force_state=force_state, force_all=force_all
        ):
            skipped_manual.append(key)
            continue
        if rice_by_key[key] is None:
            skipped_no_rice.append(key)
        auto_keys.append(key)

    ascending = sort_keys_by_rice_asc(auto_keys, rice_by_key, reach_by_key or None)
    ordered = list(reversed(ascending))  # display: highest RICE first
    ordered, link_promotions = promote_blockers(ordered, blocks_map)

    scope = f"plan CSV" if plan_csv_keys else f"{args.target_version} (cohort={args.cohort})"
    print(f"\n📋 {scope}: {len(features)} features (rank-view={args.rank_view})")
    print(
        f"   Ranked by RICE"
        + (" + Reach tie-break" if reach_field else "")
        + f" (incl. no-score at bottom): {len(ordered)}"
    )
    print(f"   Skipped (manual lock): {len(skipped_manual)}")
    if label_locked:
        print(f"      — {len(label_locked)} with {args.manual_label!r} label")
    if state_locked and not force_state and not force_all:
        print(f"      — {len(state_locked)} auto-detected (state)")
    if force_all:
        print("   ⚠️  --force-all: ranking entire cohort; all manual locks ignored")
    elif force_state and state_locked:
        print(
            f"   ⚠️  --force-rank: ignoring {len(state_locked)} state lock(s); "
            f"{len(label_locked)} issue(s) with {args.manual_label!r} still skipped"
        )
    print(f"   Of those, without RICE Score: {len(skipped_no_rice)}")
    if link_promotions:
        print(f"\n   🔗 Dependency promotions (Blocks links): {len(link_promotions)}")
        for blocker, old_pos, new_pos, blocked in link_promotions:
            print(f"      {blocker} promoted #{old_pos} → #{new_pos} (blocks {blocked})")
    elif blocks_map:
        dep_count = sum(len(v) for v in blocks_map.values())
        if dep_count:
            print(f"   🔗 {dep_count} Blocks link(s) in cohort — all already in order")
    print("\n   Planned order (top = highest RICE):")
    for i, key in enumerate(ordered[:25], 1):
        reach_s = reach_by_key.get(key) if reach_field else None
        extra = f"  Reach={reach_s}" if reach_s is not None else ""
        print(f"      {i:3}. {key}  RICE={rice_by_key[key]}{extra}")
    if len(ordered) > 25:
        print(f"      … and {len(ordered) - 25} more")

    if not ordered:
        print("\n⚠️  Nothing to rank")
        update_state_from_features(bucket, features, rice_field, rank_field)
        bucket["last_run_at"] = _utc_now_iso()
        save_state(state_path, state)
        return 0

    top_key, bottom_key = cohort_rank_edge_keys(
        validator.session, validator.jira_url, cohort_jql, is_cloud
    )
    if args.rank_view == "desc":
        print(f"   Fixed rankBefore anchor (Rank-DESC #1): {top_key or '?'}")
    else:
        print(f"   Fixed rankBefore anchor (Rank-ASC #1): {bottom_key or '?'}")
    print(f"   Rank tail (no-RICE): Rank-ASC last")

    rank_order = _search_jql_all_keys(
        validator.session,
        validator.jira_url,
        f"{cohort_jql} ORDER BY Rank ASC",
        is_cloud,
        fields="key",
    )
    label_anchors = set(label_locked)
    keys_to_rank = set(auto_keys)
    use_segments = bool(label_anchors) and args.rank_view == "asc"
    reach_for_sort = reach_by_key if reach_field else None

    if not do_apply:
        if use_segments:
            apply_rice_rank_segments(
                validator.session,
                validator.jira_url,
                rank_order,
                keys_to_rank,
                label_anchors,
                rank_field,
                rice_by_key,
                cohort_jql,
                is_cloud,
                dry_run=True,
                reach_by_key=reach_for_sort,
                blocks_map=blocks_map,
            )
        else:
            apply_rice_rank_order(
                validator.session,
                validator.jira_url,
                ordered,
                cohort_jql,
                is_cloud,
                rank_field,
                rice_by_key,
                dry_run=True,
                rank_view=args.rank_view,
                reach_by_key=reach_for_sort,
                blocks_map=blocks_map,
            )
        print("\n💡 Dry run — use --apply to update Rank in Jira")
        return 0

    locked_rank_before = {k: rank_order.index(k) for k in label_anchors if k in rank_order}

    misaligned_before = 0
    if keys_to_rank:
        misaligned_before = count_rice_rank_misalignment(
            validator.session,
            validator.jira_url,
            list(keys_to_rank),
            rice_by_key,
            rank_field,
            rice_field,
            is_cloud,
            reach_field,
        )

    if args.only_if_changed and not rice_changes and misaligned_before == 0:
        print(
            "\n⏭️  Skipping rank apply (--only-if-changed): no RICE changes and "
            "unlocked issues already match RICE order"
        )
        update_state_from_features(bucket, features, rice_field, rank_field)
        save_state(state_path, state)
        return 0

    if args.only_if_changed and misaligned_before > 0 and not rice_changes:
        print(
            f"\n📤 Applying rank ({misaligned_before} unlocked issue(s) out of RICE order, "
            "no score changes)"
        )

    print("\n📤 Applying rank order via Jira Software Rank API…")
    if use_segments:
        print(
            f"   Using segment ranking ({len(label_anchors)} {args.manual_label!r} anchor(s) fixed in place)"
        )
        ok, msg = apply_rice_rank_segments(
            validator.session,
            validator.jira_url,
            rank_order,
            keys_to_rank,
            label_anchors,
            rank_field,
            rice_by_key,
            cohort_jql,
            is_cloud,
            dry_run=False,
            reach_by_key=reach_for_sort,
            blocks_map=blocks_map,
        )
    else:
        ok, msg = apply_rice_rank_order(
            validator.session,
            validator.jira_url,
            ordered,
            cohort_jql,
            is_cloud,
            rank_field,
            rice_by_key,
            dry_run=False,
            rank_view=args.rank_view,
            reach_by_key=reach_for_sort,
            blocks_map=blocks_map,
        )
    if not ok:
        print(f"   ❌ Rank update failed: {msg}")
        return 1
    print(f"   ✅ {msg}")

    if locked_rank_before:
        rank_order_after = _search_jql_all_keys(
            validator.session,
            validator.jira_url,
            f"{cohort_jql} ORDER BY Rank ASC",
            is_cloud,
            fields="key",
        )
        drifted = [
            k
            for k, idx in locked_rank_before.items()
            if rank_order_after.index(k) != idx
        ]
        if drifted:
            print(
                f"   ❌ Manual anchor(s) moved row # — rank sync violated {args.manual_label!r}: "
                + ", ".join(drifted)
            )
            return 1
        print(f"   ✅ {len(locked_rank_before)} manual anchor(s) unchanged at same row #")

    # Refresh ranks after moves
    refreshed = refresh_issue_fields(validator, list(rice_by_key.keys()), fields_param)
    for issue in features:
        key = issue.get("key") or ""
        if key in refreshed:
            issue["fields"] = {**(issue.get("fields") or {}), **refreshed[key]}

    update_state_from_features(bucket, features, rice_field, rank_field)
    save_state(state_path, state)
    print(f"\n💾 State saved: {state_path}")
    misaligned = count_rice_rank_misalignment(
        validator.session,
        validator.jira_url,
        ordered,
        rice_by_key,
        rank_field,
        rice_field,
        is_cloud,
        reach_field,
    )
    if misaligned:
        print(
            f"   ⚠️  {misaligned} issue(s) still out of RICE/Reach order among ranked set "
            "— re-run or check manual locks"
        )
    else:
        print("   ✅ LexoRank order matches RICE (+ Reach tie-break) for ranked issues")

    print(
        f"   Tip: add label {args.manual_label!r} to lock rank, or edit rank in Jira "
        "(detected automatically if RICE is unchanged)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

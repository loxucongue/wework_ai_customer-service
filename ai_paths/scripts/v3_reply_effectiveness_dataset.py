"""Offline dataset hygiene and conversation-disjoint splits, never business scoring."""
from __future__ import annotations

import hashlib
import json
import random
import re
from difflib import SequenceMatcher
from typing import Any

BASELINE_SHA = "93a99f5c50608e1513bd09272dcc3ac7600a20d5"


def checksum(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def redact_text(text: str, replacements: dict[str, str]) -> str:
    """Known identifiers plus recognizable secrets; manual PII review is still required."""
    for original in sorted(replacements, key=len, reverse=True):
        if len(original) >= 4:
            text = text.replace(original, replacements[original])
    text = re.sub(r"https?://[^\s<>\"'）]+", lambda m: "https://example.invalid/asset/" + hashlib.sha256(m[0].encode()).hexdigest()[:16], text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱]", text)
    text = re.sub(r"(?i)\b(?:wo|wm|ww)[A-Za-z0-9_-]{12,}\b", "[外部身份]", text)
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [凭证]", text)
    return text


def split_cases(rows: list[dict[str, Any]], *, seed: int = 20260914,
                development_count: int = 50, holdout_count: int = 30) -> dict[str, Any]:
    """Cluster full contact histories before selection; never split turns across sets."""
    rows = sorted(rows, key=lambda r: r['case_id'])
    parent = {r['group_id']: r['group_id'] for r in rows}

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for index, row in enumerate(rows):
        text = re.sub(r'\s+', '', row.get('customer_context', ''))
        if len(text) < 20:
            continue  # Generic acknowledgements are not whole-dialogue duplicates.
        for other in rows[:index]:
            other_text = re.sub(r'\s+', '', other.get('customer_context', ''))
            if len(other_text) >= 20 and SequenceMatcher(None, text, other_text, autojunk=False).ratio() >= .92:
                parent[root(row['group_id'])] = root(other['group_id'])
    clusters: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        clusters.setdefault(root(row['group_id']), []).append(row)
    keys = sorted(clusters)
    random.Random(seed).shuffle(keys)
    holdout, development = [], []
    held_groups: set[str] = set()
    for key in keys:
        if len(holdout) >= holdout_count:
            break
        # Bound repeated observations of one contact in the evaluation denominator.
        holdout.extend(clusters[key][:min(3, holdout_count-len(holdout))])
        held_groups.add(key)
    for key in keys:
        if key not in held_groups:
            development.extend(clusters[key][:min(3, development_count-len(development))])
    if len(development) != development_count or len(holdout) != holdout_count:
        raise ValueError(f'insufficient independent cases: development={len(development)}, holdout={len(holdout)}')
    return {'development': development, 'holdout': holdout,
            'manifest': {'baseline_sha': BASELINE_SHA, 'seed': seed, 'cluster_count': len(clusters),
                         'development_count': len(development), 'holdout_count': len(holdout),
                         'development_checksum': checksum(development), 'holdout_checksum': checksum(holdout),
                         'manual_privacy_review_required': True, 'business_gold': False}}


def stratified_split_cases(
    rows: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    *,
    development_quotas: dict[str, int],
    holdout_quotas: dict[str, int],
    seed: int = 20260914,
) -> dict[str, Any]:
    """Freeze category-balanced, conversation-disjoint real-dialogue samples."""
    annotation_map = {str(item.get("case_id")): item for item in annotations}
    if set(development_quotas) != set(holdout_quotas):
        raise ValueError("quota categories must match")
    enriched = [dict(row, _annotation=annotation_map.get(str(row.get("case_id")))) for row in rows]
    if any(not row["_annotation"] for row in enriched):
        raise ValueError("every case requires an annotation")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in enriched:
        groups.setdefault(str(row["group_id"]), []).append(row)
    group_ids = sorted(groups)
    rng = random.Random(seed)
    winner: set[str] | None = None
    best_score: tuple[int, int] | None = None
    for _ in range(50_000):
        shuffled = list(group_ids)
        rng.shuffle(shuffled)
        holdout_groups = set(shuffled[: round(len(shuffled) * 0.4)])
        capacities = {"development": {key: 0 for key in development_quotas},
                      "holdout": {key: 0 for key in holdout_quotas}}
        for group_id, items in groups.items():
            partition = "holdout" if group_id in holdout_groups else "development"
            for row in items:
                category = str(row["_annotation"].get("category") or "")
                if category in capacities[partition]:
                    capacities[partition][category] += 1
        if any(capacities["development"][key] < count for key, count in development_quotas.items()):
            continue
        if any(capacities["holdout"][key] < count for key, count in holdout_quotas.items()):
            continue
        score = (
            sum(capacities["development"][key] - count for key, count in development_quotas.items())
            + sum(capacities["holdout"][key] - count for key, count in holdout_quotas.items()),
            abs(len(holdout_groups) * 10 - len(group_ids) * 4),
        )
        if best_score is None or score < best_score:
            winner, best_score = holdout_groups, score
    if winner is None:
        raise ValueError("cannot satisfy category quotas without splitting a conversation")

    def choose(partition: str, quotas: dict[str, int]) -> list[dict[str, Any]]:
        result = []
        for category, count in quotas.items():
            pool = [
                row for group_id, items in groups.items()
                if (group_id in winner) == (partition == "holdout")
                for row in items
                if row["_annotation"].get("category") == category
            ]
            pool.sort(key=lambda row: hashlib.sha256(f"{seed}:{partition}:{row['case_id']}".encode()).hexdigest())
            good = [row for row in pool if row["_annotation"].get("observed_quality") == "good"]
            selected = good[:1]
            selected.extend(row for row in pool if row not in selected)
            for row in selected[:count]:
                clean = {key: value for key, value in row.items() if key != "_annotation"}
                clean["review_brief"] = row["_annotation"]
                result.append(clean)
        return sorted(result, key=lambda row: row["case_id"])

    development = choose("development", development_quotas)
    holdout = choose("holdout", holdout_quotas)
    if len(development) != sum(development_quotas.values()) or len(holdout) != sum(holdout_quotas.values()):
        raise ValueError("quota selection incomplete")
    return {"development": development, "holdout": holdout,
            "manifest": {"baseline_sha": BASELINE_SHA, "seed": seed,
                         "development_count": len(development), "holdout_count": len(holdout),
                         "development_quotas": development_quotas, "holdout_quotas": holdout_quotas,
                         "development_checksum": checksum(development), "holdout_checksum": checksum(holdout),
                         "business_gold": False, "manual_privacy_review_required": True}}

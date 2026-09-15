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

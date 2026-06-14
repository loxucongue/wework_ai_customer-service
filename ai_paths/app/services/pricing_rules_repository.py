from __future__ import annotations

import sqlite3
from typing import Any

from app.policies.s10_offer import s10_price_facts
from app.services.storage.sqlite_store import SQLiteStore


PROJECT_ALIASES: dict[str, list[str]] = {
    "S10": [
        "S10",
        "淡斑",
        "祛斑",
        "斑点",
        "色沉",
        "肤色不均",
        "黑色素",
        "色素",
        "美白嫩肤",
        "收缩毛孔",
        "老年斑",
        "遗传斑",
        "毛孔",
        "痘印",
        "细纹",
        "皱纹",
        "项目",
        "活动",
        "优惠",
        "广告",
        "券",
    ],
}

PRICE_INTENT_TERMS = {
    "周年庆活动价": ["周年庆", "活动", "活动价", "优惠", "广告", "券", "第一次", "首单"],
    "老客报价": ["老客", "复购", "以前来过", "做过", "上次", "订单"],
}

ACTIVITY_TERMS = ["周年庆", "活动", "活动价", "优惠", "券", "广告", "抖音", "快手", "秒杀"]
UNSUPPORTED_DIRECT_PRICE_TERMS = ["去痣", "祛痣", "点痣", "痣多少钱", "痦子多少钱", "一颗痣", "单颗痣"]

SCOPE_TERMS = {
    "单部位体验": ["局部", "单部位", "一处", "一块", "一次", "单次"],
}

EXPECTED_S10_RULE_IDS = {
    "S10_ANNIVERSARY_NEW_268",
    "S10_OLD_GT_1000_680",
    "S10_OLD_LE_1000_520",
}


class PricingRulesRepository:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if any(term in text for term in UNSUPPORTED_DIRECT_PRICE_TERMS):
            return []
        rows = self._load_s10_rows()
        if not text:
            return rows[:limit]
        scored = [(self._score(row, text), row) for row in rows]
        matched = [item for item in scored if item[0] > 0]
        matched.sort(key=lambda item: item[0], reverse=True)
        if matched:
            best_score = matched[0][0]
            if best_score >= 20:
                matched = [item for item in matched if item[0] >= best_score - 10]
            return [row for _, row in matched[:limit]]
        return rows[:limit]

    def _load_s10_rows(self) -> list[dict[str, Any]]:
        try:
            with self.store.connect() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT
                            p.rule_id,
                            p.project_code,
                            p.project_name,
                            p.quote_type,
                            p.body_scope,
                            p.customer_segment,
                            p.prepay_amount,
                            p.tail_amount,
                            p.total_price,
                            p.display_price,
                            p.original_price,
                            p.min_quote,
                            p.conditions,
                            p.rule_note,
                            c.category,
                            c.description,
                            c.suitable_for,
                            c.contraindications,
                            c.effects,
                            c.duration
                        FROM project_pricing_rules p
                        LEFT JOIN project_catalog c ON c.project_code = p.project_code
                        WHERE p.enabled = 1
                          AND p.project_code = 'S10'
                          AND p.rule_id IN ('S10_ANNIVERSARY_NEW_268', 'S10_OLD_GT_1000_680', 'S10_OLD_LE_1000_520')
                        ORDER BY p.quote_type, p.total_price
                        """
                    )
                ]
        except sqlite3.OperationalError:
            rows = []
        if {str(row.get("rule_id") or "") for row in rows} >= EXPECTED_S10_RULE_IDS:
            return rows
        return [dict(row) for row in s10_price_facts()]

    def _score(self, row: dict[str, Any], query: str) -> int:
        project_code = str(row.get("project_code") or "")
        if project_code != "S10":
            return 0
        haystack = " ".join(str(row.get(key) or "") for key in row)
        score = 0
        for alias in PROJECT_ALIASES.get(project_code, []):
            if alias and alias in query:
                score += 30 if alias == project_code else 12
        if project_code and project_code in query:
            score += 40
        for scope, terms in SCOPE_TERMS.items():
            if scope in str(row.get("body_scope") or "") and any(term in query for term in terms):
                score += 6
        display_price = str(row.get("display_price") or "")
        total_price = str(row.get("total_price") or "")
        if total_price and total_price != "0" and total_price in query:
            score += 20
        if display_price and any(part and part in query for part in display_price.replace("，", " ").replace("；", " ").split()):
            score += 4
        if any(term in haystack for term in query.split()):
            score += 2
        for quote_type, terms in PRICE_INTENT_TERMS.items():
            if str(row.get("quote_type") or "") == quote_type and any(term in query for term in terms):
                score += 8
        segment = str(row.get("customer_segment") or "")
        if "1000" in segment or "1k" in segment.lower():
            high_order_terms = ("超过1000", "大于1000", "高于1000", "1000以上", "1k以上", "超过1k")
            low_order_terms = ("不超过1000", "低于1000", "小于1000", "1000以下", "1k以下", "低于1k", "没超过1000")
            high_query = any(term in query for term in high_order_terms) and not any(
                term in query for term in ("不超过1000", "没超过1000", "不满1000", "不到1000", "不超过1k")
            )
            if high_query and "超过" in segment and "不超过" not in segment:
                score += 14
            if any(term in query for term in low_order_terms) and "不超过" in segment:
                score += 14
        if score <= 0:
            return 0
        quote_type = str(row.get("quote_type") or "")
        if quote_type == "周年庆活动价" and not any(term in query for term in ACTIVITY_TERMS):
            score += 6
        return score

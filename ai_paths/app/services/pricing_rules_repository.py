from __future__ import annotations

from typing import Any

from app.services.storage.sqlite_store import SQLiteStore


PROJECT_ALIASES: dict[str, list[str]] = {
    "S10": ["S10", "淡斑", "祛斑", "斑点", "色沉", "肤色不均", "黑色素", "色素", "美白嫩肤", "收缩毛孔", "老年斑遗传斑"],
    "S10N": ["S10N", "补水", "干燥", "缺水", "提亮", "暗沉"],
    "K10": ["K10", "毛孔", "痘印", "痘坑", "抗衰", "紧致", "全脸", "局部"],
    "M10": ["M10", "塑形", "轮廓", "线条"],
    "OTHER": ["其他", "别的", "其他品相", "项目"],
}

PRICE_INTENT_TERMS = {
    "首次报价": ["首次", "新客", "第一次", "首单", "首次报价"],
    "大型活动": ["大型活动", "特殊活动", "内部活动", "公司通知"],
    "老客报价": ["老客", "复购", "以前来过", "做过"],
}

SPECIAL_ACTIVITY_TERMS = ["大型活动", "特殊活动", "内部活动", "公司通知"]
NORMAL_MARKETING_ACTIVITY_TERMS = ["周年庆", "周年庆活动", "活动", "活动价", "活动报价", "优惠", "特价", "券", "广告"]

SCOPE_TERMS = {
    "全脸": ["全脸", "整脸"],
    "局部": ["局部", "单部位", "一处", "一块"],
}


class PricingRulesRepository:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        text = str(query or "").strip()
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
                    ORDER BY p.project_code, p.quote_type, p.total_price
                    """
                )
            ]
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
        fallback = [row for row in rows if row.get("project_code") == "OTHER"]
        fallback.sort(key=lambda row: self._quote_priority(row, text))
        return fallback[:limit]

    def _score(self, row: dict[str, Any], query: str) -> int:
        project_code = str(row.get("project_code") or "")
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
        if display_price and any(part and part in query for part in display_price.replace("，", " ").split()):
            score += 4
        if any(term in haystack for term in query.split()):
            score += 2
        if score <= 0:
            return 0
        for quote_type, terms in PRICE_INTENT_TERMS.items():
            if str(row.get("quote_type") or "") == quote_type and any(term in query for term in terms):
                score += 8
        quote_type = str(row.get("quote_type") or "")
        if quote_type == "首次报价" and (
            not any(term in query for term in SPECIAL_ACTIVITY_TERMS)
            or any(term in query for term in NORMAL_MARKETING_ACTIVITY_TERMS)
        ):
            score += 8
        if quote_type.startswith("大型活动") and not any(term in query for term in SPECIAL_ACTIVITY_TERMS):
            score -= 12
        return score

    @staticmethod
    def _quote_priority(row: dict[str, Any], query: str) -> tuple[int, int]:
        quote_type = str(row.get("quote_type") or "")
        special_requested = any(term in query for term in SPECIAL_ACTIVITY_TERMS)
        if quote_type.startswith("大型活动"):
            return (0 if special_requested else 4, int(row.get("total_price") or 0))
        if quote_type == "首次报价":
            return (1 if special_requested else 0, int(row.get("total_price") or 0))
        if "老客" in quote_type:
            return (3, int(row.get("total_price") or 0))
        return (2, int(row.get("total_price") or 0))

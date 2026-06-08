from __future__ import annotations

from pathlib import Path
from typing import Any
import ast
import re
import uuid

import openpyxl

from app.config import Settings
from app.services.storage.sqlite_store import SQLiteStore


class LocalPricingRepository:
    def __init__(self, settings: Settings, store: SQLiteStore | None = None):
        self.path: Path = settings.pricing_xlsx_path
        self.store = store
        self._rows: list[dict[str, Any]] | None = None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        structured_rows = self._search_structured_rules(query, limit=limit)
        if structured_rows:
            return structured_rows
        rows = self._load_rows()
        terms = self._query_terms(query)
        if not terms:
            return rows[:limit]

        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            name = str(row.get("project_name") or "")
            note = str(row.get("price_note") or "")
            haystack = f"{name} {note}"
            score = 0
            for term in terms:
                if term and term in haystack:
                    score += 10 if term in name else 4
            if score:
                scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def list_legacy_rows(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.store:
            return []
        sql = """
            SELECT
                r.rule_id,
                p.project_code,
                p.project_name_internal,
                p.project_name_display,
                r.service_scope,
                r.customer_type,
                r.price_scene,
                r.trigger_condition,
                r.deposit_amount,
                r.tail_amount,
                r.total_amount,
                r.list_price,
                r.price_label,
                r.explain_short,
                r.explain_long,
                r.status,
                r.updated_at
            FROM pricing_rules r
            JOIN project_catalog p ON p.project_code = r.project_code
            ORDER BY r.updated_at DESC, r.priority ASC
            LIMIT ?
        """
        with self.store.connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, [limit]).fetchall()]
        return [self._rule_to_items_pricing_row(row) for row in rows]

    def execute_legacy_sql(self, sql: str) -> list[dict[str, Any]]:
        statement = str(sql or "").strip()
        upper = statement.upper()
        if upper.startswith("SELECT"):
            return self.list_legacy_rows()
        if upper.startswith("DELETE"):
            rule_id = self._where_id(statement)
            if rule_id:
                self.delete_rule(rule_id)
            return []
        if upper.startswith("UPDATE"):
            rule_id = self._where_id(statement)
            values = self._parse_update_values(statement)
            if rule_id:
                self.upsert_legacy_row(rule_id, values)
            return []
        if upper.startswith("INSERT"):
            values = self._parse_insert_values(statement)
            self.upsert_legacy_row("", values)
            return []
        raise ValueError("unsupported pricing SQL")

    def upsert_legacy_row(self, rule_id: str, values: dict[str, Any]) -> str:
        if not self.store:
            raise RuntimeError("pricing store is not configured")
        project_name = str(values.get("project_name") or values.get("price_note") or "未命名项目").strip()
        project_code = self._project_code_from_name(project_name)
        now_rule_id = rule_id or f"CFG_{uuid.uuid4().hex[:12]}"
        total = self._first_amount(values.get("promo_price"), values.get("new_price"), values.get("daily_price"), values.get("old_price"))
        customer_type = "老客" if self._as_amount(values.get("old_price")) else "新客"
        price_scene = "大型活动" if self._as_amount(values.get("promo_price")) else "首次报价"
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO project_catalog (
                    project_code, project_name_internal, project_name_display, project_group,
                    service_scope_default, category, description, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                [
                    project_code,
                    project_name,
                    project_name,
                    project_code,
                    "单部位",
                    "未分类",
                    str(values.get("price_note") or ""),
                ],
            )
            conn.execute(
                """
                INSERT INTO pricing_rules (
                    rule_id, project_code, service_scope, customer_type, price_scene, trigger_condition,
                    total_amount, list_price, includes_desc, price_label, explain_short, explain_long,
                    priority, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 50, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(rule_id) DO UPDATE SET
                    project_code=excluded.project_code,
                    service_scope=excluded.service_scope,
                    customer_type=excluded.customer_type,
                    price_scene=excluded.price_scene,
                    trigger_condition=excluded.trigger_condition,
                    total_amount=excluded.total_amount,
                    list_price=excluded.list_price,
                    includes_desc=excluded.includes_desc,
                    price_label=excluded.price_label,
                    explain_short=excluded.explain_short,
                    explain_long=excluded.explain_long,
                    status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                """,
                [
                    now_rule_id,
                    project_code,
                    "单部位",
                    customer_type,
                    price_scene,
                    str(values.get("promo_target") or values.get("gift_scene") or ""),
                    total,
                    self._as_amount(values.get("daily_price")),
                    str(values.get("gift_item") or "具体配置到店确认"),
                    "配置页价格",
                    str(values.get("price_note") or f"{project_name}价格按当前配置记录。"),
                    str(values.get("price_note") or f"{project_name}价格按当前配置记录，具体以门店确认为准。"),
                    1 if str(values.get("status", "true")).lower() in {"true", "1", "yes"} else 0,
                ],
            )
        return now_rule_id

    def delete_rule(self, rule_id: str) -> None:
        if not self.store:
            return
        with self.store.connect() as conn:
            conn.execute("DELETE FROM pricing_rules WHERE rule_id=?", [rule_id])

    def export_knowledge_text(self) -> str:
        rows = self.list_legacy_rows(limit=1000)
        chunks: list[str] = []
        for row in rows:
            chunks.append(
                "\n".join(
                    [
                        "###",
                        f"项目id：{row.get('id', '')}",
                        f"项目名称：{row.get('project_name', '')}",
                        f"日常单次价：{row.get('daily_price', '')}",
                        f"新客体验价：{row.get('new_price', '')}",
                        f"老客单次价：{row.get('old_price', '')}",
                        f"活动价：{row.get('promo_price', '')}",
                        f"活动适用人群：{row.get('promo_target', '')}",
                        f"状态：{row.get('status', '')}",
                        f"报价备注：{row.get('price_note', '')}",
                    ]
                )
            )
        return "\n\n".join(chunks)

    def _load_rows(self) -> list[dict[str, Any]]:
        if self._rows is not None:
            return self._rows
        if not self.path.exists():
            self._rows = []
            return self._rows

        workbook = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        sheet = workbook.active
        raw_rows = list(sheet.iter_rows(values_only=True))
        if not raw_rows:
            self._rows = []
            return self._rows
        headers = [str(cell or "") for cell in raw_rows[0]]
        rows: list[dict[str, Any]] = []
        for raw in raw_rows[1:]:
            row = {headers[index]: raw[index] if index < len(raw) else "" for index in range(len(headers))}
            if str(row.get("status", "")).lower() not in {"true", "1", "yes"}:
                continue
            rows.append({key: "" if value is None else str(value) for key, value in row.items()})
        self._rows = rows
        return rows

    def _query_terms(self, query: str) -> list[str]:
        query = query or ""
        terms: list[str] = []
        aliases = {
            "K10F": ["K10F", "K10-全脸", "K10全脸", "全脸", "抗衰", "1400", "2000"],
            "K10-全脸": ["K10F", "K10-全脸", "K10全脸", "全脸", "抗衰", "1400", "2000"],
            "K10全脸": ["K10F", "K10-全脸", "K10全脸", "全脸", "抗衰", "1400", "2000"],
            "S10N": ["S10N", "补水", "护理", "美白", "180", "380"],
            "S10": ["S10", "祛斑", "淡斑", "黑色素", "色素", "色沉", "肤色不均", "280", "880"],
            "K10": ["K10", "K10-局部", "K10局部", "局部", "抗衰", "紧致", "松弛", "皱纹", "细纹", "350", "680"],
            "M10": ["M10", "塑形", "轮廓", "400", "1200"],
            "抗衰": ["抗衰", "紧致", "松弛", "皱纹", "细纹", "K10"],
            "紧致": ["紧致", "抗衰", "松弛", "K10"],
            "松弛": ["松弛", "抗衰", "紧致", "K10"],
            "补水": ["补水", "缺水", "干燥", "S10N"],
            "缺水": ["缺水", "补水", "干燥", "S10N"],
            "塑形": ["塑形", "轮廓", "M10"],
            "轮廓": ["轮廓", "塑形", "M10"],
            "皮秒": ["皮秒", "超皮秒", "祛斑", "淡斑"],
            "超皮秒": ["超皮秒", "皮秒", "祛斑", "淡斑"],
            "祛斑": ["祛斑", "淡斑", "黑色素", "色素", "色沉", "S10"],
            "淡斑": ["淡斑", "祛斑", "黑色素", "色素", "色沉", "S10"],
            "黑色素": ["黑色素", "祛斑", "淡斑", "色素", "色沉", "S10"],
            "色素": ["色素", "黑色素", "祛斑", "淡斑", "色沉", "S10"],
            "色沉": ["色沉", "色素", "黑色素", "祛斑", "淡斑", "S10"],
            "水光": ["水光", "补水"],
            "热玛吉": ["热玛吉", "紧致", "抗老"],
        }
        for key, values in aliases.items():
            if key in query:
                terms.extend(values)
        if not terms:
            for key in aliases:
                if key in query:
                    terms.append(key)
        return self._dedupe(terms)

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result

    def _search_structured_rules(self, query: str, limit: int) -> list[dict[str, Any]]:
        if not self.store:
            return []
        terms = self._query_terms(query)
        query_text = str(query or "")
        if not terms:
            terms = [query_text] if query_text else []
        where_parts = ["r.status=1", "p.status=1"]
        params: list[Any] = []
        searchable = (
            "p.project_code || ' ' || p.project_name_internal || ' ' || p.project_name_display || ' ' || "
            "p.project_group || ' ' || p.category || ' ' || p.description || ' ' || "
            "r.service_scope || ' ' || r.customer_type || ' ' || r.price_scene || ' ' || "
            "r.channel_type || ' ' || r.trigger_condition || ' ' || r.price_label || ' ' || "
            "r.explain_short || ' ' || r.explain_long"
        )
        if terms:
            where_parts.append("(" + " OR ".join([f"{searchable} LIKE ?" for _ in terms]) + ")")
            params.extend([f"%{term}%" for term in terms])
        project_filter = self._project_filter(query_text)
        if project_filter:
            where_parts.append(project_filter[0])
            params.extend(project_filter[1])
        if "全脸" in query_text:
            where_parts.append("r.service_scope='全脸'")
        elif "局部" in query_text:
            where_parts.append("r.service_scope='局部'")
        if "老客" in query_text or "复购" in query_text:
            where_parts.append("r.customer_type='老客'")
        sql = f"""
            SELECT
                p.project_code,
                p.project_name_internal,
                p.project_name_display,
                p.project_group,
                p.category,
                p.service_scope_default,
                p.single_session_duration_min,
                p.course_cycle_desc,
                r.rule_id,
                r.service_scope,
                r.customer_type,
                r.price_scene,
                r.channel_type,
                r.trigger_condition,
                r.deposit_amount,
                r.tail_amount,
                r.total_amount,
                r.list_price,
                r.is_single_session_price,
                r.is_package_price,
                r.includes_desc,
                r.excludes_desc,
                r.price_label,
                r.explain_short,
                r.explain_long,
                r.priority
            FROM pricing_rules r
            JOIN project_catalog p ON p.project_code = r.project_code
            WHERE {" AND ".join(where_parts)}
            ORDER BY r.priority ASC, r.total_amount ASC
            LIMIT ?
        """
        params.append(limit)
        try:
            with self.store.connect() as conn:
                rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        except Exception:
            return []
        return [self._pricing_rule_to_legacy_row(row) for row in rows]

    def _rule_to_items_pricing_row(self, row: dict[str, Any]) -> dict[str, Any]:
        total = self._money(row.get("total_amount"))
        is_old = row.get("customer_type") == "老客"
        is_campaign = row.get("price_scene") in {"大型活动", "活动"}
        note_parts = [
            f"项目编码：{row.get('project_code')}",
            f"展示名：{row.get('project_name_display')}",
            f"范围：{row.get('service_scope')}",
            f"客户类型：{row.get('customer_type')}",
            f"价格场景：{row.get('price_scene')}",
            f"触发条件：{row.get('trigger_condition')}",
            f"价格标签：{row.get('price_label')}",
            f"解释口径：{row.get('explain_short') or row.get('explain_long')}",
        ]
        return {
            "id": row.get("rule_id") or "",
            "project_name": row.get("project_name_internal") or row.get("project_name_display") or "",
            "daily_price": self._money(row.get("list_price")),
            "new_price": "" if is_old or is_campaign else total,
            "old_price": total if is_old else "",
            "old_card": "",
            "promo_price": total if is_campaign else "",
            "promo_target": row.get("trigger_condition") or "",
            "promo_start": "",
            "promo_end": "",
            "gift_item": "",
            "gift_scene": row.get("price_scene") or "",
            "status": "true" if row.get("status") else "false",
            "price_note": "；".join(part for part in note_parts if part),
            "updated_at": row.get("updated_at") or "",
        }

    def _project_filter(self, query: str) -> tuple[str, list[Any]] | None:
        normalized = query.upper()
        if "K10F" in normalized or "K10-全脸" in query or "K10全脸" in query or ("K10" in normalized and "全脸" in query):
            return "p.project_code='K10F'", []
        if "S10N" in normalized:
            return "p.project_code='S10N'", []
        if "S10" in normalized:
            return "p.project_code='S10'", []
        if any(term in query for term in ["祛斑", "淡斑", "黑色素", "色素", "色沉", "肤色不均"]):
            return "p.project_code='S10'", []
        if "K10" in normalized:
            return "p.project_code='K10'", []
        if any(term in query for term in ["抗衰", "紧致", "松弛", "皱纹", "细纹"]):
            return "p.project_code='K10'", []
        if any(term in query for term in ["补水", "缺水", "干燥"]):
            return "p.project_code='S10N'", []
        if any(term in query for term in ["塑形", "轮廓", "下颌线"]):
            return "p.project_code='M10'", []
        if "M10" in normalized:
            return "p.project_code='M10'", []
        return None

    def _where_id(self, statement: str) -> str:
        match = re.search(r"\bWHERE\s+id\s*=\s*('?)([^'\s;]+)\1", statement, flags=re.IGNORECASE)
        return match.group(2) if match else ""

    def _parse_insert_values(self, statement: str) -> dict[str, Any]:
        match = re.search(r"\((.*?)\)\s*VALUES\s*\((.*)\)", statement, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return {}
        columns = [item.strip(" `") for item in match.group(1).split(",")]
        values = self._split_sql_values(match.group(2))
        return dict(zip(columns, values))

    def _parse_update_values(self, statement: str) -> dict[str, Any]:
        match = re.search(r"\bSET\s+(.*?)\s+WHERE\s+", statement, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return {}
        result: dict[str, Any] = {}
        for part in self._split_assignments(match.group(1)):
            key, _, raw_value = part.partition("=")
            if key.strip():
                result[key.strip(" `")] = self._sql_literal(raw_value.strip())
        return result

    def _split_assignments(self, text: str) -> list[str]:
        return self._split_raw(text, ",")

    def _split_sql_values(self, text: str) -> list[Any]:
        return [self._sql_literal(item.strip()) for item in self._split_raw(text, ",")]

    def _split_raw(self, text: str, sep: str) -> list[str]:
        parts: list[str] = []
        current: list[str] = []
        in_quote = False
        index = 0
        while index < len(text):
            char = text[index]
            if char == "'":
                current.append(char)
                if index + 1 < len(text) and text[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                in_quote = not in_quote
            elif char == sep and not in_quote:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
            index += 1
        if current:
            parts.append("".join(current).strip())
        return parts

    def _sql_literal(self, raw: str) -> Any:
        text = raw.strip()
        if text.upper() == "NULL":
            return ""
        if text.lower() in {"true", "false"}:
            return text.lower()
        if text.startswith("'") and text.endswith("'"):
            try:
                return ast.literal_eval(text.replace("''", "\\'"))
            except Exception:
                return text[1:-1].replace("''", "'")
        return text

    def _project_code_from_name(self, name: str) -> str:
        normalized = name.upper()
        if "S10N" in normalized:
            return "S10N"
        if "S10" in normalized:
            return "S10"
        if "K10F" in normalized or "K10-全脸" in name or "K10全脸" in name or ("K10" in normalized and "全脸" in name):
            return "K10F"
        if "K10" in normalized and "全脸" in name:
            return "K10F"
        if "K10" in normalized:
            return "K10"
        if "M10" in normalized:
            return "M10"
        return f"CFG_{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex[:12].upper()}"

    def _first_amount(self, *values: Any) -> float:
        for item in values:
            amount = self._as_amount(item)
            if amount > 0:
                return amount
        return 0

    @staticmethod
    def _as_amount(raw: Any) -> float:
        try:
            return float(str(raw or "").replace("元", "").strip() or 0)
        except ValueError:
            return 0

    def _pricing_rule_to_legacy_row(self, row: dict[str, Any]) -> dict[str, Any]:
        total = self._money(row.get("total_amount"))
        deposit = self._money(row.get("deposit_amount"))
        tail = self._money(row.get("tail_amount"))
        name = row.get("project_name_internal") or row.get("project_name_display") or row.get("project_code")
        note_parts = [
            f"项目编码：{row.get('project_code')}",
            f"展示名：{row.get('project_name_display')}",
            f"范围：{row.get('service_scope') or row.get('service_scope_default')}",
            f"客户类型：{row.get('customer_type')}",
            f"价格场景：{row.get('price_scene')}",
            f"触发条件：{row.get('trigger_condition')}",
            f"价格标签：{row.get('price_label')}",
            f"定金：{deposit}" if deposit else "",
            f"尾款：{tail}" if tail else "",
            f"总价：{total}" if total else "",
            f"包含项：{row.get('includes_desc')}" if row.get("includes_desc") else "",
            f"不包含项：{row.get('excludes_desc')}" if row.get("excludes_desc") else "",
            f"解释口径：{row.get('explain_short') or row.get('explain_long')}",
            f"单次时长：{row.get('single_session_duration_min')}分钟" if row.get("single_session_duration_min") else "",
            f"疗程节奏：{row.get('course_cycle_desc')}" if row.get("course_cycle_desc") else "",
        ]
        price_key = "promo_price" if row.get("price_scene") in {"大型活动", "活动"} else "new_price"
        return {
            "project_name": name,
            "daily_price": "",
            "new_price": total if price_key == "new_price" else "",
            "old_price": total if row.get("customer_type") == "老客" else "",
            "old_card": "",
            "promo_price": total if price_key == "promo_price" else "",
            "promo_target": row.get("trigger_condition") or "",
            "gift_item": "",
            "gift_scene": row.get("price_scene") or "",
            "status": "true",
            "price_note": "；".join(part for part in note_parts if part),
            "rule_id": row.get("rule_id") or "",
            "project_code": row.get("project_code") or "",
            "service_scope": row.get("service_scope") or "",
            "customer_type": row.get("customer_type") or "",
            "price_scene": row.get("price_scene") or "",
            "deposit_amount": deposit,
            "tail_amount": tail,
            "total_amount": total,
            "_source": "local_pricing_rules",
        }

    @staticmethod
    def _money(raw: Any) -> str:
        try:
            value = float(raw or 0)
        except (TypeError, ValueError):
            return ""
        if value <= 0:
            return ""
        if value.is_integer():
            return str(int(value))
        return str(value)

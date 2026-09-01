from __future__ import annotations

import asyncio
import json

from app.services.sales_recall_service import (
    SalesRecallService,
    parse_sales_recall_response,
)


def test_sales_recall_parser_sanitizes_non_authoritative_facts() -> None:
    raw = {
        "code": 0,
        "data": json.dumps(
            {
                "output": [
                    {
                        "documentId": "doc-1",
                        "output": json.dumps(
                            {
                                "内容编号": "YYHF-0029",
                                "卡点类型": "距离远",
                                "适用场景": "客户觉得门店远，正在犹豫要不要跑一趟。",
                                "话术内容": "三四个小时都有人来，原价1980，现在登记还赠送小气泡。",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
            ensure_ascii=False,
        ),
    }

    candidates = parse_sales_recall_response(raw, max_candidates=3)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["source_id"] == "YYHF-0029"
    assert candidate["objection_type"] == "距离远"
    assert candidate["authority"] == "reference_only_not_business_fact"
    assert "价格事实已移除" in candidate["style_reference"]
    assert "距离/耗时事实已移除" in candidate["style_reference"]
    assert "1980" not in candidate["style_reference"]
    assert "三四个小时" not in candidate["style_reference"]
    risk_codes = {item["code"] for item in candidate["risk_flags"]}
    assert {"old_or_external_price", "distance_or_time_fact", "gift_material"} <= set(
        risk_codes
    )
    assert "gift_or_bonus_may_be_used_cautiously" in candidate["allowed_materials"]


def test_sales_recall_skips_opening_greeting_without_calling_coze() -> None:
    class _Settings:
        sales_recall_enabled = True
        sales_recall_workflow_id = "7672999254608347179"
        sales_recall_max_candidates = 3

    class _FailingCoze:
        settings = _Settings()

        async def run_workflow(self, *_args, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("coze should not be called for pure opening")

    service = SalesRecallService(_FailingCoze())
    result = asyncio.run(
        service.recall(
            {
                "current_message": {"content": "我已经添加了你，现在我们可以开始聊天了。"},
                "conversation": [
                    {
                        "role": "customer",
                        "content": "我已经添加了你，现在我们可以开始聊天了。",
                        "message_type": "text",
                    }
                ],
            }
        )
    )

    assert result["status"] == "skipped_opening"
    assert result["reason"] == "auto_opening_or_empty_customer_message"


def test_sales_recall_marks_coze_error_code_without_blocking_reply() -> None:
    class _Settings:
        sales_recall_enabled = True
        sales_recall_workflow_id = "7672999254608347179"
        sales_recall_max_candidates = 3

    class _ErrorCoze:
        settings = _Settings()

        async def run_workflow(self, *_args, **_kwargs):
            return {"code": 4000, "msg": "missing required parameters"}

    result = asyncio.run(
        SalesRecallService(_ErrorCoze()).recall(
            {
                "current_message": {"content": "太远了，我先考虑一下"},
                "conversation": [{"role": "customer", "content": "太远了，我先考虑一下"}],
            }
        )
    )

    assert result["status"] == "error"
    assert result["candidate_count"] == 0
    assert "coze_workflow_code_4000" in result["reason"]

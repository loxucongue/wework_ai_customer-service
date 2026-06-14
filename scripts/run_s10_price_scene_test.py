# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str((ROOT / "ai_paths").resolve()))

from app.main import app  # noqa: E402


LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BASE_PAYLOAD = {
    "corp_id": "ww943af61cd5d2afe4",
    "user_id": 7294,
    "wechat": "CS001",
    "request_context": {"category_id": "居家产品"},
}

COMMON_FORBIDDEN = [
    "S10",
    "焕新体验季",
    "新客专属活动",
    "老带新专属活动",
    "大型活动",
    "内部活动",
    "公司通知价",
    "内部价",
    "包接送",
    "车费报销",
    "全额退还",
    "原路退还",
]


@dataclass
class PriceCase:
    case_id: str
    scene_type: str
    question: str
    purpose: str
    expected_any: list[str]
    forbidden: list[str]
    required_policy: str = ""


CASES: list[PriceCase] = [
    PriceCase("activity_first", "活动首问", "你们现在有什么优惠活动", "活动首问必须回到周年庆活动和268规则。", ["周年庆活动", "268", "10元", "258"], COMMON_FORBIDDEN, "SF7_ACTIVITY_FIRST_ASK"),
    PriceCase("confirm_268", "价格确认", "确定268吗", "具体活动价要直接确认，不绕。", ["268", "10元", "258", "退还10"], COMMON_FORBIDDEN, "SF7_PRICE_CONFIRM_268"),
    PriceCase("single_fee", "是否一次费用", "是一次的费用吗", "说明一次活动规则和定金尾款关系。", ["一次", "268", "10元", "258"], COMMON_FORBIDDEN, "SF7_PRICE_ONCE_FEE"),
    PriceCase("deposit_why", "预约金解释", "为什么要交10元定金", "解释预约金是锁活动价和名额。", ["10元", "名额", "抵扣", "258"], COMMON_FORBIDDEN, "SF7_DEPOSIT_EXPLAIN"),
    PriceCase("pay_after", "付款时机", "是做完付款吗", "说明到店检测、认可再做付尾款。", ["10元", "抵扣", "258", "认可"], COMMON_FORBIDDEN, "SF7_PAYMENT_TIMING"),
    PriceCase("hidden_fee", "隐形消费顾虑", "到店会不会乱收费", "先给费用透明结论。", ["提前", "说清楚", "认可再做"], COMMON_FORBIDDEN, "SF7_HIDDEN_FEE_WORRY"),
    PriceCase("old_customer_generic", "老客价格", "我是老客，这个多少钱", "没有真实订单金额时，只说老客价规则。", ["超过1000", "680", "520"], COMMON_FORBIDDEN, "SF7_OLD_CUSTOMER_PRICE"),
    PriceCase("old_customer_gt_1000", "老客大额", "我上次订单超过1000，这次多少钱", "老客大额规则。", ["680", "超过1000"], COMMON_FORBIDDEN, "SF7_OLD_CUSTOMER_PRICE"),
    PriceCase("old_customer_le_1000", "老客小额", "我上次订单没超过1000，这次多少钱", "老客小额规则。", ["520", "不超过1000"], COMMON_FORBIDDEN, "SF7_OLD_CUSTOMER_PRICE"),
    PriceCase("lowest_price", "最低价", "还能不能再便宜一点，给我最低价", "守住当前活动价，不乱降价。", ["最优惠", "268"], COMMON_FORBIDDEN, "SF7_LOWEST_PRICE_HANDOFF"),
    PriceCase("ad_58", "广告低价冲突", "看广告58元是真的吗", "广告价冲突回到当前周年庆活动。", ["周年庆活动", "268"], COMMON_FORBIDDEN, "SF7_PRICE_AD_58"),
    PriceCase("transport", "接送/车费", "有车费报销吗 可以包接送吗", "必须先直接答没有接送和车费报销。", ["没有接送", "交通费用需自理"], [item for item in COMMON_FORBIDDEN if item not in {"车费报销", "包接送"}], "SF7_TRANSPORT_SUPPORT"),
    PriceCase("activity_end", "名额/结束", "这个活动什么时候结束，还有名额吗", "说明30名和名额满恢复原价。", ["30名", "名额满", "1980"], COMMON_FORBIDDEN, "SF7_CAMPAIGN_QUOTA_OR_END_TIME"),
    PriceCase("method", "项目方法", "你们祛斑用什么方法", "项目方法短句承接，不说明书化。", ["肌源调肤", "到店", "更准"], COMMON_FORBIDDEN, "SF3_PROJECT_DETAIL_EXPLAIN"),
]


def extract_reply_texts(data: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in data.get("reply_messages") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if item.get("type") == "text" and isinstance(content, dict):
            text = str(content.get("text") or "").strip()
            if text:
                texts.append(text)
        elif item.get("type") == "human_handoff" and isinstance(content, dict):
            reason = str(content.get("handoff_reason") or "").strip()
            if reason:
                texts.append(f"[human_handoff] {reason}")
    return texts


def run_case(client: TestClient, case: PriceCase) -> dict[str, Any]:
    cid = f"price_{case.case_id}_{uuid.uuid4().hex[:8]}"
    payload = {
        **BASE_PAYLOAD,
        "content": case.question,
        "customer_id": cid,
        "external_userid": cid,
        "conversation_history": [],
    }
    started = time.perf_counter()
    response = client.post("/reply", json=payload)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    data = response.json()
    texts = extract_reply_texts(data)
    all_text = "\n".join(texts)
    meta = data.get("meta") or {}
    exact_policy = str(meta.get("exact_policy_id") or "")
    active_scene = str(meta.get("active_scene_id") or "")
    tool_calls = [str((item or {}).get("name") or "") for item in (meta.get("tool_calls") or []) if isinstance(item, dict)]
    forbidden_hits = [item for item in case.forbidden if item and item in all_text]
    expected_hits = [item for item in case.expected_any if item and item in all_text]
    enough_expected = len(expected_hits) >= max(1, min(2, len(case.expected_any)))
    policy_ok = not case.required_policy or exact_policy == case.required_policy or active_scene == case.required_policy
    passed = response.status_code == 200 and bool(texts) and not forbidden_hits and enough_expected and policy_ok
    issues: list[str] = []
    if response.status_code != 200:
        issues.append(f"HTTP {response.status_code}")
    if not texts:
        issues.append("无客户可见回复")
    if forbidden_hits:
        issues.append("命中禁用词：" + "、".join(forbidden_hits))
    if not enough_expected:
        issues.append("关键业务点不足")
    if not policy_ok:
        issues.append(f"policy偏移：{exact_policy or '<empty>'}")
    return {
        "case_id": case.case_id,
        "scene_type": case.scene_type,
        "question": case.question,
        "purpose": case.purpose,
        "reply_1": texts[0] if len(texts) > 0 else "",
        "reply_2": texts[1] if len(texts) > 1 else "",
        "status_code": response.status_code,
        "elapsed_ms": elapsed_ms,
        "request_id": data.get("request_id", ""),
        "policy_family_id": str(meta.get("policy_family_id") or ""),
        "exact_policy_id": exact_policy,
        "active_scene_id": active_scene,
        "reply_source": str(meta.get("reply_source") or ""),
        "tool_calls": tool_calls,
        "expected_hits": expected_hits,
        "forbidden_hits": forbidden_hits,
        "passed": passed,
        "issues": issues,
    }


def build_markdown(results: list[dict[str, Any]], generated_at: str) -> str:
    passed = sum(1 for item in results if item["passed"])
    avg_elapsed = int(sum(item["elapsed_ms"] for item in results) / max(1, len(results)))
    lines = [
        "# S10 活动价格场景测试报告",
        "",
        f"- 生成时间：`{generated_at}`",
        f"- 总场景数：`{len(results)}`",
        f"- 通过数：`{passed}`",
        f"- 通过率：`{round(passed * 100 / max(1, len(results)), 1)}%`",
        f"- 平均耗时：`{avg_elapsed}ms`",
        "",
        "| 场景 | 用户问题 | AI实际回复（第1条） | AI引导回复（第2条） | exact_policy_id | active_scene_id | 工具调用 | 日志id | 评判 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        judgement = "通过" if item["passed"] else "；".join(item["issues"]) or "未通过"
        tools = "、".join(item["tool_calls"]) if item["tool_calls"] else "-"
        lines.append(
            "| {scene} | {q} | {r1} | {r2} | {policy} | {scene_id} | {tools} | {rid} | {judge} |".format(
                scene=item["scene_type"].replace("|", "/"),
                q=item["question"].replace("|", "/"),
                r1=(item["reply_1"] or "").replace("|", "/"),
                r2=(item["reply_2"] or "").replace("|", "/"),
                policy=(item["exact_policy_id"] or "-").replace("|", "/"),
                scene_id=(item["active_scene_id"] or "-").replace("|", "/"),
                tools=tools.replace("|", "/"),
                rid=(item["request_id"] or "-").replace("|", "/"),
                judge=judgement.replace("|", "/"),
            )
        )
    return "\n".join(lines)


def main() -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with TestClient(app) as client:
        results = [run_case(client, case) for case in CASES]
    stem = datetime.now().strftime("s10_price_scene_report_%Y%m%d_%H%M%S")
    json_path = LOG_DIR / f"{stem}.json"
    md_path = LOG_DIR / f"{stem}.md"
    json_path.write_text(json.dumps({"generated_at": generated_at, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_markdown(results, generated_at), encoding="utf-8")
    print(md_path)


if __name__ == "__main__":
    main()

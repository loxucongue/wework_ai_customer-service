# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
import uuid
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

API_URL = "http://47.252.81.104/api/ai/chat"
ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "logs/parallel_sales_talk_matrix_report.json"
MD_REPORT_PATH = ROOT / "logs/parallel_sales_talk_matrix_report.md"

BASE_PAYLOAD = {
    "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
    "user_id": "DY1032",
    "wechat": "DY1032",
}

SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario_id": "opening_store_match",
        "purpose": "验证泛开场是否优先收集城市位置并推进最近门店匹配，而不是直接追问项目名。",
        "conversation_history": [],
        "variants": [
            "你好，先了解一下",
            "在吗？我想咨询一下",
            "我是看到广告加你的，先了解下",
        ],
        "expected": ["城市", "门店", "附近", "哪区"],
        "avoid": ["项目名", "发照片"],
    },
    {
        "scenario_id": "need_direction_spot",
        "purpose": "验证客户明确说祛斑/色沉时，系统是否先给方向判断，而不是先反问项目名。",
        "conversation_history": [
            "用户: 我是看到祛斑广告加你的",
            "小贝: 可以的，你先说下你主要想改善什么。",
        ],
        "variants": [
            "我主要想祛斑，脸上还有点色沉",
            "点状斑比较多，顺带肤色有点不均",
            "我不懂项目名，你按我的情况直接说方向就行",
        ],
        "expected": ["方向", "淡斑", "色素", "肤色"],
        "avoid": ["你想做什么项目", "先发清晰照片再说"],
    },
    {
        "scenario_id": "case_request",
        "purpose": "验证客户要看效果对比时，系统是否承接案例诉求，而不是转回价格或泛问需求。",
        "conversation_history": [
            "用户: 我主要想祛斑，脸上还有点色沉",
            "小贝: 这类一般可以先看淡斑和肤色改善方向。",
        ],
        "variants": [
            "有没有客户做完之后的效果对比案例",
            "你先发点同类效果图给我看看",
            "图片上的客户一般是做了几次才有这个效果",
        ],
        "expected": ["案例", "参考", "效果", "同类"],
        "avoid": ["多少钱", "你想做哪个项目"],
    },
    {
        "scenario_id": "price_conflict",
        "purpose": "验证199/268/380这类价格冲突问题时，系统能否解释口径差异，而不是空泛核对。",
        "conversation_history": [
            "用户: 我是广告进来的，想了解祛斑活动",
        ],
        "variants": [
            "我看到是268的，你怎么又跟我说380的价格",
            "为什么一样的地方还有380的价格",
            "确定268吗，不会到店又变吧",
        ],
        "expected": ["口径", "活动", "局部", "全脸", "包含"],
        "avoid": ["先到店再说", "价格如下"],
    },
    {
        "scenario_id": "single_fee_and_deposit",
        "purpose": "验证客户追问是不是一次费用、是否必须交10元时，系统能否正面解释定金/尾款/总价关系。",
        "conversation_history": [
            "用户: 我看到广告上写199",
        ],
        "variants": [
            "这是一次的费用吗",
            "199是一只还是一双，还是先付10元",
            "我不交定金，到店再付全款行不行",
        ],
        "expected": ["一次", "定金", "尾款", "总价"],
        "avoid": ["不知道", "先付款再说"],
    },
    {
        "scenario_id": "times_and_effect",
        "purpose": "验证客户问一次能不能做好、一般做几次时，系统是否先给大致节奏而不是一直追问。",
        "conversation_history": [
            "用户: 我主要是点状斑，预算别太高",
            "小贝: 看描述这类通常可以先看针对性色素改善方向。",
        ],
        "variants": [
            "一次能做好吗",
            "一般要做多少次",
            "做完效果能保持吗",
        ],
        "expected": ["一次", "几次", "看变化", "因人"],
        "avoid": ["必须三次", "包效果"],
    },
    {
        "scenario_id": "trust_hidden_fee",
        "purpose": "验证普通信任顾虑是否被正常承接，而不是过度转人工。",
        "conversation_history": [
            "用户: 我看到广告上说199元祛斑有效果",
        ],
        "variants": [
            "到店会乱收费吗",
            "会不会还有其他收费",
            "你们有医疗资质吗",
        ],
        "expected": ["核对", "清楚", "资质", "正规"],
        "avoid": ["human_handoff", "投诉"],
    },
    {
        "scenario_id": "store_nearest",
        "purpose": "验证客户给出位置后，系统能否直接推荐最近门店并解释理由。",
        "conversation_history": [
            "用户: 你好，我想先去店里看看",
            "小贝: 可以，你在什么城市或附近地标呢？",
        ],
        "variants": [
            "我在厦门机场附近",
            "我在西安中贸这边",
            "我在上海浦东，想找近一点的",
        ],
        "expected": ["店", "近", "方便", "地址"],
        "avoid": ["你想做什么项目"],
    },
    {
        "scenario_id": "appointment_time",
        "purpose": "验证客户表达到店意向时，系统是否正确确认门店与时间，而不是直接算预约成功。",
        "conversation_history": [
            "用户: 我在厦门机场附近",
            "小贝: 按你位置更方便的是厦门思明店。",
        ],
        "variants": [
            "那我今天下午5点能过去吗",
            "明天上午可以帮我约一下吗",
            "我现在过去来得及吗",
        ],
        "expected": ["帮你看", "时间", "可约", "门店"],
        "avoid": ["等您", "已预约成功"],
    },
    {
        "scenario_id": "after_sales_discontent",
        "purpose": "验证已做过没效果场景是否先复盘问题，再决定是否转人工。",
        "conversation_history": [
            "用户: 我之前在你们店做过",
        ],
        "variants": [
            "已经做了2次，不见效果呢",
            "这个店我去过，一点效果都没有",
            "做了之后感觉跟没做一样",
        ],
        "expected": ["做的什么", "哪家店", "不满意", "情况"],
        "avoid": ["正常", "再做几次就好"],
    },
    {
        "scenario_id": "complaint_refund",
        "purpose": "验证强客诉退款场景是否正确触发人工协助，并保持客户可见内容克制。",
        "conversation_history": [
            "用户: 我参加了你们活动",
        ],
        "variants": [
            "把10元退给我，不然我就投诉",
            "你们就是骗人的，把钱退我",
            "做成这样还收费，我要退款",
        ],
        "expected": ["专业同事", "核对", "付款", "情况"],
        "avoid": ["马上退", "肯定能退"],
    },
    {
        "scenario_id": "distance_objection",
        "purpose": "验证客户说太远、没时间去时，系统是否给替代方案，而不是直接放弃。",
        "conversation_history": [
            "用户: 我在厦门机场附近",
            "小贝: 这边更方便的是厦门思明店。",
        ],
        "variants": [
            "太远了，没有时间去",
            "我平时太忙了，不想专门跑一趟",
            "有没有更方便一点的店",
        ],
        "expected": ["方便", "最近", "时间", "安排"],
        "avoid": ["那没办法", "有空再说"],
    },
]


def request_chat(case_id: str, content: str, conversation_history: list[str]) -> tuple[dict[str, Any], int, str | None]:
    payload = {
        **BASE_PAYLOAD,
        "content": content,
        "customer_id": case_id,
        "external_userid": case_id,
        "conversation_history": conversation_history[-10:],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw), int((time.perf_counter() - start) * 1000), None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw), int((time.perf_counter() - start) * 1000), f"HTTP {exc.code}"
        except Exception:
            return {"raw_error": raw}, int((time.perf_counter() - start) * 1000), f"HTTP {exc.code}"
    except Exception as exc:
        return {"raw_error": str(exc)}, int((time.perf_counter() - start) * 1000), type(exc).__name__


def extract_reply_texts(data: dict[str, Any]) -> list[str]:
    reply_messages = data.get("reply_messages") or []
    texts: list[str] = []
    if not isinstance(reply_messages, list):
        return texts
    for item in reply_messages:
        content = item.get("content")
        if isinstance(content, dict):
            if item.get("type") == "text":
                texts.append(str(content.get("text") or ""))
            elif item.get("type") == "human_handoff":
                texts.append(f"[human_handoff] {json.dumps(content, ensure_ascii=False)}")
            else:
                texts.append(json.dumps(content, ensure_ascii=False))
        else:
            texts.append(str(content or ""))
    return [t for t in texts if t]


def evaluate_reply(text: str, expected: list[str], avoid: list[str]) -> dict[str, Any]:
    lower_text = text.lower()
    expected_hits = [w for w in expected if w and w.lower() in lower_text]
    avoid_hits = [w for w in avoid if w and w.lower() in lower_text]
    score = 0
    if text.strip():
        score += 1
    score += min(len(expected_hits), 2)
    score -= len(avoid_hits)
    passed = score >= 2 and not avoid_hits
    return {
        "passed": passed,
        "score": score,
        "expected_hits": expected_hits,
        "avoid_hits": avoid_hits,
    }


def run_one(scenario: dict[str, Any], variant_idx: int, question: str) -> dict[str, Any]:
    case_id = f"parallel_{scenario['scenario_id']}_{variant_idx}_{uuid.uuid4().hex[:8]}"
    data, elapsed_ms, error = request_chat(case_id, question, scenario.get("conversation_history", []))
    reply_texts = extract_reply_texts(data)
    joined = "\n".join(reply_texts)
    eval_result = evaluate_reply(joined, scenario.get("expected", []), scenario.get("avoid", []))
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return {
        "scenario_id": scenario["scenario_id"],
        "purpose": scenario["purpose"],
        "variant_index": variant_idx,
        "question": question,
        "customer_id": case_id,
        "elapsed_ms": elapsed_ms,
        "success": error is None,
        "error": error,
        "intent": data.get("intent", ""),
        "subflow": data.get("subflow", ""),
        "scene": data.get("scene", ""),
        "request_id": data.get("request_id", ""),
        "tool_result_keys": meta.get("tool_result_keys", []),
        "tool_calls": meta.get("tool_calls", []),
        "reply_messages": reply_texts,
        "reply_excerpt": joined[:220],
        "evaluation": eval_result,
    }


def build_md_report(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 并发话术场景测试报告")
    lines.append("")
    lines.append(f"- 生成时间：{summary['generated_at']}")
    lines.append(f"- 接口：`{summary['api_url']}`")
    lines.append(f"- 总请求数：{summary['total']}")
    lines.append(f"- 成功数：{summary['success']}")
    lines.append(f"- 成功率：{summary['success_rate']}%")
    lines.append(f"- 平均耗时：{summary['avg_elapsed_ms']}ms")
    lines.append(f"- 最大耗时：{summary['max_elapsed_ms']}ms")
    lines.append(f"- 最小耗时：{summary['min_elapsed_ms']}ms")
    lines.append("")
    lines.append("## 场景汇总")
    lines.append("")
    lines.append("| 场景 | 测试目的 | 请求数 | 成功率 | 通过率 | 平均耗时(ms) |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for row in summary["scenario_summary"]:
        lines.append(
            f"| {row['scenario_id']} | {row['purpose']} | {row['count']} | {row['success_rate']}% | {row['pass_rate']}% | {row['avg_elapsed_ms']} |"
        )
    lines.append("")
    lines.append("## 详细结果")
    lines.append("")
    lines.append("| 场景 | 变体 | 问题 | 目的 | 结果 | 意图/子流程 | 耗时(ms) | 主要回复摘录 | 主要分析 |")
    lines.append("|---|---:|---|---|---|---|---:|---|---|")
    for item in summary["results"]:
        eval_result = item["evaluation"]
        result_label = "通过" if eval_result["passed"] and item["success"] else ("失败" if not item["success"] else "部分通过")
        analysis = []
        if not item["success"]:
            analysis.append(f"接口失败:{item['error']}")
        if eval_result["expected_hits"]:
            analysis.append("命中:" + "、".join(eval_result["expected_hits"]))
        if eval_result["avoid_hits"]:
            analysis.append("风险:" + "、".join(eval_result["avoid_hits"]))
        if not analysis:
            analysis.append("需要人工复核回复质量")
        excerpt = item["reply_excerpt"].replace("|", "/").replace("\n", " ")
        question = item["question"].replace("|", "/")
        purpose = item["purpose"].replace("|", "/")
        lines.append(
            f"| {item['scenario_id']} | {item['variant_index']} | {question} | {purpose} | {result_label} | {item['intent']}/{item['subflow']} | {item['elapsed_ms']} | {excerpt} | {'；'.join(analysis)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    started = time.time()
    jobs: list[tuple[dict[str, Any], int, str]] = []
    for scenario in SCENARIOS:
        for idx, question in enumerate(scenario["variants"], start=1):
            jobs.append((scenario, idx, question))

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {
            executor.submit(run_one, scenario, idx, question): (scenario["scenario_id"], idx)
            for scenario, idx, question in jobs
        }
        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda x: (x["scenario_id"], x["variant_index"]))
    total = len(results)
    success = sum(1 for x in results if x["success"])
    elapsed_values = [x["elapsed_ms"] for x in results]

    scenario_summary: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        items = [x for x in results if x["scenario_id"] == scenario["scenario_id"]]
        if not items:
            continue
        scenario_summary.append(
            {
                "scenario_id": scenario["scenario_id"],
                "purpose": scenario["purpose"],
                "count": len(items),
                "success_rate": round(sum(1 for x in items if x["success"]) / len(items) * 100, 2),
                "pass_rate": round(sum(1 for x in items if x["success"] and x["evaluation"]["passed"]) / len(items) * 100, 2),
                "avg_elapsed_ms": round(sum(x["elapsed_ms"] for x in items) / len(items), 2),
            }
        )

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": round(time.time() - started, 2),
        "api_url": API_URL,
        "total": total,
        "success": success,
        "success_rate": round(success / total * 100, 2) if total else 0,
        "avg_elapsed_ms": round(sum(elapsed_values) / len(elapsed_values), 2) if elapsed_values else 0,
        "max_elapsed_ms": max(elapsed_values) if elapsed_values else 0,
        "min_elapsed_ms": min(elapsed_values) if elapsed_values else 0,
        "scenario_summary": scenario_summary,
        "results": results,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    MD_REPORT_PATH.write_text(build_md_report(summary), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

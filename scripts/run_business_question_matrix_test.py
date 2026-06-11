# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API_URL = os.getenv("AI_PATHS_API_URL", "http://47.252.81.104/api/ai/chat")
REPORT_JSON = ROOT / "logs/business_question_matrix_report.json"
REPORT_MD = ROOT / "logs/business_question_matrix_report.md"
PREVIEW_PATH = ROOT / "projects/public/test-conversations.json"

BASE_PAYLOAD = {
    "corp_id": "ent-753d018266f7453285311ce1d5ed0d94",
    "user_id": "DY1032",
    "wechat": "DY1032",
}


CASES: list[dict[str, Any]] = [
    {
        "id": "once_done",
        "question": "一次做好吗",
        "purpose": "验证一次见效/次数问题是否能给大致预期且不承诺效果。",
        "history": ["用户: 我想了解祛斑，主要是脸上点状斑。", "小贝: 这类可以先看淡斑和肤色改善方向。"],
        "expected": ["一次", "因人", "斑", "变化"],
        "avoid": ["保证", "包", "一定"],
    },
    {
        "id": "skin_harm",
        "question": "会伤害皮肤吗",
        "purpose": "验证安全顾虑是否正面解释，而不是空泛免责。",
        "history": ["用户: 我想了解祛斑活动。", "小贝: 可以先看淡斑和肤色改善方向。"],
        "expected": ["皮肤", "评估", "护理", "温和"],
        "avoid": ["绝对安全", "完全不会"],
    },
    {
        "id": "confirm_268",
        "question": "确定268吗",
        "purpose": "验证活动价确认是否解释清楚价格口径和到店核对。",
        "history": ["用户: 我看到广告上写祛斑268。", "小贝: 活动价需要看具体配置和门店当前活动。"],
        "expected": ["268", "活动", "到店", "核对"],
        "avoid": ["不知道", "不清楚"],
    },
    {
        "id": "single_fee",
        "question": "是一次的费用吗",
        "purpose": "验证单次费用问题是否正面说明，不绕回项目名。",
        "history": ["用户: 我看到广告上写祛斑268。", "小贝: 这个通常是活动体验价，需要看具体活动规则。"],
        "expected": ["一次", "活动", "体验", "包含"],
        "avoid": ["你想问哪个项目"],
    },
    {
        "id": "how_many_times",
        "question": "要做多少次",
        "purpose": "验证疗程次数问题是否给判断范围和影响因素。",
        "history": ["用户: 我脸上斑点比较多，想淡一点。", "小贝: 这类先看淡斑和肤色改善方向。"],
        "expected": ["次数", "斑", "深浅", "范围"],
        "avoid": ["必须", "一定"],
    },
    {
        "id": "are_you_store",
        "question": "你是门店的人吗",
        "purpose": "验证身份问题是否用小贝人设承接，不透露AI。",
        "history": ["用户: 我想问一下你们活动。"],
        "expected": ["小贝", "门店", "咨询", "安排"],
        "avoid": ["AI", "机器人"],
    },
    {
        "id": "qualification",
        "question": "有资质吗",
        "purpose": "验证资质信任问题是否说明正规资质和可到店查看。",
        "history": ["用户: 你们祛斑靠谱吗？"],
        "expected": ["资质", "正规", "门店", "查看"],
        "avoid": ["绝对", "不用担心"],
    },
    {
        "id": "hidden_fee",
        "question": "到店会乱收费吗",
        "purpose": "验证隐形消费顾虑是否解释费用透明和核对流程。",
        "history": ["用户: 广告价是268吗？"],
        "expected": ["费用", "清楚", "核对", "不会"],
        "avoid": ["保证", "绝对"],
    },
    {
        "id": "case_times",
        "question": "图片上的客户做了多少次",
        "purpose": "验证效果图次数追问是否承接案例上下文。",
        "history": ["用户: 你发的这个效果图看着不错。", "小贝: 这个是同类淡斑参考案例。"],
        "expected": ["案例", "次数", "参考", "个人"],
        "avoid": ["你想了解哪个项目"],
    },
    {
        "id": "price_380_conflict",
        "question": "为什么一样的地方还有380的价格",
        "purpose": "验证不同活动价格冲突是否解释活动/配置/部位差异。",
        "history": ["用户: 我看到广告是268。", "小贝: 活动价格会和项目配置、部位有关。"],
        "expected": ["380", "268", "活动", "配置"],
        "avoid": ["不知道", "先到店再说"],
    },
    {
        "id": "hand_price",
        "question": "手上的价格是多少  199是一只还是一双",
        "purpose": "验证局部/双侧价格口径问题是否澄清部位和单/双侧。",
        "history": ["用户: 我看到手部项目广告199。"],
        "expected": ["手", "199", "一只", "一双"],
        "avoid": ["脸", "祛斑"],
    },
    {
        "id": "pay_after",
        "question": "是做完付款吗",
        "purpose": "验证付款时点是否说明定金/到店尾款规则。",
        "history": ["用户: 我想参加活动，广告说先付10元。"],
        "expected": ["定金", "到店", "尾款", "付款"],
        "avoid": ["全款已付", "不用付"],
    },
    {
        "id": "travel_reimburse",
        "question": "有车费报销吗 可以包接送吗",
        "purpose": "验证交通补贴/接送问题是否避免承诺并引导按活动规则核对。",
        "history": ["用户: 门店离我有点远。"],
        "expected": ["车费", "接送", "活动", "核对"],
        "avoid": ["可以报销", "包接送"],
    },
    {
        "id": "mole_price",
        "question": "可以去痣吗 去痣要多少钱",
        "purpose": "验证项目外需求是否承接并提示需要看位置大小。",
        "history": ["用户: 你们都做哪些皮肤项目？"],
        "expected": ["痣", "位置", "大小", "评估"],
        "avoid": ["祛斑268"],
    },
    {
        "id": "effect_times",
        "question": "你发的效果图是做几次的效果",
        "purpose": "验证效果图次数追问是否承认案例差异，不编具体次数。",
        "history": ["用户: 这个效果图不错。", "小贝: 这是淡斑方向的参考案例。"],
        "expected": ["效果图", "几次", "案例", "参考"],
        "avoid": ["一次就能", "保证"],
    },
    {
        "id": "address_trust",
        "question": "为什么不敢发详细地址",
        "purpose": "验证地址信任/质疑是否转为门店信息说明，而不是防御。",
        "history": ["用户: 你们厦门有店吗？", "小贝: 厦门有思明、二店、百星几家门店。"],
        "expected": ["地址", "门店", "发", "导航"],
        "avoid": ["不敢", "不能发"],
    },
    {
        "id": "medical_qualification",
        "question": "你们有医疗资质吗？",
        "purpose": "验证医疗资质强信任问题是否准确克制。",
        "history": ["用户: 我怕不正规。"],
        "expected": ["资质", "正规", "门店", "查看"],
        "avoid": ["绝对正规", "不用查"],
    },
    {
        "id": "after_two_no_effect",
        "question": "己做了2次，不见效果呢？",
        "purpose": "验证效果不满/售后问题是否先收集项目门店时间并避免甩锅。",
        "history": ["用户: 我之前做过淡斑。"],
        "expected": ["2次", "项目", "时间", "情况"],
        "avoid": ["正常", "继续做就好"],
    },
    {
        "id": "store_name",
        "question": "你们门店名字叫什么",
        "purpose": "验证门店名称问题是否直接回答并结合城市确认。",
        "history": ["用户: 我在厦门，想去店里看。"],
        "expected": ["门店", "厦门", "思明", "百星"],
        "avoid": ["你想做什么项目"],
    },
    {
        "id": "price_268_308",
        "question": "我看到是268的 你怎么跟我说308的价格",
        "purpose": "验证价格冲突能否解释不同活动口径。",
        "history": ["用户: 我看到广告268。", "小贝: 这个活动到店需要核对配置。"],
        "expected": ["268", "308", "活动", "口径"],
        "avoid": ["你看错了", "不知道"],
    },
    {
        "id": "no_deposit",
        "question": "我不交定金到店再付全款",
        "purpose": "验证拒绝定金是否解释活动锁定逻辑并给可选方案。",
        "history": ["用户: 活动需要先付10元吗？"],
        "expected": ["定金", "名额", "到店", "全款"],
        "avoid": ["必须交", "不交不行"],
    },
    {
        "id": "too_far",
        "question": "太远了 没有时间去",
        "purpose": "验证距离/时间异议是否尝试推荐近店或灵活时间。",
        "history": ["用户: 我在厦门机场附近。", "小贝: 厦门思明店相对近一些。"],
        "expected": ["近", "时间", "方便", "门店"],
        "avoid": ["那没办法"],
    },
    {
        "id": "bad_past_result",
        "question": "这个店我去过 一点效果都没有",
        "purpose": "验证既往不满是否安抚并复盘，不直接推新项目。",
        "history": ["用户: 你们厦门百星店吗？"],
        "expected": ["体验", "不满意", "项目", "情况"],
        "avoid": ["再来一次", "一定有效"],
    },
    {
        "id": "tibet_store_ad",
        "question": "看广告西藏有门店啊",
        "purpose": "验证不存在/疑似广告误解的门店查询是否核对并不乱编。",
        "history": ["用户: 我想找附近门店。"],
        "expected": ["西藏", "门店", "核对", "广告"],
        "avoid": ["西藏店地址"],
    },
]


def request_chat(case: dict[str, Any]) -> dict[str, Any]:
    customer_id = f"matrix_{case['id']}_{uuid.uuid4().hex[:8]}"
    payload = {
        **BASE_PAYLOAD,
        "content": case["question"],
        "customer_id": customer_id,
        "external_userid": customer_id,
        "conversation_history": case["history"][-10:],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=220) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            error = None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw_error": raw}
        error = f"HTTP {exc.code}"
    except Exception as exc:
        data = {"raw_error": str(exc)}
        error = type(exc).__name__
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "case": case,
        "customer_id": customer_id,
        "elapsed_ms": elapsed_ms,
        "error": error,
        "response": data,
        "reply_text": "\n".join(extract_reply_texts(data)),
    }


def extract_reply_texts(data: dict[str, Any]) -> list[str]:
    messages = data.get("reply_messages") or []
    texts: list[str] = []
    if not isinstance(messages, list):
        return texts
    for message in messages:
        msg_type = message.get("type")
        content = message.get("content")
        if isinstance(content, dict):
            if msg_type == "text":
                text = str(content.get("text") or "")
            elif msg_type == "human_handoff":
                text = f"[human_handoff] {content.get('handoff_reason') or ''}"
            else:
                text = json.dumps(content, ensure_ascii=False)
        else:
            text = str(content or "")
        if text.strip():
            texts.append(text.strip())
    return texts


def evaluate(text: str, expected: list[str], avoid: list[str], error: str | None) -> dict[str, Any]:
    if error:
        return {"grade": "接口失败", "score": 0, "expected_hits": [], "avoid_hits": [], "notes": error}
    expected_hits = [item for item in expected if item in text]
    avoid_hits = [item for item in avoid if item in text]
    score = 0
    if text.strip():
        score += 1
    score += min(len(expected_hits), 3)
    score -= len(avoid_hits) * 2
    if len(text) > 240:
        score -= 1
    if score >= 4 and not avoid_hits:
        grade = "好"
    elif score >= 2 and len(avoid_hits) <= 1:
        grade = "可用"
    else:
        grade = "差"
    notes = []
    if not expected_hits:
        notes.append("缺少关键承接点")
    if avoid_hits:
        notes.append("出现风险表达：" + "、".join(avoid_hits))
    if len(text) > 240:
        notes.append("回复偏长")
    return {
        "grade": grade,
        "score": score,
        "expected_hits": expected_hits,
        "avoid_hits": avoid_hits,
        "notes": "；".join(notes) if notes else "基本命中",
    }


def make_preview(results: list[dict[str, Any]]) -> None:
    now = int(time.time() * 1000)
    messages: list[dict[str, Any]] = []
    for idx, result in enumerate(results, 1):
        case = result["case"]
        messages.append(
            {
                "id": f"matrix_u_{idx}",
                "role": "user",
                "content": case["question"],
                "timestamp": now + idx * 1000,
            }
        )
        for reply_idx, reply in enumerate(extract_reply_texts(result["response"]), 1):
            messages.append(
                {
                    "id": f"matrix_a_{idx}_{reply_idx}",
                    "role": "assistant",
                    "content": reply,
                    "timestamp": now + idx * 1000 + reply_idx,
                    "duration": result["elapsed_ms"],
                    "meta": {
                        "requestId": result["response"].get("request_id", ""),
                        "intent": result["response"].get("intent", ""),
                        "subflow": result["response"].get("subflow", ""),
                        "grade": result["evaluation"]["grade"],
                        "purpose": case["purpose"],
                    }
                    if reply_idx == 1
                    else None,
                }
            )
    preview = {
        "conversations": [
            {
                "id": f"business_question_matrix_{int(time.time())}",
                "title": "业务问题矩阵测试-24问",
                "createdAt": now,
                "updatedAt": now,
                "messages": messages,
            }
        ]
    }
    PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_PATH.write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")


def render_markdown(results: list[dict[str, Any]]) -> str:
    total = len(results)
    ok = sum(1 for r in results if r["evaluation"]["grade"] in {"好", "可用"})
    failed = sum(1 for r in results if r["error"])
    avg = int(sum(r["elapsed_ms"] for r in results) / total) if total else 0
    lines = [
        "# 业务问题矩阵测试报告",
        "",
        f"- 测试接口：`{API_URL}`",
        f"- 测试时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 用例数：{total}",
        f"- 可用率：{ok}/{total}",
        f"- 接口失败：{failed}",
        f"- 平均耗时：{avg / 1000:.1f}s",
        "",
        "| 序号 | 问题 | 测试目的 | 意图/子流程 | 耗时 | 评价 | 命中点 | 问题分析 | 回复摘要 |",
        "|---:|---|---|---|---:|---|---|---|---|",
    ]
    for idx, result in enumerate(results, 1):
        case = result["case"]
        resp = result["response"]
        evaluation = result["evaluation"]
        reply = result["reply_text"].replace("\n", "<br>")
        if len(reply) > 180:
            reply = reply[:180] + "..."
        lines.append(
            "| {idx} | {q} | {purpose} | {intent}/{subflow} | {elapsed:.1f}s | {grade} | {hits} | {notes} | {reply} |".format(
                idx=idx,
                q=case["question"].replace("|", "\\|"),
                purpose=case["purpose"].replace("|", "\\|"),
                intent=str(resp.get("intent", "")).replace("|", "\\|"),
                subflow=str(resp.get("subflow", "")).replace("|", "\\|"),
                elapsed=result["elapsed_ms"] / 1000,
                grade=evaluation["grade"],
                hits="、".join(evaluation["expected_hits"]) or "-",
                notes=evaluation["notes"].replace("|", "\\|"),
                reply=reply.replace("|", "\\|"),
            )
        )
    bad = [r for r in results if r["evaluation"]["grade"] == "差" or r["error"]]
    lines.extend(["", "## 需要重点优化", ""])
    if not bad:
        lines.append("- 暂无明显失败用例。")
    for result in bad:
        case = result["case"]
        lines.append(f"- `{case['question']}`：{result['evaluation']['notes']}。测试目的：{case['purpose']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for case in CASES:
        result = request_chat(case)
        result["evaluation"] = evaluate(
            result["reply_text"],
            case["expected"],
            case["avoid"],
            result["error"],
        )
        results.append(result)
        print(
            f"[{len(results):02d}/{len(CASES)}] {case['question']} -> "
            f"{result['evaluation']['grade']} ({result['elapsed_ms'] / 1000:.1f}s)"
        )
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_url": API_URL,
        "results": results,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(results), encoding="utf-8")
    make_preview(results)
    print(f"JSON report: {REPORT_JSON}")
    print(f"Markdown report: {REPORT_MD}")
    print(f"Preview conversations: {PREVIEW_PATH}")


if __name__ == "__main__":
    main()

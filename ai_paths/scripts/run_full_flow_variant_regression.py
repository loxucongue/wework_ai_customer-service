from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .test_conversation_store import append_test_conversations
except ImportError:
    from test_conversation_store import append_test_conversations


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_API_URL = "http://127.0.0.1:8000/chat/workflow-compatible"
DEFAULT_WORKFLOW_ID = "xiaobei-default"
DEFAULT_CORP_ID = "ent-753d018266f7453285311ce1d5ed0d94"
DEFAULT_USER_ID = "DY1032"
DEFAULT_WECHAT = "DY1032"
DEFAULT_PUBLIC_FILE = ROOT / "projects" / "public" / "test-conversations.json"
DEFAULT_REPORT_FILE = ROOT / "logs" / f"full_flow_variant_regression_{time.strftime('%Y%m%d%H%M%S')}.json"
CASE_IMAGE_URL = (
    "https://coze-coding-project.tos.coze.site/coze_storage_7641059342338457652/"
    "chat_images/user_uploaded_image_94bf3ee7.png?"
    "sign=1780476697-341f4fe0ca-0-7f6262b376e2e480885690a14adb2234e935701d75a4b23014bdbb072800ed2b"
)


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "variant_ad_consult",
        "title": "Codex全流程裂变-广告进线多意图",
        "turns": [
            {"content": "我是在抖音上看到你们祛斑活动加过来的，想先问下价格、效果，还有去店里方不方便。"},
            {"content": "像我这种脸上零零碎碎的小黑点，能不能做到你们图里那种变化？"},
            {"content": "一次能看出来变化吗，还是要做好几次？"},
            {"content": "你先别光讲概念，直接说大概要多少钱。"},
            {"content": "广告上说两百多，是不是去了就这个价，没有别的收费吧？"},
            {"content": "我人在厦门机场这边，哪家店离我近一点？"},
            {"content": "停车方便吗，把地址也一起发我。"},
            {"content": "那我明天下午过去看看，有没有时间？"},
            {"content": "如果先交10块，是留名额还是抵扣？"},
            {"content": "我叫罗阿姨，电话19976988097。"},
            {"content": "到店前要不要素颜，要带什么？"},
        ],
    },
    {
        "id": "variant_effect_trust",
        "title": "Codex全流程裂变-效果质疑与信任承接",
        "turns": [
            {"content": "你好，我年纪偏大了，脸上色沉和黑色素有点明显，还能改善吗？"},
            {"content": "别到时候花了钱也没变化，我最担心这个。"},
            {"content": "先给我看看你们做过的同类对比。"},
            {"content": "图片上的这种一般是做了几次才有这样的？"},
            {"content": "会不会反弹，或者越做皮肤越薄？"},
            {"content": "你们正规吧，到店不会东加一点西加一点吧？"},
            {"content": "我预算不想太高，你直接给我一个适合我的方向。"},
            {"content": "那我先发个图你看看。", "file_image": CASE_IMAGE_URL, "msgtype": "image"},
            {"content": "可以，你们这个真的能看到变化吗，我想改善我的黑色素。"},
            {"content": "如果我先去店里看看，你们给我安排哪家方便？我在长沙岳麓区。"},
            {"content": "周六下午能去的话就帮我往下安排。"},
        ],
    },
    {
        "id": "variant_store_route",
        "title": "Codex全流程裂变-门店推荐与地址跟进",
        "turns": [
            {"content": "我已经添加你了，现在可以聊了。"},
            {"content": "长沙有你们门店吧？都在哪些位置？"},
            {"content": "我在长沙岳麓区西湖公园边上，别都发给我，直接推荐最近的。"},
            {"content": "从我这里过去大概要多久？"},
            {"content": "把这家具体地址、停车、营业时间一起说清楚。"},
            {"content": "我主要是想改善脸上暗沉和毛孔，你觉得能做吗？"},
            {"content": "那价格大概怎么收，别给我来一堆听不懂的项目名。"},
            {"content": "如果我上午过去，是不是可以先给我留个时间？"},
            {"content": "报名的话是不是先登记名字电话？"},
            {"content": "我姓周，号码13800138000。"},
            {"content": "如果当天我临时去不了，这个10块还能改吗？"},
        ],
    },
    {
        "id": "variant_aftersales_boundary",
        "title": "Codex全流程裂变-效果不满与边界处理",
        "turns": [
            {"content": "我之前在别的地方做过一次斑，没什么效果，所以这次比较谨慎。"},
            {"content": "你们这边到底是不是做完就能看出来？"},
            {"content": "还有，我最怕去了以后一直加钱。"},
            {"content": "你先别让我到店，先说说你们这种一般怎么做，流程多久。"},
            {"content": "客户做完后的变化图还有吗？"},
            {"content": "如果我看着还行，再去厦门店，机场附近那个店能安排吗？"},
            {"content": "明天下午一两点附近有空档没有？"},
            {"content": "那我先留个名字，叫罗学聪。"},
            {"content": "电话19976988097。"},
            {"content": "如果我后面不满意，这10块是不是能退？"},
            {"content": "行，那你继续帮我往下安排。"},
        ],
    },
]


def _post_json(api_url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any], str, int]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": repr(exc)}, repr(exc), int((time.perf_counter() - start) * 1000)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"raw": raw}
    return status, parsed, raw, int((time.perf_counter() - start) * 1000)


def _extract_reply_messages(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    if isinstance(data, dict) and isinstance(data.get("reply_messages"), list):
        return [item for item in data["reply_messages"] if isinstance(item, dict)]
    if isinstance(response.get("reply_messages"), list):
        return [item for item in response["reply_messages"] if isinstance(item, dict)]
    return []


def _visible_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    message_type = str(message.get("type") or "text")
    if isinstance(content, dict):
        if message_type == "text":
            return str(content.get("text") or "").strip()
        if message_type == "image":
            return str(content.get("url") or "[图片]").strip()
        if message_type == "human_handoff":
            return str(content.get("handoff_reason") or "[需人工跟进]").strip()
        if message_type == "book_order":
            order_id = str(content.get("order_id") or "").strip()
            return f"[book_order:{order_id}]" if order_id else "[book_order]"
        return str(content.get("text") or json.dumps(content, ensure_ascii=False)).strip()
    return str(content or "").strip()


def _frontend_content(message: dict[str, Any]) -> str | dict[str, Any]:
    content = message.get("content")
    if isinstance(content, dict):
        return content
    return str(content or "")


def _score_turn(user_text: str, replies: list[str], status: int, code: Any) -> tuple[int, list[str]]:
    notes: list[str] = []
    if status != 200 or code != 0:
        notes.append("接口失败或业务 code 非 0")
    if not replies:
        notes.append("没有客户可见回复")
        return 20, notes
    joined = "\n".join(replies)
    score = 100
    if len(replies) > 2:
        score -= 10
        notes.append("回复条数偏多")
    if any(token in joined for token in ("系统", "知识库", "工具返回", "我是AI")):
        score -= 25
        notes.append("出现系统化措辞")
    if any(token in user_text for token in ("多少钱", "价格", "收费", "两百多", "10块")) and not any(
        token in joined for token in ("价格", "收费", "10", "定金", "预约金", "抵扣", "活动")
    ):
        score -= 20
        notes.append("价格/收费核心问题承接不足")
    if any(token in user_text for token in ("效果", "变化", "黑色素", "色沉", "反弹")) and not any(
        token in joined for token in ("变化", "改善", "参考", "案例", "方向", "因人")
    ):
        score -= 20
        notes.append("效果或案例承接不足")
    if any(token in user_text for token in ("门店", "机场", "地址", "停车")) and not any(
        token in joined for token in ("店", "地址", "停车", "导航", "机场", "公里", "分钟")
    ):
        score -= 20
        notes.append("门店地址问题承接不足")
    if any(token in user_text for token in ("预约", "明天", "周六", "报名", "名字", "电话")) and not any(
        token in joined for token in ("时间", "预约", "登记", "名字", "电话", "安排", "名额")
    ):
        score -= 20
        notes.append("预约推进承接不足")
    if "你好" == user_text.strip() and len(joined.strip()) <= 8:
        score -= 15
        notes.append("开场过短，缺少接待推进")
    return max(score, 0), notes


def _build_payload(
    workflow_id: str,
    customer_id: str,
    turn: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    payload: dict[str, Any] = {
        "workflow_id": workflow_id,
        "parameters": {
            "customer_id": customer_id,
            "external_userid": customer_id,
            "corp_id": DEFAULT_CORP_ID,
            "user_id": DEFAULT_USER_ID,
            "wechat": DEFAULT_WECHAT,
            "content": {
                "content": turn["content"],
                "msgid": f"{customer_id}_{now_ms}_external",
                "msgtime": now_ms,
                "msgtype": turn.get("msgtype", "text"),
            },
            "messages": history,
            "request_context": {
                "conversation_id": customer_id,
                "customer_id": customer_id,
                "test_customer": True,
            },
        },
    }
    if turn.get("file_image"):
        payload["parameters"]["file_image"] = turn["file_image"]
    return payload


def _append_history(history: list[dict[str, Any]], direction: str, content: str, msgtype: str = "text") -> None:
    history.append(
        {
            "direction": direction,
            "content": content,
            "msgtype": msgtype,
            "msgtime": int(time.time() * 1000),
        }
    )


def _run_scenario(api_url: str, workflow_id: str, scenario: dict[str, Any], run_suffix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    conversation_id = f"{scenario['id']}_{run_suffix}"
    customer_id = f"{scenario['id']}_{run_suffix}"
    created_at = int(time.time() * 1000)
    history: list[dict[str, Any]] = []
    frontend_messages: list[dict[str, Any]] = []
    report_turns: list[dict[str, Any]] = []
    msg_counter = 1

    for index, turn in enumerate(scenario["turns"], start=1):
        user_ts = int(time.time() * 1000)
        frontend_messages.append(
            {
                "id": f"{conversation_id}_u_{msg_counter}",
                "role": "user",
                "content": turn["content"],
                "timestamp": user_ts,
                **({"imageUrl": turn["file_image"]} if turn.get("file_image") else {}),
            }
        )
        msg_counter += 1
        _append_history(history, "customer", turn["content"], str(turn.get("msgtype", "text")))

        payload = _build_payload(workflow_id, customer_id, turn, history)
        status, response, raw, elapsed_ms = _post_json(api_url, payload)
        code = response.get("code")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        reply_messages = _extract_reply_messages(response)
        trace_id = str(data.get("trace_id") or response.get("execute_id") or "")
        visible_replies = [_visible_text(item) for item in reply_messages if _visible_text(item)]
        score, notes = _score_turn(str(turn["content"]), visible_replies, status, code)

        for ridx, item in enumerate(reply_messages, start=1):
            visible = _visible_text(item)
            if visible and str(item.get("type") or "text") == "text":
                _append_history(history, "service", visible)
            frontend_messages.append(
                {
                    "id": f"{conversation_id}_a_{msg_counter}",
                    "role": "assistant",
                    "content": _frontend_content(item),
                    "contentType": str(item.get("type") or "text"),
                    "timestamp": int(time.time() * 1000) + ridx,
                    "duration": elapsed_ms,
                    **(
                        {
                            "meta": {
                                "requestId": trace_id,
                                "raw": {"workflow_response": response},
                            }
                        }
                        if ridx == 1
                        else {}
                    ),
                }
            )
            msg_counter += 1

        report_turns.append(
            {
                "turn": index,
                "user_text": turn["content"],
                "status": status,
                "code": code,
                "elapsed_ms": elapsed_ms,
                "trace_id": trace_id,
                "intent": data.get("intent") or response.get("intent") or "",
                "scene": data.get("scene") or response.get("scene") or "",
                "subflow": data.get("subflow") or response.get("subflow") or "",
                "reply_messages": reply_messages,
                "visible_replies": visible_replies,
                "score": score,
                "notes": notes,
                "error": data.get("error") or response.get("msg") or (raw if status != 200 else ""),
            }
        )

        print(
            json.dumps(
                {
                    "scenario": scenario["id"],
                    "turn": index,
                    "status": status,
                    "code": code,
                    "elapsed_ms": elapsed_ms,
                    "reply_count": len(reply_messages),
                    "score": score,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    conversation = {
        "id": conversation_id,
        "title": f"{scenario['title']}（{run_suffix}）",
        "messages": frontend_messages,
        "createdAt": created_at,
        "updatedAt": int(time.time() * 1000),
    }
    report = {
        "scenario_id": scenario["id"],
        "title": conversation["title"],
        "turn_count": len(report_turns),
        "average_score": round(sum(item["score"] for item in report_turns) / max(len(report_turns), 1), 1),
        "average_elapsed_ms": round(sum(item["elapsed_ms"] for item in report_turns) / max(len(report_turns), 1), 1),
        "failures": [item for item in report_turns if item["status"] != 200 or item["code"] != 0],
        "turns": report_turns,
    }
    return conversation, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full-flow variant regression and append conversations to local preview.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--run-suffix", default=time.strftime("%Y%m%d%H%M%S"))
    parser.add_argument("--report-output", default=str(DEFAULT_REPORT_FILE))
    parser.add_argument("--conversation-output", default=str(DEFAULT_PUBLIC_FILE))
    parser.add_argument("--scenario-id", action="append", default=[])
    args = parser.parse_args()

    selected_ids = {item for item in args.scenario_id if item}
    scenarios = [item for item in SCENARIOS if not selected_ids or item["id"] in selected_ids]
    reports: list[dict[str, Any]] = []
    report_path = Path(args.report_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    for scenario in scenarios:
        conversation, report = _run_scenario(args.api_url, args.workflow_id, scenario, args.run_suffix)
        reports.append(report)
        append_test_conversations([conversation], path=Path(args.conversation_output))
        report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"saved_scenario": scenario["id"], "report": str(report_path)}, ensure_ascii=False), flush=True)

    print(json.dumps({"report": str(report_path), "conversation_count": len(reports)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

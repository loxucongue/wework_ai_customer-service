from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


URL = os.environ.get("AI_PATHS_ONLINE_REPLY_URL", "http://47.252.81.104/api/ai/reply/workflow-compatible")
KEY = os.environ.get("AI_EXTERNAL_API_KEY", "").strip()
OUT_DIR = Path("logs")
OUT_DIR.mkdir(exist_ok=True)

CASES = [
    ("项目咨询", "我想了解一下祛斑", "承接斑点改善方向，不诊断，轻量推进城市/到店"),
    ("项目咨询", "你们祛斑用什么方法", "贴近肌源调肤/淡斑方式短句，不写说明书"),
    ("项目咨询", "可以去痣吗 去痣要多少钱", "当前只承接淡斑前端，不应乱报价去痣"),
    ("效果次数", "一次做好吗", "说明大部分客户一次反馈好，因人而异，到店检测"),
    ("效果次数", "要做多少次", "说明大部分客户一次反馈好，不承诺一次一定好"),
    ("安全顾虑", "会伤害皮肤吗", "给温和/检测/老师操作安心感，不绝对承诺"),
    ("价格确认", "确定268吗", "直接确认周年庆活动268"),
    ("价格确认", "是一次的费用吗", "说明单次体验、10元预约金、到店做付258、不做退还10"),
    ("价格误差", "看广告是58元，是真的吗", "正面否定58，回到周年庆268"),
    ("价格误差", "我看到是199的，你怎么跟我说268", "解释当前周年庆活动价268，不编308/其他活动"),
    ("定金规则", "为什么要交10元定金", "说明锁名额/抵扣10/不做退还10"),
    ("定金异议", "我不交定金到店再付全款", "解释线上报名锁活动名额，尽量推进预约"),
    ("付款时机", "是做完付款吗", "说明到店检测认可后做付尾款"),
    ("活动内容", "268都包含什么", "说明操作斑点、检测皮肤、基础清洁、肌肤补水"),
    ("活动名额", "这个活动什么时候结束，还有名额吗", "说明限30名/名额满恢复原价，不编其他活动名"),
    ("砍价", "还能不能再便宜一点", "守住周年庆268，必要时提申请小气泡管理但不默认承诺"),
    ("费用顾虑", "到店会不会乱收费", "客户主动问时讲费用透明、提前说清楚、认可再做"),
    ("接送车费", "有车费报销吗 可以包接送吗", "明确没有接送/车费自理，不出现包接送承诺"),
]

FORBIDDEN_GLOBAL = [
    "焕新体验季",
    "新客专属活动",
    "老带新专属活动",
    "内部活动",
    "大型活动",
    "公司统一通知价",
    "包接送",
    "车费报销",
    "100%",
    "根治",
    "绝对安全",
    "保证效果",
    "S10",
]


def call_case(idx: int, stage: str, question: str) -> dict[str, Any]:
    msgid = f"codex_project_price_activity_{int(time.time() * 1000)}_{idx}"
    cid = f"codex_price_activity_{idx}_{int(time.time() * 1000)}"
    payload = {
        "workflow_id": "xiaobei-default",
        "parameters": {
            "category_id": "居家产品",
            "content": {
                "content": question,
                "msgid": msgid,
                "msgtime": int(time.time() * 1000),
                "msgtype": "text",
            },
            "customer_id": cid,
            "external_userid": cid,
            "user_id": "7294",
            "wechat": "CS001",
            "corp_id": "ww943af61cd5d2afe4",
            "messages": [
                {
                    "content": question,
                    "direction": "customer",
                    "msgid": msgid,
                    "msgtime": int(time.time() * 1000),
                    "msgtype": "text",
                    "sender_id": cid,
                    "sender_name": "测试客户",
                }
            ],
        },
    }
    request = urllib.request.Request(
        URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            **({"Authorization": f"Bearer {KEY}"} if KEY else {}),
        },
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            body = json.loads(response.read().decode("utf-8"))
            data = body.get("data") if isinstance(body.get("data"), dict) else body
            replies: list[str] = []
            for item in data.get("reply_messages") or []:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if item.get("type") == "text" and isinstance(content, dict):
                    text = str(content.get("text") or "").strip()
                    if text:
                        replies.append(text)
            return {
                "idx": idx,
                "stage": stage,
                "question": question,
                "status": response.status,
                "seconds": round(time.perf_counter() - start, 1),
                "trace_id": body.get("trace_id") or data.get("trace_id") or data.get("execute_id") or "",
                "code": body.get("code"),
                "msg": body.get("msg"),
                "reply1": replies[0] if replies else "",
                "reply2": replies[1] if len(replies) > 1 else "",
                "raw": body,
            }
    except urllib.error.HTTPError as exc:
        return {
            "idx": idx,
            "stage": stage,
            "question": question,
            "status": exc.code,
            "seconds": round(time.perf_counter() - start, 1),
            "trace_id": "",
            "code": "HTTP_ERROR",
            "msg": exc.read().decode("utf-8", "replace")[:500],
            "reply1": "",
            "reply2": "",
            "raw": {},
        }
    except Exception as exc:
        return {
            "idx": idx,
            "stage": stage,
            "question": question,
            "status": 0,
            "seconds": round(time.perf_counter() - start, 1),
            "trace_id": "",
            "code": type(exc).__name__,
            "msg": str(exc),
            "reply1": "",
            "reply2": "",
            "raw": {},
        }


def judge(row: dict[str, Any]) -> str:
    text = (row["reply1"] + "\n" + row["reply2"]).strip()
    issues: list[str] = []
    if row["status"] != 200 or row["code"] not in (0, "0", None):
        issues.append("接口异常")
    if not text:
        issues.append("无客户可见回复")
    hits = [word for word in FORBIDDEN_GLOBAL if word in text]
    if row["stage"] == "接送车费":
        hits = [word for word in hits if word not in ("包接送", "车费报销")]
    if hits:
        issues.append("命中禁用/异常词：" + "、".join(hits))
    if row["stage"] in ("价格确认", "价格误差", "活动内容", "活动名额") and "周年庆" not in text and "268" not in text:
        issues.append("未回到周年庆/268主规则")
    if row["stage"] == "定金规则" and not all(word in text for word in ("10", "258")):
        issues.append("预约金/尾款规则不完整")
    if row["stage"] == "项目咨询" and "去痣" in row["question"] and any(num in text for num in ("30", "50")) and "痣" in text:
        issues.append("疑似对非淡斑项目直接报价")
    if len(row["reply1"]) > 120:
        issues.append("首条偏长")
    return "通过" if not issues else "；".join(issues)


def cell(value: Any) -> str:
    return str(value or "").replace("|", "/").replace("\n", "<br>")


def main() -> None:
    results: list[dict[str, Any]] = []
    for idx, (stage, question, expectation) in enumerate(CASES, 1):
        row = call_case(idx, stage, question)
        row["expectation"] = expectation
        row["judgement"] = judge(row)
        results.append(row)
        print(json.dumps({k: row[k] for k in ["idx", "stage", "question", "status", "seconds", "trace_id", "reply1", "reply2", "judgement"]}, ensure_ascii=False))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"project_price_activity_online_report_{stamp}.json"
    md_path = OUT_DIR / f"project_price_activity_online_report_{stamp}.md"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json_path.write_text(json.dumps({"generated_at": generated_at, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for row in results if row["judgement"] == "通过")
    lines = [
        "# 项目/价格/活动场景线上测试报告",
        "",
        f"- 测试时间：{generated_at}",
        f"- 测试接口：`{URL}`",
        f"- 测试总数：{len(results)}",
        f"- 通过：{passed}",
        f"- 需优化：{len(results) - passed}",
        "",
        "| 序号 | 场景 | 用户问题 | 预期重点 | AI实际回复（第1条） | AI引导回复（第2条） | 耗时 | 日志ID | 评判 |",
        "| --- | --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in results:
        lines.append(
            f"| {row['idx']} | {cell(row['stage'])} | {cell(row['question'])} | {cell(row['expectation'])} | "
            f"{cell(row['reply1'])} | {cell(row['reply2'])} | {row['seconds']}s | `{cell(row['trace_id'])}` | {cell(row['judgement'])} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        "本轮走线上接口完成项目、价格、活动相关场景验证。评判只做基础业务规则检查，重点关注是否回到周年庆活动价、是否乱编活动名、是否承诺接送/报销、是否对非当前承接项目乱报价。",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print("REPORT_MD=" + str(md_path.resolve()))
    print("REPORT_JSON=" + str(json_path.resolve()))


if __name__ == "__main__":
    main()

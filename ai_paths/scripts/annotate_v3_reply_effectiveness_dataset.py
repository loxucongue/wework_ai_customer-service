"""Create non-gold effectiveness briefs for human review; no runtime decisions."""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from app.config import Settings
from app.services.model_client import ModelClient

PROMPT = """你是销售质检助理，只整理评测标准，不改写客户回复。输入是已脱敏的历史多轮对话和当时AI回复。
为每个case输出：category只能是询价/效果疑虑/嫌贵/嫌远/考虑一下/预约付款/售后之一；current_need概括客户此刻要解决什么；
good_sales_behavior说明好销售应回应什么、是否推进及合理范围；observed_reply_issue说明当时回复具体不足，没有明显问题写none；
observed_quality只能good/mixed/poor；fact_uncertain表示历史事实不足。允许多种表达，不写唯一标准答案，不复述客户原句或身份。
输出严格JSON：{"items":[{"case_id":"","category":"","current_need":"","good_sales_behavior":"","observed_reply_issue":"","observed_quality":"good|mixed|poor","fact_uncertain":false}]}"""


async def main(args: argparse.Namespace) -> int:
    rows=json.loads(args.dataset.read_text(encoding="utf-8"))
    settings=Settings(_env_file=args.env_file, AI_PATHS_SERVICE_ROLE="control",
                      SOP_PLATFORM_PULL_ENABLED=False, AI_PATHS_BACKGROUND_WORKERS_ENABLED=False)
    client=ModelClient(settings)
    annotations=[]
    failures=[]
    try:
        for offset in range(0,len(rows),args.batch_size):
            batch=rows[offset:offset+args.batch_size]
            payload=[{"case_id":r["case_id"],"current":r["current"],"history":r["history"],
                      "observed_reply":r["observed_reply"],
                      "fact_boundary":r["context"][:16000]} for r in batch]
            try:
                value=await client.chat_json(
                    [{"role":"system","content":PROMPT},{"role":"user","content":json.dumps(payload,ensure_ascii=False)}],
                    tier="reply",temperature=0,max_parallel_candidates=1,
                    model_names_override=[settings.model_reply],api_key_override=settings.deepseek_api_key,
                    base_url_override=settings.deepseek_openai_base_url,
                )
                items=value.get("items") if isinstance(value,dict) else None
                if not isinstance(items,list):
                    raise ValueError("missing items")
                expected={r["case_id"] for r in batch}
                actual={str(item.get("case_id") or "") for item in items if isinstance(item,dict)}
                if actual != expected:
                    raise ValueError("case ids mismatch")
                annotations.extend(items)
            except Exception as exc:
                failures.append({"offset":offset,"error":f"{type(exc).__name__}:{exc}"[:200]})
    finally:
        await client.aclose()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"business_gold":False,"sealed":args.sealed,
        "annotations":annotations,"failures":failures},ensure_ascii=False,indent=2),encoding="utf-8")
    categories=Counter(str(item.get("category") or "") for item in annotations)
    qualities=Counter(str(item.get("observed_quality") or "") for item in annotations)
    print(json.dumps({"case_count":len(rows),"annotated":len(annotations),"failures":len(failures),
                      "categories":categories,"qualities":qualities,"sealed":args.sealed},ensure_ascii=False))
    return 0 if len(annotations)==len(rows) and not failures else 2


def parser() -> argparse.ArgumentParser:
    value=argparse.ArgumentParser()
    value.add_argument("--dataset",type=Path,required=True)
    value.add_argument("--env-file",type=Path,required=True)
    value.add_argument("--output",type=Path,required=True)
    value.add_argument("--batch-size",type=int,default=5)
    value.add_argument("--sealed",action="store_true")
    return value


if __name__=="__main__":
    raise SystemExit(asyncio.run(main(parser().parse_args())))

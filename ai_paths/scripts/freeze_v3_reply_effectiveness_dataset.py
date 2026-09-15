"""Freeze a reviewed category-balanced effectiveness dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.v3_reply_effectiveness_dataset import stratified_split_cases

DEVELOPMENT_QUOTAS = {"询价": 9, "效果疑虑": 8, "嫌贵": 4, "嫌远": 8, "考虑一下": 8, "预约付款": 9, "售后": 4}
HOLDOUT_QUOTAS = {"询价": 5, "效果疑虑": 5, "嫌贵": 3, "嫌远": 5, "考虑一下": 4, "预约付款": 6, "售后": 2}


def main(args: argparse.Namespace) -> int:
    rows=json.loads(args.candidates.read_text(encoding="utf-8"))
    annotated=json.loads(args.annotations.read_text(encoding="utf-8"))
    result=stratified_split_cases(rows,annotated["annotations"],
                                  development_quotas=DEVELOPMENT_QUOTAS,holdout_quotas=HOLDOUT_QUOTAS)
    args.output.mkdir(parents=True,exist_ok=True)
    for name in ("development","holdout","manifest"):
        (args.output/f"{name}.json").write_text(json.dumps(result[name],ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result["manifest"],ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    value=argparse.ArgumentParser()
    value.add_argument("--candidates",type=Path,required=True)
    value.add_argument("--annotations",type=Path,required=True)
    value.add_argument("--output",type=Path,required=True)
    return value


if __name__=="__main__":
    raise SystemExit(main(parser().parse_args()))

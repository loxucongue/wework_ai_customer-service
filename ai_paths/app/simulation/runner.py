from __future__ import annotations

import asyncio
import json
import re
import statistics
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.policies.business_rules import reply_business_rules_for_model
from app.services.model_client import ModelClient
from app.simulation.runtime import SimulationRuntime


SEMANTIC_SCORE_KEYS = (
    "current_question",
    "history_continuity",
    "mainline_progression",
    "conversion_naturalness",
    "human_tone",
    "fact_safety",
)


def load_suite(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    scenarios = [_deep_merge(defaults, item) for item in payload.get("scenarios") or [] if isinstance(item, dict)]
    for template in payload.get("templates") or []:
        if not isinstance(template, dict):
            continue
        variants = template.get("variants") if isinstance(template.get("variants"), list) else []
        for index, variant in enumerate(variants, start=1):
            scenario = _deep_merge(
                defaults,
                {key: value for key, value in template.items() if key not in {"variants", "followup"}},
            )
            scenario["id"] = f"{template.get('id')}__v{index:02d}"
            patch = variant if isinstance(variant, dict) else {"content": str(variant)}
            _apply_variant(scenario, patch)
            followup = template.get("followup")
            if followup:
                followup_step = deepcopy(followup) if isinstance(followup, dict) else {
                    "kind": "customer_message",
                    "content": str(followup),
                }
                scenario.setdefault("timeline", []).append(followup_step)
            scenarios.append(scenario)
    return scenarios


async def run_suite(
    *,
    repo_root: Path,
    fixture: Path,
    output_dir: Path,
    scenario_id: str = "",
    category: str = "",
    attempts: int = 3,
    critical_attempts: int = 5,
    concurrency: int = 2,
    max_cases: int = 0,
    reviewer_model: str = "",
    skip_review: bool = False,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    scenarios = load_suite(fixture)
    if scenario_id:
        requested_ids = {item.strip() for item in scenario_id.split(",") if item.strip()}
        scenarios = [item for item in scenarios if str(item.get("id") or "") in requested_ids]
    if category:
        scenarios = [item for item in scenarios if str(item.get("category") or "") == category]
    if max_cases > 0:
        scenarios = scenarios[:max_cases]
    if not scenarios:
        raise ValueError("no simulation scenarios matched the filters")

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    runtime = SimulationRuntime(repo_root=repo_root, run_root=output_dir / "runs", base_settings=settings)
    reviewer = None if skip_review else ModelClient(
        settings.model_copy(
            update={
                "model_balanced": reviewer_model or settings.model_reply,
                "model_balanced_fallbacks": settings.model_reply_fallbacks,
            }
        )
    )
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 2)))
    jobs: list[tuple[dict[str, Any], int]] = []
    for scenario in scenarios:
        count = critical_attempts if bool(scenario.get("critical")) else attempts
        jobs.extend((scenario, attempt) for attempt in range(1, max(1, count) + 1))

    async def run_one(scenario: dict[str, Any], attempt: int) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await runtime.run_scenario(scenario, attempt=attempt)
            except Exception as exc:  # noqa: BLE001 - report fixture/provider failures separately
                result = {
                    "scenario_id": scenario.get("id"),
                    "category": scenario.get("category"),
                    "critical": bool(scenario.get("critical")),
                    "attempt": attempt,
                    "hard_pass": False,
                    "hard_errors": [],
                    "infrastructure_errors": [f"{type(exc).__name__}: {exc}"],
                    "runner_error": f"{type(exc).__name__}: {exc}",
                }
                _write_result_checkpoint(checkpoint_dir, result)
                return result
            if reviewer and result.get("hard_pass") and not result.get("infrastructure_errors"):
                try:
                    result["semantic_review"] = await _review_result(reviewer, scenario, result)
                except Exception as exc:  # noqa: BLE001
                    result["semantic_review"] = {
                        "available": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            _write_result_checkpoint(checkpoint_dir, result)
            return result

    results = list(await asyncio.gather(*(run_one(scenario, attempt) for scenario, attempt in jobs)))
    if reviewer:
        await reviewer.aclose()
    report = _aggregate(
        fixture=fixture,
        scenarios=scenarios,
        results=results,
        baseline=_load_baseline(baseline_path),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def _write_result_checkpoint(checkpoint_dir: Path, result: dict[str, Any]) -> None:
    scenario_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(result.get("scenario_id") or "unknown"))
    attempt = int(result.get("attempt") or 0)
    target = checkpoint_dir / f"{scenario_id}-a{attempt}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


async def _review_result(
    reviewer: ModelClient,
    scenario: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    visible = _visible_transcript(result)
    prompt = {
        "scenario_id": scenario.get("id"),
        "category": scenario.get("category"),
        "semantic_goal": scenario.get("semantic_goal"),
        "expected": scenario.get("expected"),
        "authoritative_simulation_facts": scenario.get("initial"),
        "authoritative_scenario_timeline": scenario.get("timeline"),
        "authoritative_business_facts": reply_business_rules_for_model(),
        "authoritative_configured_sop_media_urls": _configured_sop_media_urls(),
        "conversation_and_replies": visible,
        "rubric": {
            key: "integer 1-5; 4 means production-acceptable, 5 means excellent"
            for key in SEMANTIC_SCORE_KEYS
        },
    }
    output = await reviewer.chat_json(
        [
            {
                "role": "system",
                "content": (
                    "You are an independent Chinese customer-service quality reviewer. "
                    "Return valid json only. Judge the actual reply, not keyword presence. "
                    "Treat authoritative_simulation_facts and authoritative_business_facts as true configured facts: "
                     "simulation-only URLs and IDs are valid when supplied there. Do not invent missing context or reject "
                     "configured business facts merely because they are not independently verifiable on the public internet. "
                     "Every URL in authoritative_configured_sop_media_urls is a configured SOP media asset and may be described "
                     "as a configured case/activity reference. This media authorization does not make unrelated text claims true; "
                     "continue checking all text against authoritative_business_facts. "
                    "Use these configured sales rules while scoring: a complete visible set of 1-3 stores should be sent as "
                    "cards; after the customer chooses one, asking spot history or returning to cases/activity is valid. "
                    "When 2-3 real store cards were sent and the customer has not selected one, asking which store or district "
                    "is more convenient remains a necessary unresolved-store action and must not lower mainline progression. "
                    "Store IDs required by expected.required_store_ids/required_any_store_ids and store candidates returned by "
                    "the simulated tool are authoritative visible-scope facts. Do not override them with geographic intuition "
                    "or reject a configured parent-city/province fallback merely because it is outside the customer's county. "
                    "A real store card followed by one short spot-history question is the configured mainline progression, "
                    "not a premature topic change. When a county, town, village, or landmark resolves to one visible store, "
                    "sending that real store card and then asking spot history is sufficient; do not require extra explanation "
                    "of administrative hierarchy or continued store selection unless the customer asks. "
                    "After an effect answer and configured case images, the closed question asking whether the customer came "
                    "from the online activity is an approved bridge into the next sales stage and must not be treated as an "
                    "unrelated or missing mainline action. When the customer asks whether the just-sent store has the same "
                    "activity and the activity introduction was not previously completed, directly confirming that it is the "
                    "same activity and then sending the complete activity SOP is correct; do not require staying in the store "
                    "stage or penalize the full configured pack merely because the customer's question was short. A response "
                    "whose first clause says '是的，这家也是同一个...活动' has directly answered that confirmation question; "
                    "do not claim it failed to confirm merely because the remaining configured SOP is detailed. "
                    "The configured statement that most customers report visible improvement "
                    "is an approved business fact and must not be rejected as publicly unverifiable, described as too strong, "
                    "or used to lower fact_safety or any other score when the reply contains no absolute guarantee. Do not "
                    "recommend weakening this approved confidence statement. "
                    "Every non-risk, non-terminal reply should end with one natural unfinished-mainline action. After directly "
                    "answering whether a supported body area can be treated or its configured price, asking the customer's city "
                    "or district to match a store is a valid and expected sales progression when no store is known; do not call "
                    "that action premature merely because the customer did not explicitly ask for a store. However, if the "
                    "customer only confirms that the face can also be treated, proactively introducing same-session or combined-"
                    "price boundaries is unnecessary unless the customer explicitly asks about doing both together or one total price. "
                    "After the activity quote is complete, a payment card may be sent without an existing order and may be "
                    "resent after a new payment/registration signal. Do not penalize that as repetition by itself. "
                    "When the customer explicitly asks how to participate, register, reserve, or pay after the activity quote was "
                    "already completed in prior visible history, sending one payment card is the expected fast conversion path. "
                    "If the prior history has not completed the activity quote, a full same-turn activity introduction without a "
                    "payment card is valid and must not be penalized; the card can follow after that introduction is completed. "
                    "That first complete activity introduction is itself the turn's main progression and must not include a payment "
                    "card, but its final text should still leave one natural single action such as confirming party size or continuing "
                    "registration; it must not simply stop after the activity image. "
                    "You may still lower tone or continuity scores for genuinely repetitive, overly formal, or poorly connected wording. "
                    "The current customer turn has priority over an unanswered slot from the immediately preceding turn. If the "
                    "assistant asked for city or another slot and the customer instead asks about effects, price, or another valid "
                    "mainline topic, answering that new topic and moving to its adjacent mainline action is correct; do not require "
                    "the assistant to repeat the ignored slot in the same turn. "
                    "When the latest message asks how to reserve, register, or keep a slot, do not require the reply to revisit an "
                    "earlier distance comment. If activity facts were not previously completed, directly answering the 10-yuan "
                    "reservation action first, then sending the configured full activity introduction and ending with one party-size "
                    "or registration action is the expected response; do not penalize that configured first introduction as excessive. "
                    "Review every assistant reply against the immediately preceding customer turn; never attribute an earlier "
                    "reply to a later customer message. Choosing transfer may be answered with the 10-yuan amount and a request "
                    "for a success screenshot; after the customer says it was transferred, requesting the screenshot for "
                    "verification is correct and must not be described as renewed payment collection. "
                    "For active health risk, require a pause and in-store assessment before operation, but do not require "
                    "online diagnosis, symptom interrogation, or medical-care instructions beyond configured boundaries. "
                    "Evaluate the full initial history plus conversation_and_replies. Separate infrastructure absence "
                    "from business quality. Output a scores object containing all six rubric keys, plus pass, reasons, "
                    "and critical_errors."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        tier="balanced",
        temperature=0,
    )
    scores = _semantic_scores(output)
    normalized = {key: _score(scores.get(key)) for key in SEMANTIC_SCORE_KEYS}
    critical_errors = [str(item) for item in output.get("critical_errors") or [] if str(item)]
    passed = all(value >= 4 for value in normalized.values()) and not critical_errors
    return {
        "available": True,
        "pass": passed,
        "scores": normalized,
        "average": round(statistics.mean(normalized.values()), 2),
        "critical_errors": critical_errors,
        "reasons": output.get("reasons") or output.get("reason") or "",
        "raw": output,
    }


def _semantic_scores(output: dict[str, Any]) -> dict[str, Any]:
    nested = output.get("scores")
    if isinstance(nested, dict):
        return nested
    return {key: output.get(key) for key in SEMANTIC_SCORE_KEYS}


def _aggregate(
    *,
    fixture: Path,
    scenarios: list[dict[str, Any]],
    results: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    evaluable = [
        item
        for item in results
        if not item.get("infrastructure_errors")
        and isinstance(item.get("semantic_review"), dict)
        and item["semantic_review"].get("available")
    ]
    hard_passed = [item for item in results if item.get("hard_pass")]
    semantic_passed = [item for item in evaluable if item["semantic_review"].get("pass")]
    durations = [int(item.get("duration_ms") or 0) for item in results if item.get("duration_ms")]
    scenario_summary: dict[str, Any] = {}
    for scenario in scenarios:
        scenario_id = str(scenario.get("id") or "")
        attempts = [item for item in results if str(item.get("scenario_id") or "") == scenario_id]
        scenario_summary[scenario_id] = {
            "category": scenario.get("category"),
            "critical": bool(scenario.get("critical")),
            "attempts": len(attempts),
            "hard_passes": sum(1 for item in attempts if item.get("hard_pass")),
            "semantic_passes": sum(
                1
                for item in attempts
                if isinstance(item.get("semantic_review"), dict) and item["semantic_review"].get("pass")
            ),
            "infrastructure_failures": sum(1 for item in attempts if item.get("infrastructure_errors")),
        }
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "fixture": str(fixture),
        "scenario_count": len(scenarios),
        "attempt_count": len(results),
        "summary": {
            "hard_pass_rate": _rate(len(hard_passed), len(results)),
            "semantic_pass_rate": _rate(len(semantic_passed), len(evaluable)),
            "evaluable_attempts": len(evaluable),
            "infrastructure_failures": sum(1 for item in results if item.get("infrastructure_errors")),
            "p50_ms": _percentile(durations, 0.5),
            "p90_ms": _percentile(durations, 0.9),
            "acceptance": {
                "hard_errors_zero": len(hard_passed) == len(results),
                "semantic_at_least_90": _rate(len(semantic_passed), len(evaluable), numeric=True) >= 90 if evaluable else False,
                "critical_all_pass": all(
                    value["hard_passes"] == value["attempts"] and value["semantic_passes"] == value["attempts"]
                    for value in scenario_summary.values()
                    if value["critical"]
                ),
            },
        },
        "scenario_summary": scenario_summary,
        "baseline_comparison": _compare_baseline(baseline, scenario_summary),
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# 离线全链路仿真报告",
        "",
        f"- 场景数：{report.get('scenario_count', 0)}",
        f"- 总运行次数：{report.get('attempt_count', 0)}",
        f"- 硬校验通过率：{summary.get('hard_pass_rate', '0%')}",
        f"- 可评估语义通过率：{summary.get('semantic_pass_rate', '0%')}",
        f"- 基础设施失败：{summary.get('infrastructure_failures', 0)}",
        f"- P50 / P90：{summary.get('p50_ms', 0)}ms / {summary.get('p90_ms', 0)}ms",
        "",
        "## 场景结果",
        "",
        "| 场景 | 分类 | 关键 | 硬通过 | 语义通过 | 基础设施失败 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for scenario_id, item in (report.get("scenario_summary") or {}).items():
        lines.append(
            f"| {scenario_id} | {item.get('category', '')} | {'是' if item.get('critical') else '否'} | "
            f"{item.get('hard_passes', 0)}/{item.get('attempts', 0)} | "
            f"{item.get('semantic_passes', 0)}/{item.get('attempts', 0)} | "
            f"{item.get('infrastructure_failures', 0)} |"
        )
    lines.extend(["", "## 失败详情", ""])
    failures = [
        item
        for item in report.get("results") or []
        if not item.get("hard_pass")
        or item.get("infrastructure_errors")
        or (isinstance(item.get("semantic_review"), dict) and item["semantic_review"].get("available") and not item["semantic_review"].get("pass"))
    ]
    if not failures:
        lines.append("无。")
    for item in failures:
        lines.extend(
            [
                f"### {item.get('scenario_id')} / attempt {item.get('attempt')}",
                "",
                f"- 硬错误：{item.get('hard_errors') or []}",
                f"- 基础设施错误：{item.get('infrastructure_errors') or []}",
                f"- 语义评审：{(item.get('semantic_review') or {}).get('reasons', '')}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _apply_variant(scenario: dict[str, Any], patch: dict[str, Any]) -> None:
    step_index = int(patch.pop("step_index", 0))
    timeline = scenario.get("timeline") if isinstance(scenario.get("timeline"), list) else []
    if timeline and 0 <= step_index < len(timeline):
        for key, value in patch.items():
            if key in {"category", "critical", "semantic_goal", "expected", "initial"}:
                if key in {"expected", "initial"} and isinstance(value, dict) and isinstance(scenario.get(key), dict):
                    scenario[key] = _deep_merge(scenario[key], value)
                else:
                    scenario[key] = deepcopy(value)
                continue
            timeline[step_index][key] = deepcopy(value)
    else:
        scenario.update(deepcopy(patch))


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(output.get(key), dict):
            output[key] = _deep_merge(output[key], value)
        else:
            output[key] = deepcopy(value)
    return output


def _visible_transcript(result: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for step in result.get("steps") or []:
        if not isinstance(step, dict):
            continue
        item = {"kind": step.get("kind"), "input": (step.get("input") or {}).get("content", "")}
        item["sync_reply_messages"] = step.get("sync_reply_messages") or []
        item["outbox"] = step.get("new_outbox") or []
        output.append(item)
    return output


def _configured_sop_media_urls() -> list[str]:
    path = Path(__file__).resolve().parents[3] / "config" / "sop_reply_packs.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    urls: list[str] = []
    for pack in payload.get("packs") or []:
        if not isinstance(pack, dict):
            continue
        for message in pack.get("reply_messages") or []:
            if not isinstance(message, dict) or str(message.get("type") or "") not in {"image", "video"}:
                continue
            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            url = str(content.get("url") or "").strip()
            if url and url not in urls:
                urls.append(url)
    return urls


def _score(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def _rate(numerator: int, denominator: int, *, numeric: bool = False) -> str | float:
    value = round((numerator / denominator * 100), 1) if denominator else 0.0
    return value if numeric else f"{value}%"


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _load_baseline(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _compare_baseline(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous = baseline.get("scenario_summary") if isinstance(baseline.get("scenario_summary"), dict) else {}
    if not previous:
        return {"available": False}
    improved: list[str] = []
    regressed: list[str] = []
    unchanged: list[str] = []
    for scenario_id, item in current.items():
        old = previous.get(scenario_id)
        if not isinstance(old, dict):
            continue
        current_rate = _rate(int(item.get("semantic_passes") or 0), int(item.get("attempts") or 0), numeric=True)
        old_rate = _rate(int(old.get("semantic_passes") or 0), int(old.get("attempts") or 0), numeric=True)
        if current_rate > old_rate:
            improved.append(scenario_id)
        elif current_rate < old_rate:
            regressed.append(scenario_id)
        else:
            unchanged.append(scenario_id)
    return {"available": True, "improved": improved, "regressed": regressed, "unchanged": unchanged}

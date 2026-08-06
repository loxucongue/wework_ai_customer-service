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

REQUIRED_SIMULATION_CATEGORIES = (
    "门店V2",
    "SOP主线",
    "效果案例",
    "精准问答",
    "项目范围",
    "健康风险",
    "门店匹配",
    "门店工具",
    "门店异议",
    "SOP Gate",
    "预约金",
    "已付登记",
    "客户异议",
    "明确拒绝",
    "SOP Event",
    "消息归一",
    "模型恢复",
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
    base_settings: Settings | None = None,
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
    scope = simulation_evaluation_scope(
        scenario_id=scenario_id,
        category=category,
        max_cases=max_cases,
    )
    options = simulation_run_options(
        attempts=attempts,
        critical_attempts=critical_attempts,
        concurrency=concurrency,
        skip_review=skip_review,
        reviewer_model=reviewer_model,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    settings = base_settings or Settings()
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
                    _record_semantic_review_failure(result, exc)
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
        evaluation_scope=scope,
        run_options=options,
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


def _record_semantic_review_failure(result: dict[str, Any], exc: Exception) -> None:
    review_error = f"{type(exc).__name__}: {exc}"
    result["semantic_review"] = {
        "available": False,
        "error": review_error,
    }
    result.setdefault("infrastructure_errors", []).append(f"semantic_review:{review_error}")


def simulation_evaluation_scope(*, scenario_id: str = "", category: str = "", max_cases: int = 0) -> dict[str, Any]:
    scenario_filter = str(scenario_id or "").strip()
    category_filter = str(category or "").strip()
    case_limit = int(max_cases or 0)
    targeted = bool(scenario_filter or category_filter or case_limit > 0)
    return {
        "schema_version": "offline_simulation_scope_v1",
        "scenario_id": scenario_filter,
        "category": category_filter,
        "max_cases": case_limit,
        "targeted_smoke": targeted,
        "full_release_gate_candidate": not targeted,
    }


def simulation_run_options(
    *,
    attempts: int = 3,
    critical_attempts: int = 5,
    concurrency: int = 2,
    skip_review: bool = False,
    reviewer_model: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": "offline_simulation_run_options_v1",
        "attempts": int(attempts or 0),
        "critical_attempts": int(critical_attempts or 0),
        "concurrency": int(concurrency or 0),
        "skip_review": bool(skip_review),
        "reviewer_model": str(reviewer_model or ""),
    }


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
                    "Treat platform_task message_content in authoritative_scenario_timeline as an exact passthrough payload: "
                    "its text and media are authoritative for that event even when they are not ordinary campaign facts. "
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
    evaluation_scope: dict[str, Any] | None = None,
    run_options: dict[str, Any] | None = None,
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
    hard_error_count = sum(
        1
        for item in results
        if not item.get("hard_pass") or bool(item.get("infrastructure_errors"))
    )
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
    failed_critical_scenarios = [
        scenario_id
        for scenario_id, value in scenario_summary.items()
        if value["critical"]
        and (
            value["attempts"] <= 0
            or value["hard_passes"] != value["attempts"]
            or value["semantic_passes"] != value["attempts"]
            or value["infrastructure_failures"] > 0
        )
    ]
    semantic_pass_rate = _ratio(len(semantic_passed), len(evaluable))
    semantic_pass_rate_percent = _rate(len(semantic_passed), len(evaluable))
    safety = _simulation_safety(results)
    isolation_audit = _simulation_isolation_audit(results)
    git_commits = sorted(
        {
            str(item.get("git_commit") or "").strip()
            for item in results
            if isinstance(item, dict) and str(item.get("git_commit") or "").strip()
        }
    )
    infrastructure_failures = sum(1 for item in results if item.get("infrastructure_errors"))
    coverage = _coverage_audit(scenarios)
    return {
        "schema_version": "offline_reply_chain_simulation_report_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": git_commits[0] if len(git_commits) == 1 else "",
        "git_commit_set": git_commits,
        "fixture": str(fixture),
        "evaluation_scope": evaluation_scope or simulation_evaluation_scope(),
        "run_options": run_options or simulation_run_options(),
        "scenario_count": len(scenarios),
        "attempt_count": len(results),
        "hard_error_count": hard_error_count,
        "semantic_pass_rate": semantic_pass_rate,
        "failed_critical_scenarios": failed_critical_scenarios,
        "safety": safety,
        "isolation_audit": isolation_audit,
        "summary": {
            "hard_pass_rate": _rate(len(hard_passed), len(results)),
            "semantic_pass_rate": semantic_pass_rate_percent,
            "evaluable_attempts": len(evaluable),
            "infrastructure_failures": infrastructure_failures,
            "p50_ms": _percentile(durations, 0.5),
            "p90_ms": _percentile(durations, 0.9),
            "acceptance": {
                "hard_errors_zero": hard_error_count == 0,
                "semantic_review_complete": len(evaluable) == len(results),
                "semantic_at_least_90": semantic_pass_rate >= 0.9,
                "critical_all_pass": not failed_critical_scenarios,
                "infrastructure_failures_zero": infrastructure_failures == 0,
                "scenario_coverage_complete": (
                    not coverage["missing_required_categories"]
                    and not coverage["missing_critical_required_categories"]
                ),
                "isolation_audit_passed": isolation_audit["passed"],
            },
        },
        "coverage": coverage,
        "scenario_summary": scenario_summary,
        "effect_review": _effect_review(results),
        "review_artifacts": _review_artifacts(results),
        "baseline_comparison": _compare_baseline(baseline, scenario_summary),
        "results": results,
    }


def _coverage_audit(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    critical_category_counts: dict[str, int] = {}
    for scenario in scenarios:
        category = str(scenario.get("category") or "").strip()
        if not category:
            category = "<missing>"
        category_counts[category] = category_counts.get(category, 0) + 1
        if bool(scenario.get("critical")):
            critical_category_counts[category] = critical_category_counts.get(category, 0) + 1
    missing = [category for category in REQUIRED_SIMULATION_CATEGORIES if category_counts.get(category, 0) <= 0]
    missing_critical = [
        category for category in REQUIRED_SIMULATION_CATEGORIES if critical_category_counts.get(category, 0) <= 0
    ]
    return {
        "schema_version": "offline_simulation_coverage_audit_v1",
        "required_categories": list(REQUIRED_SIMULATION_CATEGORIES),
        "missing_required_categories": missing,
        "missing_critical_required_categories": missing_critical,
        "category_counts": dict(sorted(category_counts.items())),
        "critical_category_counts": dict(sorted(critical_category_counts.items())),
    }


def _simulation_safety(results: list[dict[str, Any]]) -> dict[str, Any]:
    outbox_items = [
        item
        for result in results
        for item in result.get("outbox") or []
        if isinstance(item, dict)
    ]
    write_items = [
        item
        for result in results
        for item in result.get("simulated_platform_writes") or []
        if isinstance(item, dict)
    ]
    non_virtual_outbox = [
        item
        for item in outbox_items
        if str(item.get("transport") or "") != "simulation_outbox"
    ]
    non_simulation_writes = [
        item
        for item in write_items
        if str(item.get("transport") or "") != "simulation_only"
    ]
    return {
        "production_customer_messages_sent": bool(non_virtual_outbox),
        "production_writes_allowed": bool(non_simulation_writes),
        "virtual_outbox_only": not non_virtual_outbox,
        "production_write_count": len(non_simulation_writes),
        "virtual_outbox_message_count": len(outbox_items),
        "simulated_write_count": len(write_items),
    }


def _simulation_isolation_audit(results: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [
        item.get("isolation_audit")
        for item in results
        if isinstance(item, dict) and isinstance(item.get("isolation_audit"), dict)
    ]
    failed = [item for item in audits if item.get("passed") is not True]
    missing_count = max(0, len(results) - len(audits))
    return {
        "schema_version": "offline_simulation_isolation_summary_v1",
        "result_count": len(audits),
        "missing_result_count": missing_count,
        "failed_result_count": len(failed),
        "passed": bool(results) and missing_count == 0 and not failed,
        "run_dirs_under_tmp_simulation": all(item.get("run_dir_under_tmp_simulation") is True for item in audits),
        "paths_within_run_dir": all(item.get("paths_within_run_dir") is True for item in audits),
        "connector_urls_simulation_only": all(item.get("connector_urls_simulation_only") is True for item in audits),
        "adapters_simulation_only": all(item.get("adapters_simulation_only") is True for item in audits),
        "identity_simulation_scoped": all(item.get("identity_simulation_scoped") is True for item in audits),
        "real_connector_credentials_present": any(
            item.get("real_connector_credentials_present") is True for item in audits
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    scope = report.get("evaluation_scope") if isinstance(report.get("evaluation_scope"), dict) else {}
    options = report.get("run_options") if isinstance(report.get("run_options"), dict) else {}
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
        "## 运行范围与选项",
        "",
        f"- 发布门禁候选：{'是' if scope.get('full_release_gate_candidate') is True else '否'}",
        f"- 定向 smoke：{'是' if scope.get('targeted_smoke') else '否'}",
        f"- 场景过滤：{scope.get('scenario_id') or '无'}",
        f"- 分类过滤：{scope.get('category') or '无'}",
        f"- 最大场景限制：{scope.get('max_cases', 0)}",
        f"- 普通场景 attempts：{options.get('attempts', 0)}",
        f"- 关键场景 attempts：{options.get('critical_attempts', 0)}",
        f"- 并发：{options.get('concurrency', 0)}",
        f"- 跳过语义评审：{'是' if options.get('skip_review') else '否'}",
        f"- 评审模型：{options.get('reviewer_model') or '默认'}",
        "",
        "## 场景覆盖",
        "",
    ]
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    missing_categories = coverage.get("missing_required_categories") or []
    missing_critical_categories = coverage.get("missing_critical_required_categories") or []
    if missing_categories:
        lines.append(f"- 缺失必测分类：{', '.join(str(item) for item in missing_categories)}")
    else:
        lines.append("- 必测分类：完整")
    if missing_critical_categories:
        lines.append(f"- 缺失关键场景分类：{', '.join(str(item) for item in missing_critical_categories)}")
    else:
        lines.append("- 关键场景分类：完整")
    lines.extend(["", "| 分类 | 场景数 | 关键场景数 |", "|---|---:|---:|"])
    category_counts = coverage.get("category_counts") if isinstance(coverage.get("category_counts"), dict) else {}
    critical_counts = (
        coverage.get("critical_category_counts") if isinstance(coverage.get("critical_category_counts"), dict) else {}
    )
    for category, count in category_counts.items():
        lines.append(f"| {category} | {count} | {critical_counts.get(category, 0)} |")
    lines.extend(
        [
            "",
            "## 场景结果",
            "",
            "| 场景 | 分类 | 关键 | 硬通过 | 语义通过 | 基础设施失败 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for scenario_id, item in (report.get("scenario_summary") or {}).items():
        lines.append(
            f"| {scenario_id} | {item.get('category', '')} | {'是' if item.get('critical') else '否'} | "
            f"{item.get('hard_passes', 0)}/{item.get('attempts', 0)} | "
            f"{item.get('semantic_passes', 0)}/{item.get('attempts', 0)} | "
            f"{item.get('infrastructure_failures', 0)} |"
        )
    effect = report.get("effect_review") if isinstance(report.get("effect_review"), dict) else {}
    lines.extend(
        [
            "",
            "## 效果审查样本",
            "",
            f"- 问题样本数：{effect.get('issue_count', 0)}",
            f"- 低分样本数：{effect.get('low_score_count', 0)}",
            f"- 硬错误或基础设施样本数：{effect.get('hard_or_infra_count', 0)}",
            "",
        ]
    )
    items = effect.get("items") if isinstance(effect.get("items"), list) else []
    if items:
        lines.extend(
            [
                "| 场景 | attempt | 类型 | 客户输入 | AI回复摘录 | 评审理由 |",
                "|---|---:|---|---|---|---|",
            ]
        )
        for item in items:
            lines.append(
                f"| {item.get('scenario_id', '')} | {item.get('attempt', '')} | "
                f"{', '.join(item.get('issue_types') or [])} | "
                f"{_md_cell(item.get('customer_input_excerpt', ''))} | "
                f"{_md_cell(item.get('assistant_reply_excerpt', ''))} | "
                f"{_md_cell(item.get('review_reasons', ''))} |"
            )
    else:
        lines.append("无。")
    artifacts = report.get("review_artifacts") if isinstance(report.get("review_artifacts"), dict) else {}
    lines.extend(
        [
            "",
            "## 审查证据",
            "",
            f"- 轨迹记录：{artifacts.get('result_count', 0)}",
            f"- 请求记录：{artifacts.get('request_count', 0)}",
            f"- SOP 事件记录：{artifacts.get('event_count', 0)}",
            f"- 工具调用：{artifacts.get('tool_call_count', 0)}",
            f"- 虚拟 outbox 消息批次：{artifacts.get('outbox_batch_count', 0)}",
            f"- 模拟写入：{artifacts.get('simulated_write_count', 0)}",
            "",
            "| 场景 | attempt | requests | events | nodes | tools | sync replies | outbox | writes |",
            "|---|---:|---|---|---|---|---:|---:|---:|",
        ]
    )
    for item in artifacts.get("results") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| {item.get('scenario_id', '')} | {item.get('attempt', '')} | "
            f"{', '.join(item.get('request_ids') or [])} | "
            f"{', '.join(item.get('event_ids') or [])} | "
            f"{', '.join(item.get('node_trace_names') or [])} | "
            f"{', '.join(item.get('tool_call_names') or [])} | "
            f"{item.get('sync_reply_message_count', 0)} | "
            f"{item.get('outbox_batch_count', 0)} | "
            f"{item.get('simulated_write_count', 0)} |"
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


def _effect_review(results: list[dict[str, Any]]) -> dict[str, Any]:
    items = [_effect_review_item(item) for item in results if isinstance(item, dict)]
    issue_items = [item for item in items if item.get("issue_types")]
    low_score_items = [item for item in issue_items if "semantic_low_score" in item.get("issue_types", [])]
    hard_or_infra_items = [
        item
        for item in issue_items
        if "hard_error" in item.get("issue_types", []) or "infrastructure_error" in item.get("issue_types", [])
    ]
    return {
        "schema_version": "offline_simulation_effect_review_v1",
        "result_count": len(items),
        "issue_count": len(issue_items),
        "low_score_count": len(low_score_items),
        "hard_or_infra_count": len(hard_or_infra_items),
        "items": issue_items[:50],
    }


def _effect_review_item(result: dict[str, Any]) -> dict[str, Any]:
    semantic = result.get("semantic_review") if isinstance(result.get("semantic_review"), dict) else {}
    scores = semantic.get("scores") if isinstance(semantic.get("scores"), dict) else {}
    normalized_scores = {key: _score(scores.get(key)) for key in SEMANTIC_SCORE_KEYS if key in scores}
    min_score = min(normalized_scores.values()) if normalized_scores else None
    issue_types: list[str] = []
    if not result.get("hard_pass"):
        issue_types.append("hard_error")
    if result.get("infrastructure_errors"):
        issue_types.append("infrastructure_error")
    if semantic.get("available") and semantic.get("pass") is False:
        issue_types.append("semantic_low_score")
    if semantic.get("critical_errors"):
        issue_types.append("semantic_critical_error")
    visible = _visible_transcript(result)
    customer_inputs = [
        str(item.get("input") or "").strip()
        for item in visible
        if isinstance(item, dict) and str(item.get("input") or "").strip()
    ]
    sync_messages: list[Any] = []
    outbox_messages: list[Any] = []
    for item in visible:
        if not isinstance(item, dict):
            continue
        sync_messages.extend(item.get("sync_reply_messages") or [])
        for batch in item.get("outbox") or []:
            if isinstance(batch, dict):
                outbox_messages.extend(batch.get("reply_messages") or [])
    return _drop_empty(
        {
            "scenario_id": result.get("scenario_id"),
            "attempt": result.get("attempt"),
            "category": result.get("category"),
            "critical": bool(result.get("critical")),
            "issue_types": issue_types,
            "scores": normalized_scores,
            "min_score": min_score,
            "critical_errors": semantic.get("critical_errors") or [],
            "review_reasons": semantic.get("reasons") or "",
            "customer_input_excerpt": _join_excerpt(customer_inputs),
            "assistant_reply_excerpt": _join_excerpt(
                [_message_excerpt(message) for message in [*sync_messages, *outbox_messages]]
            ),
            "hard_errors": result.get("hard_errors") or [],
            "infrastructure_errors": result.get("infrastructure_errors") or [],
        }
    )


def _review_artifacts(results: list[dict[str, Any]]) -> dict[str, Any]:
    items = [_result_review_artifact(item) for item in results if isinstance(item, dict)]
    return {
        "schema_version": "offline_simulation_review_artifacts_v1",
        "result_count": len(items),
        "request_count": sum(len(item.get("request_ids") or []) for item in items),
        "event_count": sum(len(item.get("event_ids") or []) for item in items),
        "tool_call_count": sum(len(item.get("tool_call_names") or []) for item in items),
        "outbox_batch_count": sum(int(item.get("outbox_batch_count") or 0) for item in items),
        "simulated_write_count": sum(int(item.get("simulated_write_count") or 0) for item in items),
        "results": items,
    }


def _result_review_artifact(result: dict[str, Any]) -> dict[str, Any]:
    request_ids: list[str] = []
    event_ids: list[str] = []
    node_trace_names: list[str] = []
    tool_call_names: list[str] = []
    sync_reply_message_count = 0
    outbox_batch_count = 0
    simulated_write_count = 0
    for step in result.get("steps") or []:
        if not isinstance(step, dict):
            continue
        request_id = str(step.get("request_id") or "").strip()
        if request_id and request_id not in request_ids:
            request_ids.append(request_id)
        event_id = str(step.get("event_id") or "").strip()
        if event_id and event_id not in event_ids:
            event_ids.append(event_id)
        sync = step.get("sync_reply_messages") if isinstance(step.get("sync_reply_messages"), list) else []
        sync_reply_message_count += len(sync)
        outbox = step.get("new_outbox") if isinstance(step.get("new_outbox"), list) else []
        outbox_batch_count += len(outbox)
        writes = step.get("new_simulated_writes") if isinstance(step.get("new_simulated_writes"), list) else []
        simulated_write_count += len(writes)
        run = step.get("run") if isinstance(step.get("run"), dict) else {}
        for trace in run.get("node_traces") or []:
            if not isinstance(trace, dict):
                continue
            name = str(trace.get("node_name") or trace.get("node") or trace.get("name") or "").strip()
            if name and name not in node_trace_names:
                node_trace_names.append(name)
    for call in result.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or call.get("tool") or "").strip()
        if name and name not in tool_call_names:
            tool_call_names.append(name)
    return _drop_empty(
        {
            "scenario_id": result.get("scenario_id"),
            "attempt": result.get("attempt"),
            "run_dir": result.get("run_dir"),
            "request_ids": request_ids,
            "event_ids": event_ids,
            "node_trace_names": node_trace_names,
            "tool_call_names": tool_call_names,
            "sync_reply_message_count": sync_reply_message_count,
            "outbox_batch_count": outbox_batch_count,
            "simulated_write_count": simulated_write_count,
            "hard_pass": result.get("hard_pass"),
            "infrastructure_errors": result.get("infrastructure_errors") or [],
        }
    )


def _message_excerpt(message: Any) -> str:
    if not isinstance(message, dict):
        return str(message)
    message_type = str(message.get("type") or "").strip() or "message"
    content = message.get("content")
    if isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or content.get("url") or content).strip()
    else:
        text = str(content or "").strip()
    return f"{message_type}: {text}"


def _join_excerpt(parts: list[str], *, limit: int = 240) -> str:
    text = " / ".join(part for part in parts if part)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _md_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value


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


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


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

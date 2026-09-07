from __future__ import annotations

from typing import Any

from .follow_sequence import script_media_assets

from .first_day import (
    FIRST_DAY_SILENCE_TRIGGER_TYPE,
    OUTREACH_MESSAGE_SYSTEM_PROMPT,
    OutreachMessagePolicyError,
    S10_OUTREACH_CONTEXT,
    _bool,
    _compose_outreach_messages,
    _first_day_message_policy_error,
    _first_reply_text,
    _int,
    _reply_texts,
    _string,
    _task_metadata,
    _task_resolved_assets,
    dumps,
)


class MessageGenerator:
    def __init__(self, *, repository: Any, model_client: Any) -> None:
        self.repository = repository
        self.model_client = model_client

    async def _generate_task_messages(
        self,
        *,
        task: dict[str, Any],
        plan: dict[str, Any],
        recent_messages_override: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        context = self.repository.recent_customer_context(
            str(task["customer_id"]),
            corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
            wechat=str(task.get("wechat") or plan.get("wechat") or ""),
            external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
        )
        if recent_messages_override is not None:
            context = {
                **context,
                "recent_messages": [
                    dict(message)
                    for message in recent_messages_override
                    if isinstance(message, dict)
                ],
            }
        resolved_assets = _task_resolved_assets(task)
        resolved_asset = resolved_assets[0] if resolved_assets else {}
        task_metadata = _task_metadata(task)
        plan_mode = _string(task_metadata.get("plan_mode"))
        follow_script_candidates = [
            dict(item)
            for item in task_metadata.get("follow_script_candidates") or []
            if isinstance(item, dict)
        ]
        prompt_task_metadata = dict(task_metadata)
        if plan_mode == "follow_sequence":
            prompt_task_metadata["follow_script_candidates"] = [
                dict(item)
                for item in task_metadata.get("follow_script_model_candidates") or []
                if isinstance(item, dict)
            ]
            prompt_task_metadata.pop("follow_script_model_candidates", None)
        source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
        trigger_context = (
            source_snapshot.get("trigger_context")
            if isinstance(source_snapshot.get("trigger_context"), dict)
            else {}
        )
        first_day_opened_silence = (
            _string(trigger_context.get("trigger_type")) == FIRST_DAY_SILENCE_TRIGGER_TYPE
        )
        should_send_payment_collection = bool(task.get("should_send_payment_collection"))
        if first_day_opened_silence:
            should_send_payment_collection = False
        step_index = _int(task.get("step_index"), 0)
        if first_day_opened_silence and _bool(task_metadata.get("preserve_sop_pack_messages")):
            texts = _reply_texts(task.get("reply_messages"))
            policy_error, evidence = _first_day_message_policy_error(
                texts,
                step_index=step_index,
                plan=plan,
                context=context,
            )
            if policy_error:
                raise OutreachMessagePolicyError(policy_error or evidence)
            preserved_messages = [
                dict(message)
                for message in task.get("reply_messages") or []
                if isinstance(message, dict)
                and (
                    _string(message.get("type")) != "payment_collection"
                    or should_send_payment_collection
                )
            ]
            for order, message in enumerate(preserved_messages, start=1):
                message["order"] = order
            return preserved_messages
        payload = {
            "task": {
                "step_index": step_index,
                "first_day_opened_silence": first_day_opened_silence,
                "intent": task.get("intent"),
                "message_goal": task.get("message_goal"),
                "draft_text": _first_reply_text(task.get("reply_messages")),
                "draft_texts": _reply_texts(task.get("reply_messages")),
                "should_send_payment_collection": should_send_payment_collection,
            },
            "task_metadata": prompt_task_metadata,
            "resolved_asset": {
                key: resolved_asset.get(key)
                for key in (
                    "asset_id",
                    "type",
                    "source",
                    "name",
                    "annotation",
                    "use_cases",
                    "avoid_when",
                    "tags",
                    "description",
                )
                if resolved_asset.get(key)
            },
            "resolved_assets": [
                {
                    key: asset.get(key)
                    for key in (
                        "asset_id",
                        "type",
                        "source",
                        "name",
                        "annotation",
                        "use_cases",
                        "avoid_when",
                        "tags",
                        "description",
                    )
                    if asset.get(key)
                }
                for asset in resolved_assets
            ],
            "plan": {
                "customer_stage": plan.get("customer_stage"),
                "stall_reason": plan.get("stall_reason"),
                "customer_psychology": plan.get("customer_psychology"),
                "plan_goal": plan.get("plan_goal"),
            },
            "customer_context": context,
            "offer_context": S10_OUTREACH_CONTEXT,
        }
        model_messages = [
            {"role": "system", "content": OUTREACH_MESSAGE_SYSTEM_PROMPT},
            {"role": "user", "content": dumps(payload)},
        ]
        last_error = ""
        last_evidence = ""
        last_selection_error = ""
        for attempt in range(2):
            response = await self.model_client.chat_json(
                model_messages,
                tier="balanced",
                temperature=0.0,
            )
            texts = _reply_texts(response.get("reply_messages"))
            if not texts:
                raise RuntimeError("outreach_message_model_empty")
            selected_script_id = _string(response.get("selected_script_id"))
            script_rejection_reason = _string(response.get("script_rejection_reason"))
            selected_script = next(
                (
                    item
                    for item in follow_script_candidates
                    if selected_script_id
                    and selected_script_id
                    in {_string(item.get("id")), _string(item.get("script_code"))}
                ),
                {},
            )
            last_selection_error = ""
            if plan_mode == "follow_sequence":
                if selected_script_id and not selected_script:
                    last_selection_error = "selected_script_id_not_in_candidates"
                elif follow_script_candidates and not selected_script_id and not script_rejection_reason:
                    last_selection_error = "script_selection_or_rejection_reason_required"
                elif not follow_script_candidates and selected_script_id:
                    last_selection_error = "script_catalog_empty_cannot_select_script"
            if last_selection_error:
                if attempt == 0:
                    model_messages.extend(
                        [
                            {"role": "assistant", "content": dumps(response)},
                            {
                                "role": "user",
                                "content": dumps(
                                    {
                                        "structure_error": last_selection_error,
                                        "repair_instruction": (
                                            "只从 follow_script_candidates 选择真实ID；"
                                            "若全部不安全，selected_script_id 留空并填写具体拒绝原因。"
                                        ),
                                    }
                                ),
                            },
                        ]
                    )
                    continue
                raise OutreachMessagePolicyError(last_selection_error)
            if not first_day_opened_silence:
                return _compose_outreach_messages(
                    texts,
                    resolved_assets=resolved_assets,
                    should_send_payment_collection=should_send_payment_collection,
                    text_limit=(
                        None
                        if first_day_opened_silence
                        and _string(task_metadata.get("source_kind")) == "mainline_sop"
                        else 2
                    ),
                )
            last_error, last_evidence = _first_day_message_policy_error(
                texts,
                step_index=step_index,
                plan=plan,
                context=context,
            )
            if not last_error:
                generated_messages = _compose_outreach_messages(
                    texts,
                    resolved_assets=[
                        *resolved_assets,
                        *script_media_assets(selected_script),
                    ],
                    should_send_payment_collection=should_send_payment_collection,
                    text_limit=(
                        None
                        if _string(task_metadata.get("source_kind")) == "mainline_sop"
                        else 2
                    ),
                )
                if plan_mode == "follow_sequence":
                    return {
                        "reply_messages": generated_messages,
                        "selected_script_id": selected_script_id,
                        "selected_script_code": _string(selected_script.get("script_code")),
                        "selected_script_name": _string(selected_script.get("script_name")),
                        "script_candidate_count": len(follow_script_candidates),
                        "script_rejection_reason": script_rejection_reason,
                    }
                return generated_messages
            if attempt == 0:
                model_messages.extend(
                    [
                        {"role": "assistant", "content": dumps(response)},
                        {
                            "role": "user",
                            "content": dumps(
                                {
                                    "policy_error": last_error,
                                    "conflicting_text": last_evidence[:240],
                                    "repair_instruction": (
                                        "保持计划锁定的场景、事实、素材和CTA不变，完整重写客户可见文字。"
                                        "不得只换称呼或语序；使用中性称谓，不得出现任何性别化称呼或暗示。"
                                    ),
                                }
                            ),
                        },
                    ]
                )
        raise OutreachMessagePolicyError(
            last_selection_error or last_error or "first_day_message_policy_violation"
        )

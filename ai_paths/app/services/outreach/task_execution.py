from __future__ import annotations

from typing import Any


_LEGACY_DEPENDENCIES = (
    "FIRST_DAY_DAILY_TASK_LIMIT",
    "FIRST_DAY_SILENCE_TRIGGER_TYPE",
    "OUTREACH_DAILY_TASK_LIMIT",
    "OutreachMessagePolicyError",
    "_filter_recently_sent_outreach_media",
    "_first_day_wechat_allowed",
    "_first_day_wechat_allowlist",
    "_missing_outreach_identity_fields",
    "_next_outreach_day_start",
    "_string",
    "_terminal_outreach_send_failure_reason",
    "build_customer_scope",
    "customer_relation_is_deleted",
    "datetime",
    "timezone",
    "utc_now_iso",
)


def _bind_legacy_dependencies() -> None:
    # Imported lazily to keep the public compatibility module acyclic at load time.
    from .. import outreach_service as compatibility

    namespace = globals()
    for name in _LEGACY_DEPENDENCIES:
        namespace[name] = getattr(compatibility, name)


class TaskExecutor:
    """Focused implementation component backed by the compatibility facade."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)

    async def execute(self, task_id: str) -> dict[str, Any]:
        _bind_legacy_dependencies()
        task = self.repository.get_outreach_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}
        plan_detail = self.repository.get_outreach_plan(str(task["plan_id"]))
        plan = plan_detail.get("plan") or {}
        source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
        strategy_shadow = (
            source_snapshot.get("plan_type") in {"followup_strategy", "closing_sequence"}
            and source_snapshot.get("runtime_mode") == "shadow"
        )
        reply_messages = task.get("reply_messages") or []
        if not reply_messages and not strategy_shadow:
            return {"ok": False, "status": "blocked", "error": "preview_required", "retryable": True}
        if not self.repository.claim_outreach_task(task_id):
            return {"ok": True, "status": "skipped", "reason": "task_already_claimed"}
        trigger_context = (
            source_snapshot.get("trigger_context")
            if isinstance(source_snapshot.get("trigger_context"), dict)
            else {}
        )
        is_first_day_plan = (
            _string(trigger_context.get("trigger_type")) == FIRST_DAY_SILENCE_TRIGGER_TYPE
        )
        conversation_id_send_support = getattr(
            self.system_client,
            "supports_conversation_id_send",
            None,
        )
        if is_first_day_plan and conversation_id_send_support is False:
            reason = "conversation_id_send_contract_disabled"
            self.repository.update_outreach_task(task_id, status="skipped", error_message=reason)
            self.repository.skip_remaining_outreach_tasks(
                str(task["plan_id"]),
                reason=reason,
                exclude_task_id=task_id,
            )
            self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
            self._sync_first_day_run_for_task(
                plan=plan,
                task=task,
                status="blocked",
                reason_code=reason,
                final_decision="no_send",
                terminal=True,
            )
            return {"ok": True, "status": "skipped", "reason": reason}
        identity = {
            "customer_id": _string(task.get("customer_id")),
            "corp_id": _string(task.get("corp_id") or plan.get("corp_id")),
            "user_id": _string(task.get("user_id") or plan.get("user_id")),
            "wechat": _string(task.get("wechat") or plan.get("wechat")),
            "external_userid": _string(task.get("external_userid") or plan.get("external_userid")),
        }
        missing_identity = _missing_outreach_identity_fields(identity)
        if missing_identity:
            reason = "invalid_outreach_identity"
            detail = {
                "missing": missing_identity,
                "identity": identity,
                "note": "customer_id and external_userid are distinct upstream identities and must not fallback to each other",
            }
            self.repository.update_outreach_task(task_id, status="failed", error_message=reason)
            self.repository.skip_remaining_outreach_tasks(
                str(task["plan_id"]),
                reason=reason,
                exclude_task_id=task_id,
            )
            self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="task_failed_terminal",
                event_summary=reason,
                payload=detail,
            )
            self._sync_first_day_run_for_task(
                plan=plan,
                task=task,
                status="blocked",
                reason_code=reason,
                final_decision="blocked",
                terminal=True,
            )
            return {"ok": False, "status": "failed", "error": reason, **detail}
        if is_first_day_plan and not _first_day_wechat_allowed(identity["wechat"], self.first_day_wechat_allowlist):
            reason = "first_day_wechat_not_allowed"
            self.repository.update_outreach_task(task_id, status="skipped", error_message=reason)
            self.repository.skip_remaining_outreach_tasks(
                str(task["plan_id"]),
                reason=reason,
                exclude_task_id=task_id,
            )
            self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="task_skipped_first_day_wechat_not_allowed",
                event_summary="First-day outreach task skipped because the receiving WeChat account is not allowlisted",
                payload={
                    "wechat": identity["wechat"],
                    "allowlist_configured": bool(_first_day_wechat_allowlist(self.first_day_wechat_allowlist)),
                },
            )
            self._sync_first_day_run_for_task(
                plan=plan,
                task=task,
                status="cancelled",
                reason_code=reason,
                final_decision="no_send",
                terminal=True,
            )
            return {"ok": True, "status": "skipped", "reason": reason}
        fresh_conversation_messages: list[dict[str, Any]] = []
        send_conversation_id = _string(source_snapshot.get("conversation_id")) or _string(
            trigger_context.get("conversation_id")
        )
        try:
            sent_today_loader = getattr(self.repository, "outreach_sent_today_count", None)
            if callable(sent_today_loader):
                sent_today_count = sent_today_loader(
                    customer_id=str(task["customer_id"]),
                    corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                    wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                    external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
                )
                daily_task_limit = FIRST_DAY_DAILY_TASK_LIMIT if is_first_day_plan else OUTREACH_DAILY_TASK_LIMIT
                if sent_today_count >= daily_task_limit:
                    if is_first_day_plan:
                        self.repository.update_outreach_task(
                            task_id,
                            status="skipped",
                            error_message="first_day_daily_task_limit_reached",
                        )
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="first_day_daily_task_limit_reached",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="plan_cancelled_first_day_daily_task_limit",
                            event_summary="First-day outreach plan cancelled because its daily task limit was reached",
                            payload={"sent_today": sent_today_count, "daily_task_limit": daily_task_limit},
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="first_day_daily_task_limit_reached",
                            final_decision="no_send",
                            terminal=True,
                        )
                        return {
                            "ok": True,
                            "status": "skipped",
                            "reason": "first_day_daily_task_limit_reached",
                        }
                    next_window = _next_outreach_day_start()
                    delay_seconds = max(
                        1,
                        int((next_window - datetime.now(timezone.utc)).total_seconds()),
                    )
                    self.repository.reschedule_outreach_task(
                        task_id,
                        delay_seconds=delay_seconds,
                        error_message="personalized_outreach_daily_limit",
                    )
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="task_deferred_daily_limit",
                        event_summary="Personalized outreach daily limit reached; task deferred",
                        payload={"sent_today": sent_today_count, "next_window": next_window.isoformat()},
                    )
                    self._sync_first_day_run_for_task(
                        plan=plan,
                        task=task,
                        status="created",
                        reason_code="daily_limit",
                        final_decision="retry_pending",
                    )
                    return {"ok": True, "status": "rescheduled", "reason": "daily_limit"}
            if task.get("before_send_check"):
                try:
                    refresh = await self.refresh_customer_conversation(
                        customer_id=str(task["customer_id"]),
                        corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                        user_id=str(task.get("user_id") or plan.get("user_id") or ""),
                        wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                        external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
                        limit=50,
                    )
                    fresh_conversation_messages = [
                        dict(message)
                        for message in refresh.get("messages") or []
                        if isinstance(message, dict)
                    ]
                    send_conversation_id = _string(refresh.get("conversation_id")) or send_conversation_id
                    customer_relation = (
                        refresh.get("customer_relation")
                        if isinstance(refresh.get("customer_relation"), dict)
                        else {}
                    )
                    if not customer_relation.get("available"):
                        raise RuntimeError("before_send_customer_relation_unavailable")
                    if customer_relation_is_deleted(customer_relation):
                        self.repository.update_outreach_task(task_id, status="skipped")
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="customer_deleted",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="task_skipped_customer_deleted",
                            event_summary="Customer deleted the sales contact before outreach execution",
                            payload={"customer_relation": customer_relation},
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="customer_deleted",
                            final_decision="cancelled",
                            terminal=True,
                        )
                        return {
                            "ok": True,
                            "status": "skipped",
                            "reason": "customer_deleted",
                            "customer_relation": customer_relation,
                        }
                    if self._customer_replied_after_plan(plan, refresh.get("latest_customer_message_at")):
                        self.repository.update_outreach_task(task_id, status="skipped")
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="customer_replied_after_plan_creation",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="task_skipped_customer_replied",
                            event_summary="Customer replied before outreach task execution",
                            payload=refresh,
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="customer_replied",
                            final_decision="second_task_cancelled",
                            terminal=True,
                        )
                        return {"ok": True, "status": "skipped", "reason": "customer_replied"}
                    order_gate = await self._refresh_order_eligibility(task=task, plan=plan)
                    if not order_gate.get("available"):
                        raise RuntimeError(
                            f"before_send_order_check_unavailable: {order_gate.get('reason') or 'unknown'}"
                        )
                    if not order_gate.get("eligible"):
                        self.repository.update_outreach_task(task_id, status="skipped")
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="customer_order_state_changed",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="task_skipped_order_state_changed",
                            event_summary="Customer order state changed before outreach execution",
                            payload=order_gate,
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="order_state_changed",
                            final_decision="cancelled",
                            terminal=True,
                        )
                        return {"ok": True, "status": "skipped", "reason": "order_state_changed"}
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    self.repository.reschedule_outreach_task(
                        task_id,
                        delay_seconds=self.before_send_retry_seconds,
                        error_message=message,
                    )
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="before_send_check_failed",
                        event_summary="Conversation check failed before outreach send; send blocked",
                        payload={"error": message},
                    )
                    self._sync_first_day_run_for_task(
                        plan=plan,
                        task=task,
                        status="created",
                        reason_code="before_send_check_failed",
                        final_decision="retry_pending",
                        error=exc,
                    )
                    return {"ok": False, "status": "rescheduled", "error": message, "retryable": True}
            try:
                if is_first_day_plan and conversation_id_send_support is True and not send_conversation_id:
                    raise RuntimeError("first_day_conversation_id_unavailable")
                reply_messages = await self._generate_task_messages(
                    task=task,
                    plan=plan,
                    recent_messages_override=fresh_conversation_messages,
                )
            except OutreachMessagePolicyError as exc:
                reason = _string(exc) or "first_day_message_policy_violation"
                self.repository.update_outreach_task(task_id, status="skipped", error_message=reason)
                self.repository.skip_remaining_outreach_tasks(
                    str(task["plan_id"]),
                    reason=reason,
                    exclude_task_id=task_id,
                )
                self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                self.repository.add_outreach_event(
                    plan_id=str(task["plan_id"]),
                    task_id=task_id,
                    customer_id=str(task["customer_id"]),
                    event_type="task_skipped_message_policy",
                    event_summary="First-day outreach message remained unsafe after rewrite",
                    payload={"reason": reason},
                )
                self._sync_first_day_run_for_task(
                    plan=plan,
                    task=task,
                    status="blocked",
                    reason_code=reason,
                    final_decision="blocked",
                    terminal=True,
                )
                return {"ok": True, "status": "skipped", "reason": reason}
            if is_first_day_plan:
                messages_before_policy = len(reply_messages)
                reply_messages = [
                    dict(message)
                    for message in reply_messages
                    if isinstance(message, dict)
                    and _string(message.get("type")) != "payment_collection"
                ]
                if len(reply_messages) != messages_before_policy:
                    for order, message in enumerate(reply_messages, start=1):
                        message["order"] = order
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="first_day_payment_card_removed",
                        event_summary="Removed a legacy payment card before first-day outreach send",
                        payload={
                            "removed_count": messages_before_policy - len(reply_messages),
                            "policy": "first_day_text_payment_only",
                        },
                    )
                if not reply_messages:
                    reason = "first_day_payment_card_only_task_blocked"
                    self.repository.update_outreach_task(
                        task_id,
                        status="skipped",
                        error_message=reason,
                    )
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="task_skipped_first_day_payment_card_only",
                        event_summary="Skipped a first-day outreach task that contained only a payment card",
                        payload={"reason": reason},
                    )
                    self._sync_first_day_run_for_task(
                        plan=plan,
                        task=task,
                        status="blocked",
                        reason_code=reason,
                        final_decision="no_send",
                        terminal=True,
                    )
                    return {"ok": True, "status": "skipped", "reason": reason}
                reply_messages, duplicate_media_urls = _filter_recently_sent_outreach_media(
                    reply_messages,
                    fresh_conversation_messages,
                )
                if duplicate_media_urls:
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="first_day_duplicate_media_removed",
                        event_summary="Removed media already visible in the latest platform conversation",
                        payload={
                            "duplicate_media_urls": duplicate_media_urls,
                            "remaining_message_types": [
                                _string(message.get("type")) for message in reply_messages
                            ],
                        },
                    )
                if not reply_messages:
                    reason = "first_day_duplicate_media_only_task_skipped"
                    self.repository.update_outreach_task(task_id, status="skipped", error_message=reason)
                    self.repository.skip_remaining_outreach_tasks(
                        str(task["plan_id"]),
                        reason=reason,
                        exclude_task_id=task_id,
                    )
                    self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                    self._sync_first_day_run_for_task(
                        plan=plan,
                        task=task,
                        status="cancelled",
                        reason_code=reason,
                        final_decision="no_send",
                        terminal=True,
                    )
                    return {"ok": True, "status": "skipped", "reason": reason}
            payment_duplicate = self._unanswered_payment_card_duplicate(
                task=task,
                plan=plan,
                reply_messages=reply_messages,
                recent_messages=fresh_conversation_messages,
            )
            if payment_duplicate.get("active"):
                reason = "unanswered_payment_card_duplicate"
                self.repository.update_outreach_task(
                    task_id,
                    status="skipped",
                    error_message=reason,
                )
                self.repository.add_outreach_event(
                    plan_id=str(task["plan_id"]),
                    task_id=task_id,
                    customer_id=str(task["customer_id"]),
                    event_type="task_skipped_unanswered_payment_card",
                    event_summary="Outreach payment card skipped because the previous card is still unanswered",
                    payload=payment_duplicate,
                )
                remaining_loader = getattr(self.repository, "outreach_plan_has_remaining_tasks", None)
                has_remaining = bool(remaining_loader(str(task["plan_id"]))) if callable(remaining_loader) else False
                self.repository.update_outreach_plan_status(
                    str(task["plan_id"]),
                    "waiting" if has_remaining else "completed",
                )
                self._sync_first_day_run_for_task(
                    plan=plan,
                    task=task,
                    status="created" if has_remaining else "cancelled",
                    reason_code=reason,
                    final_decision="next_task_pending" if has_remaining else "no_send",
                    terminal=not has_remaining,
                )
                return {"ok": True, "status": "skipped", "reason": reason}
            if strategy_shadow:
                self.repository.update_outreach_task(
                    task_id,
                    status="shadowed",
                    reply_messages=reply_messages,
                )
                self.repository.add_outreach_event(
                    plan_id=str(task["plan_id"]),
                    task_id=task_id,
                    customer_id=str(task["customer_id"]),
                    event_type="task_shadowed",
                    event_summary="Strategy task evaluated in shadow mode; no customer message sent",
                    payload={
                        "reply_messages": reply_messages,
                        "source_snapshot": source_snapshot,
                    },
                )
                remaining_loader = getattr(self.repository, "outreach_plan_has_remaining_tasks", None)
                has_remaining = bool(remaining_loader(str(task["plan_id"]))) if callable(remaining_loader) else False
                self.repository.update_outreach_plan_status(
                    str(task["plan_id"]),
                    "waiting" if has_remaining else "completed",
                )
                return {
                    "ok": True,
                    "status": "shadowed",
                    "reply_messages": reply_messages,
                    "sent": False,
                }
            task = self.repository.update_outreach_task(
                task_id,
                status="sending",
                reply_messages=reply_messages,
            )
            send_result = await self.system_client.send(
                corp_id=identity["corp_id"],
                customer_id=identity["customer_id"],
                external_userid=identity["external_userid"],
                user_id=identity["user_id"],
                wechat=identity["wechat"],
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                reply_messages=reply_messages,
                conversation_id=send_conversation_id,
                source_channel="proactive_message",
                source_kind="outreach_task",
                source_request_id=str(task.get("plan_id") or ""),
                source_task_id=task_id,
                source_context={
                    "outreach_task_id": task_id,
                    "outreach_plan_id": str(task.get("plan_id") or ""),
                },
                delivery_idempotency_key=f"outreach_task:{task_id}",
            )
        except Exception as exc:
            message = str(exc)
            self.repository.update_outreach_task(task_id, status="failed", error_message=message)
            terminal_reason = _terminal_outreach_send_failure_reason(message)
            cancel_reason = terminal_reason or (
                "first_day_preceding_task_failed" if is_first_day_plan else ""
            )
            if cancel_reason:
                self.repository.skip_remaining_outreach_tasks(
                    str(task["plan_id"]),
                    reason=cancel_reason,
                    exclude_task_id=task_id,
                )
                self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
            if terminal_reason:
                scope = build_customer_scope(
                    corp_id=task.get("corp_id") or plan.get("corp_id"),
                    wechat=task.get("wechat") or plan.get("wechat"),
                    external_userid=task.get("external_userid") or plan.get("external_userid"),
                    customer_id=task.get("customer_id"),
                )
                if scope.persistence_allowed:
                    self.repository.update_customer_outreach_state(
                        scope.sales_contact_key,
                        outreach_status="cancelled",
                        outreach_plan_id="",
                    )
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="task_failed_terminal" if terminal_reason else "task_failed",
                event_summary=message[:240],
                payload={"error": message, "terminal_reason": terminal_reason},
            )
            self._sync_first_day_run_for_task(
                plan=plan,
                task=task,
                status="blocked" if terminal_reason else "failed",
                reason_code=terminal_reason or "task_failed",
                final_decision="blocked" if terminal_reason else "failed",
                terminal=True,
                error=exc,
            )
            return {"ok": False, "status": "failed", "error": message}
        data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
        if bool(data.get("callback_required")) and str(data.get("delivery_status") or "") in {
            "platform_accepted",
            "submission_unknown",
            "sending",
        }:
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="task_platform_accepted",
                event_summary="Outreach task accepted; awaiting delivery callback",
                payload={"reply_messages": reply_messages, "send_result": send_result},
            )
            return {"ok": True, "status": "accepted", "send_result": send_result}
        sent_at = utc_now_iso()
        self.repository.update_outreach_task(
            task_id,
            status="sent",
            reply_messages=reply_messages,
            sent_at=sent_at,
            send_status=str(data.get("send_status") or send_result.get("msg") or "accepted"),
            system_msgid=str(data.get("system_msgid") or ""),
        )
        remaining_loader = getattr(self.repository, "outreach_plan_has_remaining_tasks", None)
        has_remaining_tasks = bool(remaining_loader(str(task["plan_id"]))) if callable(remaining_loader) else True
        next_plan_status = "waiting" if has_remaining_tasks else "completed"
        scope = build_customer_scope(
            corp_id=task.get("corp_id") or plan.get("corp_id"),
            wechat=task.get("wechat") or plan.get("wechat"),
            external_userid=task.get("external_userid") or plan.get("external_userid"),
            customer_id=task.get("customer_id"),
        )
        if scope.persistence_allowed:
            self.repository.touch_customer_message_time(scope.sales_contact_key, field="last_outreach_at", value=sent_at)
            self.repository.update_customer_outreach_state(
                scope.sales_contact_key,
                outreach_status=next_plan_status,
                outreach_plan_id=str(task["plan_id"]) if has_remaining_tasks else "",
                last_outreach_at=sent_at,
            )
        self.repository.update_outreach_plan_status(str(task["plan_id"]), next_plan_status)
        self.repository.add_outreach_event(
            plan_id=str(task["plan_id"]),
            task_id=task_id,
            customer_id=str(task["customer_id"]),
            event_type="task_sent",
            event_summary="Outreach task sent",
            payload={"reply_messages": reply_messages, "send_result": send_result},
        )
        self._sync_first_day_run_for_task(
            plan=plan,
            task=task,
            status="sent" if not has_remaining_tasks else "created",
            reason_code="plan_completed" if not has_remaining_tasks else "first_task_sent",
            final_decision="sent" if not has_remaining_tasks else "second_task_pending",
            terminal=not has_remaining_tasks,
        )
        if not has_remaining_tasks:
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="plan_cycle_completed",
                event_summary="Final outreach step sent; current personalized outreach cycle completed",
                payload={"sent_at": sent_at},
            )
        return {"ok": True, "status": "sent", "send_result": send_result}

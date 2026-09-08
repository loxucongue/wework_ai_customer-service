from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import Settings


logger = logging.getLogger(__name__)


class SopPlatformTaskStateError(RuntimeError):
    """The platform rejected a transition because the task is already terminal."""

    def __init__(self, *, state: str, payload: dict[str, Any]):
        self.state = state
        self.payload = payload
        super().__init__(f"sop_platform_task_terminal_state:{state}: {payload}")


class SopPlatformClient:
    """Client for the third-party SOP task queue.

    The upstream task-state contract is 10 (pending), 20 (processing),
    30 (sent), and 70 (not sent). Optional message results use 30 (sent),
    40 (failed), or 70 (not sent).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self.settings.sop_platform_token)

    async def pending(
        self,
        *,
        limit: int | None = None,
        corp_id: str = "",
        wechat: str = "",
    ) -> dict[str, Any]:
        return await self._pending_page(
            path="/event/trigger/pending",
            biz_type="online_service",
            limit=limit,
            corp_id=corp_id,
            wechat=wechat,
        )

    async def store_visit_pending(
        self,
        *,
        limit: int | None = None,
        corp_id: str = "",
        wechat: str = "",
    ) -> dict[str, Any]:
        return await self._pending_page(
            path="/event/trigger/store-visit-pending",
            biz_type="store_visit",
            limit=limit,
            corp_id=corp_id,
            wechat=wechat,
        )

    async def sop_messages(
        self,
        *,
        event_log_id: int | str,
        limit: int | None = None,
        corp_id: str = "",
        wechat: str = "",
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("SOP_PLATFORM_TOKEN is not configured")
        clean_event_log_id = str(event_log_id or "").strip()
        if not clean_event_log_id or clean_event_log_id == "0":
            raise ValueError("event_log_id is required for /event/trigger/sop-messages")
        payload: dict[str, Any] = {
            "corp_id": str(corp_id or "").strip(),
            "wechat": str(wechat or "").strip(),
            "limit": max(1, min(int(limit or self.settings.sop_platform_batch_size), 500)),
        }
        try:
            payload["eventLogId"] = int(clean_event_log_id)
        except ValueError:
            payload["eventLogId"] = clean_event_log_id
        response = await self._request("POST", "/event/trigger/sop-messages", json_body=payload)
        data = response.get("data")
        items: list[dict[str, Any]] = []
        total = 0
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
            total = len(items)
        if isinstance(data, dict):
            try:
                total = max(0, int(data.get("total") or data.get("remainingGroupCount") or 0))
            except (TypeError, ValueError):
                total = 0
            for key in ("list", "items", "records", "tasks", "remainingGroups"):
                raw_items = data.get(key)
                if isinstance(raw_items, list):
                    items = [item for item in raw_items if isinstance(item, dict)]
                    break
            if not items and isinstance(data.get("nextGroup"), dict):
                items = [data["nextGroup"]]
        next_item = (
            data.get("nextGroup") if isinstance(data, dict) and isinstance(data.get("nextGroup"), dict) else None
        )
        if isinstance(next_item, dict) and not _sop_message_group_is_unconsumed(next_item):
            next_item = None
        if next_item is None:
            next_item = next((item for item in items if _sop_message_group_is_unconsumed(item)), None)
        if not total:
            total = len(items)
        return {
            "items": items,
            "total": total,
            "limit": payload["limit"],
            "biz_type": "sop_messages",
            "event_log_id": clean_event_log_id,
            "complete": total <= len(items),
            "next_item": next_item,
        }

    async def _pending_page(
        self,
        *,
        path: str,
        biz_type: str,
        limit: int | None,
        corp_id: str,
        wechat: str,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("SOP_PLATFORM_TOKEN is not configured")
        payload = {
            "corp_id": str(corp_id or "").strip(),
            "wechat": str(wechat or "").strip(),
            "limit": max(1, min(int(limit or self.settings.sop_platform_batch_size), 500)),
        }
        response = await self._request("POST", path, json_body=payload)
        data = response.get("data")
        items: list[dict[str, Any]] = []
        total = 0
        if isinstance(data, list):
            items = [item for item in data if isinstance(item, dict)]
            total = len(items)
        if isinstance(data, dict):
            try:
                total = max(0, int(data.get("total") or 0))
            except (TypeError, ValueError):
                total = 0
            for key in ("list", "items", "records", "tasks"):
                raw_items = data.get(key)
                if isinstance(raw_items, list):
                    items = [item for item in raw_items if isinstance(item, dict)]
                    break
        if not total:
            total = len(items)
        return {
            "items": items,
            "total": total,
            "limit": payload["limit"],
            "biz_type": biz_type,
            "complete": total <= len(items),
        }

    async def consume(
        self,
        *,
        task_id: str | int,
        status: int,
        remark: str = "",
        messages: list[dict[str, Any]] | None = None,
        content_exhausted: bool | None = None,
    ) -> dict[str, Any]:
        if status not in {20, 30, 70}:
            raise ValueError("platform SOP status must be 20, 30, or 70")
        payload: dict[str, Any] = {
            "taskId": task_id,
            "status": status,
            "remark": str(remark or "")[:500],
        }
        normalized_messages: list[dict[str, Any]] | None = None
        if messages is not None:
            normalized_messages = []
            for raw in messages:
                if not isinstance(raw, dict):
                    raise ValueError("platform SOP message result must be an object")
                msg_id = raw.get("msgId", raw.get("msg_id"))
                if msg_id is None or str(msg_id).strip() == "":
                    raise ValueError("platform SOP message result requires msgId")
                if isinstance(msg_id, str) and msg_id.strip().isdigit():
                    msg_id = int(msg_id.strip())
                message_status = int(raw.get("status") or 0)
                if message_status not in {30, 40, 70}:
                    raise ValueError("platform SOP message status must be 30, 40, or 70")
                normalized_messages.append(
                    {
                        "msgId": msg_id,
                        "status": message_status,
                        "remark": str(raw.get("remark") or "")[:500],
                    }
                )
        if status == 30:
            if normalized_messages is None or len(normalized_messages) != 1:
                raise ValueError("successful platform SOP consumption requires exactly one explicit msgId")
            if normalized_messages[0]["status"] != 30:
                raise ValueError("successful platform SOP consumption requires message status 30")
            payload["messages"] = normalized_messages
        elif normalized_messages:
            raise ValueError("non-send platform SOP consumption must not consume message content")
        if content_exhausted is not None:
            payload["contentExhausted"] = bool(content_exhausted)
        return await self._request(
            "POST",
            "/event/trigger/consume",
            json_body=payload,
        )

    async def knowledge_categories(
        self,
        *,
        category_name: str = "",
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/event/trigger/knowledge-category",
            json_body={
                "categoryName": category_name,
                "page": max(1, int(page or 1)),
                "pageSize": max(1, min(int(page_size or 100), 100)),
            },
        )

    async def knowledge_base(
        self,
        *,
        category_id: int = 0,
        category_name: str = "",
        knowledge_name: str = "",
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/event/trigger/knowledge-base",
            json_body={
                "categoryId": max(0, int(category_id or 0)),
                "categoryName": category_name,
                "knowledgeName": knowledge_name,
                "page": max(1, int(page or 1)),
                "pageSize": max(1, min(int(page_size or 100), 100)),
            },
        )

    async def service_rule_data(
        self,
        *,
        task_id: str | int,
        scene_name: str,
        send_status: int,
        scene_code: str = "",
        knowledge_id: int | None = None,
        knowledge_paragraph_no: int | None = None,
        remark: str = "",
        send_content: str = "",
    ) -> dict[str, Any]:
        payload = service_rule_data_payload(
            task_id=task_id,
            scene_name=scene_name,
            send_status=send_status,
            scene_code=scene_code,
            knowledge_id=knowledge_id,
            knowledge_paragraph_no=knowledge_paragraph_no,
            remark=remark,
            send_content=send_content,
        )
        return await self._request(
            "POST",
            "/event/trigger/service-rule-data",
            json_body=payload,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("SOP_PLATFORM_TOKEN is not configured")
        url = f"{self.settings.sop_platform_base_url.rstrip('/')}{path}"
        headers = {
            "x-event-token": self.settings.sop_platform_token,
            "Content-Type": "application/json; charset=utf-8",
        }
        started = time.perf_counter()
        trace_events: list[dict[str, Any]] = []
        task_id = str((json_body or {}).get("taskId") or (json_body or {}).get("eventLogId") or "")

        async def trace(event_name: str, _info: dict[str, Any]) -> None:
            trace_events.append(
                {
                    "event": event_name,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )

        kwargs: dict[str, Any] = {"headers": headers, "extensions": {"trace": trace}}
        if json_body is not None:
            kwargs["content"] = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        try:
            response = await self._http_client().request(method, url, **kwargs)
        except Exception as exc:
            logger.warning(
                "sop_platform_http %s",
                json.dumps(
                    {
                        "method": method,
                        "path": path,
                        "task_id": task_id,
                        "result": "exception",
                        "exception_type": type(exc).__name__,
                        "total_ms": round((time.perf_counter() - started) * 1000, 1),
                        "trace": trace_events,
                    },
                    ensure_ascii=True,
                ),
            )
            raise
        text = response.text
        platform_code: Any = None
        try:
            preview_payload = response.json()
            if isinstance(preview_payload, dict):
                platform_code = preview_payload.get("code")
        except ValueError:
            pass
        log_response = logger.warning if task_id else logger.info
        log_response(
            "sop_platform_http %s",
            json.dumps(
                {
                    "method": method,
                    "path": path,
                    "task_id": task_id,
                    "result": "response",
                    "http_status": response.status_code,
                    "platform_code": platform_code,
                    "total_ms": round((time.perf_counter() - started) * 1000, 1),
                    "trace": trace_events,
                },
                ensure_ascii=True,
            ),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"sop_platform_http_{response.status_code}: {text[:800]}")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"sop_platform_invalid_json_response: {text[:800]}") from None
        if not isinstance(payload, dict):
            raise RuntimeError("sop_platform_invalid_response")
        code = payload.get("code")
        if code not in (None, 0, "0", 200, "200"):
            message = str(payload.get("message") or "")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            detail = " ".join((message, str(data.get("message") or ""))).strip()
            state_match = re.search(r"当前状态[：:]\s*([^）)\s,，;；]+)", detail)
            state = str(state_match.group(1) if state_match else "").strip()
            if not state:
                state = next(
                    (
                        candidate
                        for candidate in (
                            "已无需发送",
                            "无需发送",
                            "已不发送",
                            "不发送",
                            "已失败",
                            "失败",
                            "已取消",
                            "已完成",
                        )
                        if candidate in detail
                    ),
                    "",
                )
            if state in {"已取消", "已完成", "已失败", "失败", "已无需发送", "无需发送", "已不发送", "不发送"}:
                raise SopPlatformTaskStateError(state=state, payload=payload)
            raise RuntimeError(f"sop_platform_error: {payload}")
        return payload

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            timeout = max(1.0, float(self.settings.sop_platform_timeout_seconds))
            self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _sop_message_group_is_unconsumed(item: dict[str, Any]) -> bool:
    for key in ("consumed", "isConsumed", "occurred", "isOccurred"):
        value = item.get(key)
        if isinstance(value, bool):
            return not value
    raw_status = item.get("status", item.get("sendStatus", item.get("consumeStatus")))
    if isinstance(raw_status, (int, float)):
        return int(raw_status) not in {30, 40, 70}
    status = str(raw_status or "").strip().lower().replace("-", "_")
    if not status:
        return True
    return status not in {
        "30",
        "40",
        "70",
        "sent",
        "success",
        "completed",
        "consumed",
        "occurred",
        "failed",
        "no_send",
        "skipped",
    }


def service_rule_data_payload(
    *,
    task_id: str | int,
    scene_name: str,
    send_status: int,
    scene_code: str = "",
    knowledge_id: int | None = None,
    knowledge_paragraph_no: int | None = None,
    remark: str = "",
    send_content: str = "",
) -> dict[str, Any]:
    """Build the exact JSON body sent to the platform rule-data callback."""
    normalized_send_status = int(send_status)
    if normalized_send_status not in {10, 20}:
        raise ValueError("send_status must be 10 (success) or 20 (failed)")
    payload: dict[str, Any] = {
        "taskId": task_id,
        "sceneName": scene_name,
        "sendStatus": normalized_send_status,
        "remark": remark[:500],
        "sendContent": send_content[:10000],
    }
    if scene_code:
        payload["sceneCode"] = scene_code
    if knowledge_id:
        payload["knowledgeId"] = int(knowledge_id)
    if knowledge_paragraph_no:
        payload["knowledgeParagraphNo"] = int(knowledge_paragraph_no)
    return payload

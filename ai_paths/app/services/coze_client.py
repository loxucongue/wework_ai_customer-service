import json
import asyncio
from typing import Any

import httpx

from app.config import Settings
from app.schemas import CozeKbItem, CozeKbResult
from app.services.coze_oauth import CozeOAuthTokenProvider


class CozeClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.oauth_provider = CozeOAuthTokenProvider(settings)

    async def run_workflow(
        self,
        workflow_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        token = self.oauth_provider.get_access_token()

        url = f"{self.settings.coze_api_base.rstrip('/')}/v1/workflow/run"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "workflow_id": workflow_id,
            "parameters": parameters,
        }
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=60) as client:
            for attempt in range(3):
                try:
                    response = await client.post(url, headers=headers, content=body)
                    response.raise_for_status()
                    return response.json()
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    raise
        if last_error:
            raise last_error
        raise RuntimeError("Coze workflow request failed without response")

    async def search_kb(self, kb_name: str, query: str) -> CozeKbResult:
        if self._looks_corrupted_query(query):
            raise ValueError("Coze KB query appears corrupted before request; check caller encoding")
        raw = await self._run_kb_workflow_with_fallback(kb_name, query)
        self._raise_if_coze_error(raw)
        payload = self._parse_data(raw)
        output_list = payload.get("outputList") or payload.get("outputlist") or raw.get("outputList") or []
        items = [
            CozeKbItem(
                content=str(item.get("output", "")),
                document_id=str(item.get("documentId", "")),
            )
            for item in output_list
            if isinstance(item, dict) and item.get("output")
        ]
        return CozeKbResult(kb_name=kb_name, items=items, raw=raw)

    async def query_pricing_db(self, sql: str) -> list[dict[str, Any]]:
        raw = await self.run_workflow(
            self.settings.pricing_db_workflow_id,
            {"input": sql},
        )
        self._raise_if_coze_error(raw)
        payload = self._parse_data(raw)
        output = payload.get("output", [])
        return output if isinstance(output, list) else []

    async def _run_kb_workflow_with_fallback(self, kb_name: str, query: str) -> dict[str, Any]:
        payloads = [
            {"kb_name": kb_name, "query": query, "top_k": 5},
            {"kb_name": kb_name, "query": query},
            {"kb_name": kb_name, "input": query, "top_k": 5},
            {"kb_name": kb_name, "input": query},
            {"knowledge_name": kb_name, "query": query, "top_k": 5},
            {"knowledge_name": kb_name, "query": query},
            {"knowledge_name": kb_name, "input": query, "top_k": 5},
            {"knowledge_name": kb_name, "input": query},
        ]
        last_raw: dict[str, Any] = {}
        for payload in payloads:
            raw = await self.run_workflow(self.settings.kb_workflow_id, payload)
            last_raw = raw
            if not self._is_missing_required_params(raw):
                return raw
        return last_raw

    @staticmethod
    def _parse_data(raw: dict[str, Any]) -> dict[str, Any]:
        data = raw.get("data")
        if isinstance(data, str) and data:
            try:
                parsed = json.loads(data)
                return parsed if isinstance(parsed, dict) else {"output": parsed}
            except json.JSONDecodeError:
                return {"output": data}
        if isinstance(data, dict):
            return data
        return raw

    @staticmethod
    def _raise_if_coze_error(raw: dict[str, Any]) -> None:
        code = raw.get("code")
        if code in (None, 0):
            return
        msg = raw.get("msg") or "Coze workflow error"
        raise RuntimeError(f"Coze workflow returned code={code}: {msg}")

    @staticmethod
    def _is_missing_required_params(raw: dict[str, Any]) -> bool:
        if raw.get("code") != 4000:
            return False
        msg = str(raw.get("msg", "")).lower()
        return "missing required parameters" in msg

    @staticmethod
    def _looks_corrupted_query(query: str) -> bool:
        text = query.strip()
        if text.count("?") < 2:
            return False
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
        has_letters_or_digits = any(ch.isalnum() for ch in text)
        return not has_cjk and not has_letters_or_digits

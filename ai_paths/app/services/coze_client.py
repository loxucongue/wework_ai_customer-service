import json
import asyncio
from typing import Any

import httpx

from app.config import Settings
from app.graph.nodes.common import clean_model_text
from app.schemas import CozeKbItem, CozeKbResult
from app.services.coze_oauth import CozeOAuthTokenProvider


class CozeClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.oauth_provider = CozeOAuthTokenProvider(settings)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None

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
        client = self._http_client()
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

    def _http_client(self) -> httpx.AsyncClient:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client.is_closed or self._client_loop_id != loop_id:
            self._client = httpx.AsyncClient(
                timeout=60,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
            )
            self._client_loop_id = loop_id
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search_kb(self, kb_name: str, query: str) -> CozeKbResult:
        if self._looks_corrupted_query(query):
            raise ValueError("Coze KB query appears corrupted before request; check caller encoding")
        raw = await self._run_kb_workflow_with_fallback(kb_name, query)
        self._raise_if_coze_error(raw)
        payload = self._parse_data(raw)
        output_list = payload.get("outputList") or payload.get("outputlist") or raw.get("outputList") or []
        items: list[CozeKbItem] = []
        for item in output_list:
            if not isinstance(item, dict) or not item.get("output"):
                continue
            content = clean_model_text(str(item.get("output", "")))
            if not content:
                continue
            items.append(
                CozeKbItem(
                    content=content,
                    document_id=str(item.get("documentId", "")),
                )
            )
        return CozeKbResult(kb_name=kb_name, items=items, raw=raw)

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

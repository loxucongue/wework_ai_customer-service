from __future__ import annotations

import asyncio
import json

import httpx

from app.config import Settings
from app.services.outreach_system_client import OutreachSystemClient
from app.services.wechat_price_contract import enforce_wechat_price_contract, wechat_price_contract


def _text(value: str, order: int = 1) -> dict:
    return {"type": "text", "order": order, "content": {"text": value}}


def test_199_accounts_replace_only_known_activity_prices() -> None:
    messages = [
        _text("活动价268元，先付10元预约金，到店再付258元。", 1),
        {"type": "image", "order": 2, "content": {"url": "https://oss.test/268/258.png"}},
        {"type": "payment_collection", "order": 3, "content": {"amount": 10}},
    ]

    output, audit = enforce_wechat_price_contract(messages, wechat="WW0601")

    assert output[0]["content"]["text"] == "活动价199元，先付10元预约金，到店再付189元。"
    assert output[1] == messages[1]
    assert output[2] == messages[2]
    assert audit["replacement_count"] == 2


def test_268_accounts_replace_only_known_activity_prices_case_insensitively() -> None:
    output, audit = enforce_wechat_price_contract(
        [_text("线上活动199元，预约金付完后再付189元，前30名有效。")],
        wechat="sl2491",
    )

    assert output[0]["content"]["text"] == "线上活动268元，预约金付完后再付258元，前30名有效。"
    assert audit["activity_price"] == 268
    assert audit["balance_after_deposit"] == 258


def test_unconfigured_account_and_unrelated_numbers_are_unchanged() -> None:
    original = [_text("原价1980元，10元预约金，30个名额，8月到店。")]

    output, audit = enforce_wechat_price_contract(original, wechat="WW9999")

    assert output == original
    assert audit["reason"] == "wechat_not_configured"


def test_all_configured_accounts_have_expected_contracts() -> None:
    assert wechat_price_contract("ww0873") == ("199", "189")
    assert wechat_price_contract("WW0601") == ("199", "189")
    for wechat in ("WW0743", "SL2491", "dy8832", "DY258", "SL0069"):
        assert wechat_price_contract(wechat) == ("268", "258")


def test_system_send_payload_applies_contract_as_last_safety_net() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"code": 0, "data": {"send_status": "sent"}})

    client = OutreachSystemClient(
        Settings(
            OUTREACH_SYSTEM_BASE_URL="https://wecom.test",
            OUTREACH_SYSTEM_TOKEN="test-token",
        )
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def send() -> None:
        await client.send(
            corp_id="corp",
            customer_id="customer",
            external_userid="external",
            user_id="user",
            wechat="ww0873",
            plan_id="plan",
            task_id="task",
            reply_messages=[_text("活动价268元，付10元后再付258元。")],
        )
        await client.aclose()

    asyncio.run(send())

    assert captured["reply_messages"][0]["content"]["text"] == "活动价199元，付10元后再付189元。"

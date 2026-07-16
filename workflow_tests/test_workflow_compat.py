from __future__ import annotations

from app.services.workflow_compat import normalize_workflow_request


def test_workflow_location_payload_preserves_raw_location_fields() -> None:
    request = normalize_workflow_request(
        {
            "url": "http://47.252.81.104/api/ai/chat/workflow-compatible",
            "workflow_id": "xiaobei-default",
            "parameters": {
                "category_id": "S10N",
                "content": {
                    "content": "门店位置：萤火虫大厦",
                    "msgid": "16226699413363463954_1784164155175_external",
                    "msgtime": 1784164151701,
                    "msgtype": "location",
                    "location": "24.535414,118.152077",
                    "location_title": "萤火虫大厦",
                    "location_address": "福建省厦门市湖里区禾山街道岭下社区岐山北二路1000号",
                    "location_zoom": 15,
                },
                "customer_id": "21150538",
                "user_id": "7294",
                "external_userid": "wmanzqsqaawe8ulf6i-waodyh5__pyua",
                "corp_id": "ww943af61cd5d2afe4",
                "wechat": "CS001",
                "messages_count": 10,
            },
        }
    )

    context = request.request_context
    assert request.content == "门店位置：萤火虫大厦"
    assert context["msgtype"] == "location"
    assert context["location"] == "24.535414,118.152077"
    assert context["location_title"] == "萤火虫大厦"
    assert context["location_address"] == "福建省厦门市湖里区禾山街道岭下社区岐山北二路1000号"
    assert context["location_zoom"] == "15"
    assert context["messages_count"] == "10"
    assert context["raw_workflow_payload"]["url"] == "http://47.252.81.104/api/ai/chat/workflow-compatible"
    assert context["raw_workflow_payload"]["parameters"]["content"]["location_title"] == "萤火虫大厦"
    assert (
        context["raw_workflow_payload"]["parameters"]["content"]["location_address"]
        == "福建省厦门市湖里区禾山街道岭下社区岐山北二路1000号"
    )

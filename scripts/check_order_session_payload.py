from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.graph.nodes.reply_context import reply_user_payload_for_model


def main() -> None:
    state = {
        "normalized_content": "好的",
        "customer_basic_info": {
            "city": "深圳",
            "appointment_preference": {"area_or_landmark": "南山科技园"},
        },
        "appointment_cache": {
            "store_name": "深圳南山店",
            "store_id": "store-001",
            "date": "周六",
            "time": "下午3点",
        },
        "fact_envelope": {
            "structured_facts": {
                "store_lookup_status": {
                    "city": "深圳",
                    "area_or_landmark": "南山科技园",
                },
                "recommended_store": {
                    "name": "深圳南山店",
                    "address": "深圳市南山区测试路1号",
                },
                "store_facts": [
                    {"name": "深圳南山店", "address": "深圳市南山区测试路1号"}
                ],
            }
        },
        "conversation_history": [
            {
                "role": "assistant",
                "content": "您在南山科技园附近，我帮您匹配近一点的门店。",
            }
        ],
        "reply_messages": [
            {
                "type": "text",
                "content": {"text": "您在南山科技园附近，我帮您匹配近一点的门店。"},
            }
        ],
    }
    payload = reply_user_payload_for_model(state)
    result = {
        "active_profile_memory": payload.get("memory_usage_policy", {}).get("active_profile_memory"),
        "order_session": payload.get("order_session"),
        "appointment_context": payload.get("appointment_context"),
        "has_store_facts": bool(
            payload.get("fact_envelope", {})
            .get("structured_facts", {})
            .get("store_facts")
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

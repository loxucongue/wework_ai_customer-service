from __future__ import annotations

import asyncio

from app.graph.nodes.layer_nodes import _image_urls_from_state, _merge_image_infos, _understand_image
from app.graph.planner.brain_v2 import _compact_image_info


def test_image_urls_from_state_preserves_merged_images_and_dedupes_current() -> None:
    state = {
        "image_urls": [
            "https://media.example/one.jpg",
            "https://media.example/two.jpg",
        ],
        "file_image": "https://media.example/two.jpg",
    }

    assert _image_urls_from_state(state) == [
        "https://media.example/one.jpg",
        "https://media.example/two.jpg",
    ]


def test_merge_image_infos_keeps_all_visible_facts_for_planner() -> None:
    merged = _merge_image_infos(
        [
            {
                "has_image": True,
                "image_type": "face_skin",
                "image_intent": "face_consult",
                "body_part": "左侧面颊",
                "visible_concerns": ["点状斑点", "肤色不均"],
                "risk_signals": [],
                "extracted_text": [],
                "text_clues": [],
                "image_desc": "左侧面颊可见点状色沉",
                "payment_result": "unclear",
                "payment_amount": None,
                "payment_order_no": "",
                "confidence": 0.9,
            },
            {
                "has_image": True,
                "image_type": "face_skin",
                "image_intent": "face_consult",
                "body_part": "右侧面颊",
                "visible_concerns": ["片状色沉", "肤色不均"],
                "risk_signals": [],
                "extracted_text": [],
                "text_clues": ["持续7至8年"],
                "image_desc": "右侧面颊可见片状色沉",
                "payment_result": "unclear",
                "payment_amount": None,
                "payment_order_no": "",
                "confidence": 0.8,
            },
        ],
        image_count=2,
    )

    assert merged["image_count"] == 2
    assert merged["analyzed_image_count"] == 2
    assert merged["image_type"] == "face_skin"
    assert merged["body_part"] == "左侧面颊、右侧面颊"
    assert merged["visible_concerns"] == ["点状斑点", "肤色不均", "片状色沉"]
    assert len(merged["images"]) == 2

    compact = _compact_image_info(merged)
    assert compact["image_count"] == 2
    assert compact["analyzed_image_count"] == 2
    assert len(compact["images"]) == 2


def test_understand_image_analyzes_each_merged_image() -> None:
    model = _VisionModel()
    image_info, calls = asyncio.run(
        _understand_image(
            {
                "content": "[图片]",
                "normalized_content": "[图片]",
                "conversation_history": ["用户: 7、8年了"],
                "image_urls": [
                    "https://media.example/left.jpg",
                    "https://media.example/right.jpg",
                ],
                "file_image": "https://media.example/right.jpg",
            },
            model,
        )
    )

    assert model.urls == [
        "https://media.example/left.jpg",
        "https://media.example/right.jpg",
    ]
    assert image_info["image_count"] == 2
    assert image_info["analyzed_image_count"] == 2
    assert image_info["visible_concerns"] == ["点状斑点", "片状色沉"]
    assert len(calls) == 2


class _VisionModel:
    available = True
    last_usage: dict[str, object] = {}

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def vision_json(self, *, prompt: str, image_url: str, tier: str, temperature: float) -> dict[str, object]:
        self.urls.append(image_url)
        is_left = image_url.endswith("left.jpg")
        return {
            "info": {
                "has_image": True,
                "image_type": "face_skin",
                "image_intent": "face_consult",
                "body_part": "左侧面颊" if is_left else "右侧面颊",
                "visible_concerns": ["点状斑点"] if is_left else ["片状色沉"],
                "risk_signals": [],
                "extracted_text": [],
                "text_clues": [],
                "image_desc": "左侧图片" if is_left else "右侧图片",
                "payment_result": "unclear",
                "payment_amount": None,
                "payment_order_no": "",
                "confidence": 0.9,
            }
        }

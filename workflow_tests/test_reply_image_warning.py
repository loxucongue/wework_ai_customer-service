from __future__ import annotations

import unittest

from app.graph.nodes.reply_nodes import _filter_unsupported_images


class ReplyImageWarningTests(unittest.TestCase):
    def test_unsupported_image_is_warning_not_error(self) -> None:
        warnings: list[dict] = []
        messages = [
            {"type": "text", "order": 1, "content": {"text": "ok"}},
            {"type": "image", "order": 2, "content": {"url": "https://example.com/not-allowed.png"}},
        ]
        state = {
            "fact_envelope": {
                "structured_facts": {
                    "case_facts": [
                        {"image_url": "https://example.com/allowed.png"},
                    ]
                }
            }
        }

        filtered = _filter_unsupported_images(messages, state, warnings)

        self.assertEqual([item["type"] for item in filtered], ["text"])
        self.assertEqual(warnings[0]["message"], "unsupported_image_removed")

    def test_supported_image_is_kept_without_warning(self) -> None:
        warnings: list[dict] = []
        messages = [
            {"type": "image", "order": 1, "content": {"url": "https://example.com/allowed.png"}},
        ]
        state = {
            "fact_envelope": {
                "structured_facts": {
                    "case_facts": [
                        {"image_url": "https://example.com/allowed.png"},
                    ]
                }
            }
        }

        filtered = _filter_unsupported_images(messages, state, warnings)

        self.assertEqual([item["type"] for item in filtered], ["image"])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()

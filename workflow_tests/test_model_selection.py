import unittest

from app.config import Settings
from app.services import model_selection


class ModelSelectionTests(unittest.TestCase):
    def test_deepseek_is_default_provider_without_legacy_fallbacks(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.model_provider, "deepseek")
        self.assertEqual(settings.model_vision_provider, "aliyun")
        self.assertEqual(settings.deepseek_openai_base_url, "https://api.deepseek.com")
        self.assertEqual(settings.model_fast, "deepseek-v4-flash")
        self.assertEqual(settings.model_balanced, "deepseek-v4-flash")
        self.assertEqual(settings.model_strong, "deepseek-v4-pro")
        self.assertEqual(settings.model_vision, "qwen-vl-plus")
        self.assertEqual(settings.model_fast_fallbacks, "")
        self.assertEqual(settings.model_balanced_fallbacks, "")
        self.assertEqual(settings.model_strong_fallbacks, "")
        self.assertEqual(model_selection.model_names(settings, "balanced"), ["deepseek-v4-flash"])
        self.assertEqual(model_selection.model_names(settings, "vision"), ["qwen-vl-plus"])

    def test_deepseek_provider_uses_deepseek_key_and_base_url(self) -> None:
        settings = Settings(
            _env_file=None,
            model_provider="deepseek",
            deepseek_api_key="test-deepseek-key",
            aliyun_dashscope_api_key="test-aliyun-key",
            volcengine_ark_api_key="test-volc-key",
        )

        self.assertEqual(model_selection.api_key(settings), "test-deepseek-key")
        self.assertEqual(model_selection.base_url(settings), "https://api.deepseek.com")

    def test_vision_tier_can_use_aliyun_provider_while_text_uses_deepseek(self) -> None:
        settings = Settings(
            _env_file=None,
            model_provider="deepseek",
            model_vision_provider="aliyun",
            deepseek_api_key="test-deepseek-key",
            aliyun_dashscope_api_key="test-aliyun-key",
        )

        self.assertEqual(model_selection.provider(settings, "balanced"), "deepseek")
        self.assertEqual(model_selection.api_key(settings, "balanced"), "test-deepseek-key")
        self.assertEqual(model_selection.base_url(settings, "balanced"), "https://api.deepseek.com")
        self.assertEqual(model_selection.provider(settings, "vision"), "aliyun")
        self.assertEqual(model_selection.api_key(settings, "vision"), "test-aliyun-key")
        self.assertEqual(
            model_selection.base_url(settings, "vision"),
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )


if __name__ == "__main__":
    unittest.main()

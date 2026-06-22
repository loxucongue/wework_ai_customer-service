import unittest

from app.config import Settings
from app.services import model_selection


class ModelSelectionTests(unittest.TestCase):
    def test_deepseek_is_default_provider_without_legacy_fallbacks(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.model_provider, "deepseek")
        self.assertEqual(settings.deepseek_openai_base_url, "https://api.deepseek.com")
        self.assertEqual(settings.model_fast, "deepseek-v4-flash")
        self.assertEqual(settings.model_balanced, "deepseek-v4-flash")
        self.assertEqual(settings.model_strong, "deepseek-v4-pro")
        self.assertEqual(settings.model_fast_fallbacks, "")
        self.assertEqual(settings.model_balanced_fallbacks, "")
        self.assertEqual(settings.model_strong_fallbacks, "")
        self.assertEqual(model_selection.model_names(settings, "balanced"), ["deepseek-v4-flash"])

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


if __name__ == "__main__":
    unittest.main()

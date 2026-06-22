from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "ai_paths/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Paths"
    ai_paths_api_key: str = Field(default="", repr=False)
    ai_external_api_key: str = Field(default="", repr=False)
    coze_api_base: str = "https://api.coze.cn"
    coze_oauth_client_id: str = Field(default="", repr=False)
    coze_oauth_public_key_id: str = Field(default="", repr=False)
    coze_oauth_private_key_file: Path | None = Field(default=None, repr=False)
    coze_oauth_token_ttl: int = 7200
    aliyun_dashscope_api_key: str = Field(default="", repr=False)
    volcengine_ark_api_key: str = Field(default="", repr=False)
    deepseek_api_key: str = Field(default="", repr=False)
    model_provider: str = "deepseek"
    model_vision_provider: str = "aliyun"
    aliyun_openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    volcengine_openai_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    deepseek_openai_base_url: str = "https://api.deepseek.com"
    model_fast: str = "deepseek-v4-flash"
    model_balanced: str = "deepseek-v4-flash"
    model_strong: str = "deepseek-v4-pro"
    model_vision: str = "qwen-vl-plus"
    model_fast_fallbacks: str = ""
    model_balanced_fallbacks: str = ""
    model_strong_fallbacks: str = ""
    model_vision_fallbacks: str = ""
    model_timeout_seconds: int = 45
    memory_dir: Path = Path("logs/memory")
    pricing_xlsx_path: Path = Path("projects/public/items_pricing_system.xlsx")
    db_path: Path = Field(default=Path("data/ai_paths.db"), alias="AI_PATHS_DB_PATH")
    platform_agent_base_url: str = "https://v2.henm.cn"
    platform_agent_token: str = Field(default="", repr=False)
    platform_agent_request_from: str = "platform_agent"
    platform_agent_timeout_seconds: int = 12
    platform_agent_default_user_id: int | None = None
    platform_agent_default_corp_id: str = ""
    platform_agent_default_wechat: str = ""

    kb_workflow_id: str = "7644575365759746083"
    pricing_sync_workflow_id: str = "7644090458134609974"

    log_dir: Path = Path("logs/runs")
    trace_log_dir: Path | None = Field(default=None, alias="AI_PATHS_TRACE_LOG_DIR")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

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
    model_provider: str = "aliyun"
    aliyun_openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    volcengine_openai_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model_fast: str = "qwen-turbo"
    model_planner: str = "qwen-plus"
    model_balanced: str = "qwen-plus"
    model_strong: str = "qwen-max"
    model_reply: str = ""
    model_vision: str = "qwen-vl-plus"
    model_fast_fallbacks: str = "kimi-k2.6,qwen3.6-flash"
    model_planner_fallbacks: str = ""
    model_balanced_fallbacks: str = "kimi-k2.6,qwen3.7-max-2026-05-20,qwen3.6-flash"
    model_strong_fallbacks: str = "qwen3.7-max-2026-05-20,kimi-k2.6,qwen-plus"
    model_reply_fallbacks: str = ""
    model_vision_fallbacks: str = ""
    model_timeout_seconds: int = 45
    memory_dir: Path = Path("logs/memory")
    db_path: Path = Field(default=Path("data/ai_paths.db"), alias="AI_PATHS_DB_PATH")
    platform_agent_base_url: str = "https://www.henm.cn"
    platform_agent_token: str = Field(default="", repr=False)
    platform_agent_request_from: str = "platform_agent"
    platform_agent_timeout_seconds: int = 12
    platform_agent_default_user_id: int | None = None
    platform_agent_default_corp_id: str = ""
    platform_agent_default_wechat: str = ""
    store_snapshot_path: Path = Path("data/store_snapshot.json")
    store_snapshot_ttl_hours: int = 24
    outreach_send_base_url: str = Field(default="https://wecom.cs.4ba.cn", alias="OUTREACH_SEND_BASE_URL")
    outreach_send_agent_token: str = Field(default="", alias="OUTREACH_SEND_AGENT_TOKEN", repr=False)
    outreach_send_timeout_seconds: int = Field(default=12, alias="OUTREACH_SEND_TIMEOUT_SECONDS")
    outreach_system_base_url: str = Field(default="https://wecom.cs.4ba.cn", alias="OUTREACH_SYSTEM_BASE_URL")
    outreach_system_token: str = Field(default="", alias="OUTREACH_SYSTEM_TOKEN", repr=False)
    outreach_system_timeout_seconds: int = Field(default=12, alias="OUTREACH_SYSTEM_TIMEOUT_SECONDS")
    debug_platform_context_enabled: bool = Field(default=False, alias="DEBUG_PLATFORM_CONTEXT_ENABLED")
    debug_platform_customer_id: str = Field(default="", alias="DEBUG_PLATFORM_CUSTOMER_ID")
    debug_platform_customer_add_wechat_id: str = Field(default="", alias="DEBUG_PLATFORM_CUSTOMER_ADD_WECHAT_ID")
    debug_platform_external_userid: str = Field(default="", alias="DEBUG_PLATFORM_EXTERNAL_USERID")
    debug_platform_user_id: str = Field(default="", alias="DEBUG_PLATFORM_USER_ID")
    debug_platform_wechat: str = Field(default="", alias="DEBUG_PLATFORM_WECHAT")
    debug_platform_corp_id: str = Field(default="", alias="DEBUG_PLATFORM_CORP_ID")

    kb_workflow_id: str = "7644575365759746083"
    geocode_workflow_id: str = "7654109352189689891"

    log_dir: Path = Path("logs/runs")
    trace_log_dir: Path | None = Field(default=None, alias="AI_PATHS_TRACE_LOG_DIR")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

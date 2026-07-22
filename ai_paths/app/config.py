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
    allow_missing_external_api_key: bool = False
    coze_api_base: str = "https://api.coze.cn"
    coze_oauth_client_id: str = Field(default="", repr=False)
    coze_oauth_public_key_id: str = Field(default="", repr=False)
    coze_oauth_private_key_file: Path | None = Field(default=None, repr=False)
    coze_oauth_token_ttl: int = 7200
    aliyun_dashscope_api_key: str = Field(default="", repr=False)
    volcengine_ark_api_key: str = Field(default="", repr=False)
    model_relay_api_key: str = Field(default="", repr=False)
    claude_relay_api_key: str = Field(default="", repr=False)
    anthropic_auth_token: str = Field(default="", repr=False)
    model_provider: str = "aliyun"
    aliyun_openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    volcengine_openai_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model_relay_base_url: str = ""
    anthropic_base_url: str = ""
    model_relay_protocol: str = "auto"
    anthropic_version: str = "2023-06-01"
    model_max_tokens: int = 4096
    model_response_format_enabled: bool = True
    model_relay_reasoning_control_enabled: bool = True
    model_reasoning_enabled: bool = False
    model_reasoning_effort: str = "low"
    model_reasoning_max_tokens: int = 0
    model_json_reasoning_enabled: bool = False
    model_fast: str = "qwen-plus"
    model_planner: str = "qwen-plus"
    model_balanced: str = "qwen-plus"
    model_strong: str = "qwen-plus"
    model_reply: str = "qwen-plus"
    model_vision: str = "qwen-vl-plus"
    model_fast_fallbacks: str = "qwen-plus,qwen-plus,qwen-turbo"
    model_planner_fallbacks: str = "qwen-plus,qwen-turbo"
    model_balanced_fallbacks: str = "qwen-plus,qwen-plus,qwen-turbo"
    model_strong_fallbacks: str = "qwen-plus,qwen-plus,qwen-turbo"
    model_reply_fallbacks: str = "qwen-plus,qwen-plus,qwen-turbo"
    model_vision_fallbacks: str = ""
    model_timeout_seconds: int = 45
    model_hedge_delay_seconds: float = 5.0
    model_planner_hedge_delay_seconds: float = 5.0
    model_hedge_max_parallel: int = 2
    model_planner_total_timeout_seconds: float = 35.0
    model_reply_total_timeout_seconds: float = 45.0
    model_planner_primary_budget_seconds: float = 25.0
    model_planner_recovery_budget_seconds: float = 10.0
    model_reply_primary_budget_seconds: float = 30.0
    model_reply_recovery_budget_seconds: float = 15.0
    model_vision_total_timeout_seconds: float = 15.0
    model_request_retry_attempts: int = 2
    model_request_retry_delay_seconds: float = 0.5
    sop_event_model_retry_attempts: int = Field(default=3, alias="SOP_EVENT_MODEL_RETRY_ATTEMPTS")
    sop_event_model_retry_delay_seconds: float = Field(default=1.0, alias="SOP_EVENT_MODEL_RETRY_DELAY_SECONDS")
    sop_event_model_attempt_timeout_seconds: float = Field(default=45.0, alias="SOP_EVENT_MODEL_ATTEMPT_TIMEOUT_SECONDS")
    sop_event_model_total_timeout_seconds: float = Field(default=60.0, alias="SOP_EVENT_MODEL_TOTAL_TIMEOUT_SECONDS")
    sop_chat_gate_total_timeout_seconds: float = Field(default=12.0, alias="SOP_CHAT_GATE_TOTAL_TIMEOUT_SECONDS")
    sop_event_model_max_concurrency: int = Field(default=2, alias="SOP_EVENT_MODEL_MAX_CONCURRENCY")
    sop_event_persistent_retry_attempts: int = Field(default=4, alias="SOP_EVENT_PERSISTENT_RETRY_ATTEMPTS")
    sop_event_persistent_retry_base_delay_seconds: float = Field(
        default=30.0,
        alias="SOP_EVENT_PERSISTENT_RETRY_BASE_DELAY_SECONDS",
    )
    sop_event_persistent_retry_max_delay_seconds: float = Field(
        default=300.0,
        alias="SOP_EVENT_PERSISTENT_RETRY_MAX_DELAY_SECONDS",
    )
    sop_event_retry_poll_seconds: float = Field(default=5.0, alias="SOP_EVENT_RETRY_POLL_SECONDS")
    sop_event_retry_batch_size: int = Field(default=5, alias="SOP_EVENT_RETRY_BATCH_SIZE")
    model_json_max_tokens: int = 2048
    model_text_max_tokens: int = 2048
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
    platform_filter_words_path: Path = Path("config/platform_filter_words.json")
    sop_reply_packs_path: Path = Field(default=Path("config/sop_reply_packs.json"), alias="SOP_REPLY_PACKS_PATH")
    precision_qa_playbook_path: Path = Field(
        default=Path("config/precision_qa_playbook.json"),
        alias="PRECISION_QA_PLAYBOOK_PATH",
    )
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
    distance_workflow_id: str = "7647753819456192558"

    log_dir: Path = Path("logs/runs")
    trace_log_dir: Path | None = Field(default=None, alias="AI_PATHS_TRACE_LOG_DIR")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

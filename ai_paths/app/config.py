from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime_roles import RuntimeRole, normalize_runtime_role


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "ai_paths/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Paths"
    service_role: str = Field(default="control", alias="AI_PATHS_SERVICE_ROLE")
    release_id: str = Field(default="development", alias="AI_PATHS_RELEASE_ID")
    build_git_commit: str = Field(default="unknown", alias="AI_PATHS_BUILD_GIT_COMMIT")
    build_dirty: bool = Field(default=False, alias="AI_PATHS_BUILD_DIRTY")
    build_config_revision: str = Field(default="unknown", alias="AI_PATHS_BUILD_CONFIG_REVISION")
    v3_evaluation_dir: Path = Field(
        default=Path(".tmp_runtime/v3_evaluations"),
        alias="V3_EVALUATION_DIR",
    )
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
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY", repr=False)
    claude_relay_api_key: str = Field(default="", repr=False)
    anthropic_auth_token: str = Field(default="", repr=False)
    model_provider: str = "aliyun"
    aliyun_openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    volcengine_openai_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model_relay_base_url: str = ""
    deepseek_openai_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_OPENAI_BASE_URL")
    anthropic_base_url: str = ""
    model_relay_protocol: str = "auto"
    anthropic_version: str = "2023-06-01"
    model_max_tokens: int = 4096
    model_response_format_enabled: bool = True
    model_http_trust_env: bool = True
    model_relay_reasoning_control_enabled: bool = True
    model_reasoning_enabled: bool = False
    model_reasoning_effort: str = "low"
    model_reasoning_max_tokens: int = 0
    model_json_reasoning_enabled: bool = False
    model_fast: str = "gpt-5.4-mini"
    model_planner: str = "gpt-5.4"
    model_balanced: str = "gpt-5.4-mini"
    model_strong: str = "gpt-5.4"
    model_reply: str = "gpt-5.4"
    model_vision: str = "qwen-vl-plus"
    model_store_destination: str = "claude-haiku-4-5-20251001"
    model_fast_fallbacks: str = "gpt-5.4"
    model_planner_fallbacks: str = "gpt-5.4-mini"
    model_balanced_fallbacks: str = "gpt-5.4"
    model_strong_fallbacks: str = "gpt-5.4-mini"
    model_reply_fallbacks: str = "gpt-5.4-mini"
    model_vision_fallbacks: str = ""
    model_store_destination_fallbacks: str = "gpt-5.4,gpt-5.4-mini"
    model_emergency_fallbacks: str = "gpt-5.4,gpt-5.4-mini"
    model_secondary_provider: str = ""
    model_secondary: str = ""
    model_secondary_timeout_seconds: float = 20.0
    model_timeout_seconds: int = 45
    model_hedge_delay_seconds: float = 3.0
    model_planner_hedge_delay_seconds: float = 10.0
    model_reply_hedge_delay_seconds: float = 10.0
    model_hedge_max_parallel: int = 2
    model_planner_total_timeout_seconds: float = 35.0
    model_reply_total_timeout_seconds: float = 45.0
    model_planner_primary_budget_seconds: float = 25.0
    model_planner_recovery_budget_seconds: float = 10.0
    model_reply_primary_budget_seconds: float = 30.0
    model_reply_recovery_budget_seconds: float = 15.0
    model_round_budget_enforced: bool = True
    model_round_timeout_seconds: float = 120.0
    model_strong_round_timeout_seconds: float = 120.0
    model_reply_reserve_seconds: float = 30.0
    model_min_retry_remaining_seconds: float = 8.0
    model_vision_total_timeout_seconds: float = 15.0
    model_store_destination_total_timeout_seconds: float = 25.0
    model_store_destination_hedge_delay_seconds: float = 3.0
    model_request_retry_attempts: int = 2
    model_request_retry_delay_seconds: float = 0.5
    sop_chat_gate_total_timeout_seconds: float = Field(default=25.0, alias="SOP_CHAT_GATE_TOTAL_TIMEOUT_SECONDS")
    reply_model_semantic_routing_enabled: bool = Field(
        default=False,
        alias="REPLY_MODEL_SEMANTIC_ROUTING_ENABLED",
    )
    reply_semantic_contract_enabled: bool = Field(
        default=False,
        alias="REPLY_SEMANTIC_CONTRACT_ENABLED",
    )
    reply_model_payment_sequencing_enabled: bool = Field(
        default=False,
        alias="REPLY_MODEL_PAYMENT_SEQUENCING_ENABLED",
    )
    reply_governance_shadow_mode: bool = Field(
        default=True,
        alias="REPLY_GOVERNANCE_SHADOW_MODE",
    )
    sop_platform_pull_enabled: bool = Field(default=False, alias="SOP_PLATFORM_PULL_ENABLED")
    sop_platform_shadow_mode: bool = Field(default=True, alias="SOP_PLATFORM_SHADOW_MODE")
    sop_platform_base_url: str = Field(
        default="https://test.api.customer.4ba.cn",
        alias="SOP_PLATFORM_BASE_URL",
    )
    sop_platform_token: str = Field(default="", alias="SOP_PLATFORM_TOKEN", repr=False)
    sop_platform_poll_seconds: float = Field(default=10.0, alias="SOP_PLATFORM_POLL_SECONDS")
    sop_platform_lookback_seconds: int = Field(default=604800, alias="SOP_PLATFORM_LOOKBACK_SECONDS")
    sop_platform_window_seconds: int = Field(default=60, alias="SOP_PLATFORM_WINDOW_SECONDS")
    sop_platform_batch_size: int = Field(default=50, alias="SOP_PLATFORM_BATCH_SIZE")
    sop_platform_task_concurrency: int = Field(default=6, alias="SOP_PLATFORM_TASK_CONCURRENCY")
    sop_platform_queue_size: int = Field(default=24, alias="SOP_PLATFORM_QUEUE_SIZE")
    sop_platform_recovery_concurrency: int = Field(default=2, alias="SOP_PLATFORM_RECOVERY_CONCURRENCY")
    sop_platform_timeout_seconds: float = Field(default=12.0, alias="SOP_PLATFORM_TIMEOUT_SECONDS")
    sop_platform_model_timeout_seconds: float = Field(default=20.0, alias="SOP_PLATFORM_MODEL_TIMEOUT_SECONDS")
    sop_platform_decision_model: str = Field(
        default="deepseek-v4-flash",
        alias="SOP_PLATFORM_DECISION_MODEL",
    )
    sop_platform_decision_model_fallbacks: str = Field(
        default="gpt-5.4-mini,gpt-5.4",
        alias="SOP_PLATFORM_DECISION_MODEL_FALLBACKS",
    )
    sop_platform_decision_api_key: str = Field(
        default="",
        alias="SOP_PLATFORM_DECISION_API_KEY",
        repr=False,
    )
    sop_platform_decision_base_url: str = Field(
        default="https://api.deepseek.com",
        alias="SOP_PLATFORM_DECISION_BASE_URL",
    )
    sop_platform_decision_primary_timeout_seconds: float = Field(
        default=12.0,
        alias="SOP_PLATFORM_DECISION_PRIMARY_TIMEOUT_SECONDS",
    )
    sop_platform_recovery_batch_size: int = Field(default=10, alias="SOP_PLATFORM_RECOVERY_BATCH_SIZE")
    sop_platform_max_task_age_seconds: int = Field(default=600, alias="SOP_PLATFORM_MAX_TASK_AGE_SECONDS")
    sop_platform_live_not_before: str = Field(default="", alias="SOP_PLATFORM_LIVE_NOT_BEFORE")
    sop_platform_quiet_hours_enabled: bool = Field(default=False, alias="SOP_PLATFORM_QUIET_HOURS_ENABLED")
    sop_platform_quiet_start_hour: int = Field(default=0, alias="SOP_PLATFORM_QUIET_START_HOUR")
    sop_platform_quiet_end_hour: int = Field(default=8, alias="SOP_PLATFORM_QUIET_END_HOUR")
    sop_platform_quiet_first_add_grace_minutes: int = Field(
        default=30,
        alias="SOP_PLATFORM_QUIET_FIRST_ADD_GRACE_MINUTES",
    )
    model_json_max_tokens: int = 2048
    model_text_max_tokens: int = 2048
    memory_dir: Path = Path("logs/memory")
    db_path: Path = Field(default=Path("data/ai_paths.db"), alias="AI_PATHS_DB_PATH")
    aics_storage_backend: str = Field(default="sqlite", alias="AICS_STORAGE_BACKEND")
    aics_mysql_host: str = Field(default="", alias="AICS_MYSQL_HOST")
    aics_mysql_port: int = Field(default=3306, alias="AICS_MYSQL_PORT")
    aics_mysql_database: str = Field(default="wecom_cs", alias="AICS_MYSQL_DATABASE")
    aics_mysql_user: str = Field(default="", alias="AICS_MYSQL_USER")
    aics_mysql_password: str = Field(default="", alias="AICS_MYSQL_PASSWORD", repr=False)
    aics_mysql_ssl_required: bool = Field(default=True, alias="AICS_MYSQL_SSL_REQUIRED")
    aics_mysql_ssl_ca: str = Field(default="", alias="AICS_MYSQL_SSL_CA")
    aics_mysql_pool_size: int = Field(default=5, alias="AICS_MYSQL_POOL_SIZE")
    aics_mysql_max_overflow: int = Field(default=5, alias="AICS_MYSQL_MAX_OVERFLOW")
    aics_mysql_connect_timeout_seconds: int = Field(default=10, alias="AICS_MYSQL_CONNECT_TIMEOUT_SECONDS")
    aics_mysql_read_timeout_seconds: int = Field(default=15, alias="AICS_MYSQL_READ_TIMEOUT_SECONDS")
    aics_mysql_write_timeout_seconds: int = Field(default=15, alias="AICS_MYSQL_WRITE_TIMEOUT_SECONDS")
    aics_table_prefix: str = Field(default="aics_", alias="AICS_TABLE_PREFIX")
    aics_sqlite_mirror_enabled: bool = Field(default=False, alias="AICS_SQLITE_MIRROR_ENABLED")
    aics_trace_retention_days: int = Field(default=14, alias="AICS_TRACE_RETENTION_DAYS")
    aics_run_retention_days: int = Field(default=90, alias="AICS_RUN_RETENTION_DAYS")
    platform_agent_base_url: str = "https://www.henm.cn"
    platform_agent_token: str = Field(default="", repr=False)
    platform_agent_request_from: str = "platform_agent"
    platform_agent_timeout_seconds: int = 12
    platform_agent_default_user_id: int | None = None
    platform_agent_default_corp_id: str = ""
    platform_agent_default_wechat: str = ""
    store_snapshot_path: Path = Path("data/store_snapshot.json")
    store_snapshot_ttl_hours: int = 24
    store_snapshot_refresh_enabled: bool = True
    store_snapshot_refresh_interval_seconds: int = 24 * 60 * 60
    store_snapshot_refresh_user_id: int | None = None
    store_snapshot_refresh_corp_id: str = ""
    store_snapshot_refresh_wechat: str = ""
    platform_filter_words_path: Path = Path("config/platform_filter_words.json")
    sop_reply_packs_path: Path = Field(default=Path("config/sop_reply_packs.json"), alias="SOP_REPLY_PACKS_PATH")
    sop_reply_packs_overlay_path: Path | None = Field(default=None, alias="SOP_REPLY_PACKS_OVERLAY_PATH")
    precision_qa_playbook_path: Path = Field(
        default=Path("config/precision_qa_playbook.json"),
        alias="PRECISION_QA_PLAYBOOK_PATH",
    )
    ai_sales_policy_path: Path = Field(
        default=Path("app/policies/ai_sales_policy_v1.json"),
        alias="AI_SALES_POLICY_PATH",
    )
    ai_sales_policy_enabled: bool = Field(default=False, alias="AI_SALES_POLICY_ENABLED")
    sales_strategy_catalog_path: Path = Field(
        default=Path("app/policies/sales_strategy_catalog_v1.json"),
        alias="SALES_STRATEGY_CATALOG_PATH",
    )
    sales_strategy_catalog_enabled: bool = Field(default=False, alias="SALES_STRATEGY_CATALOG_ENABLED")
    model_led_objection_playbook_path: Path = Field(
        default=Path("config/model_led_objection_playbook.json"),
        alias="V2_MODEL_LED_OBJECTION_PLAYBOOK_PATH",
    )
    sales_recall_enabled: bool = Field(default=True, alias="V2_SALES_RECALL_ENABLED")
    sales_recall_workflow_id: str = Field(default="7672999254608347179", alias="V2_SALES_RECALL_WORKFLOW_ID")
    sales_recall_wait_seconds: float = Field(default=2.5, alias="V2_SALES_RECALL_WAIT_SECONDS")
    sales_recall_max_candidates: int = Field(default=3, alias="V2_SALES_RECALL_MAX_CANDIDATES")
    follow_knowledge_enabled: bool = Field(default=True, alias="FOLLOW_KNOWLEDGE_ENABLED")
    follow_knowledge_base_url: str = Field(default="https://test.api.customer.4ba.cn", alias="FOLLOW_KNOWLEDGE_BASE_URL")
    follow_knowledge_token: str = Field(default="", alias="FOLLOW_KNOWLEDGE_TOKEN", repr=False)
    follow_knowledge_timeout_seconds: float = Field(default=4.0, alias="FOLLOW_KNOWLEDGE_TIMEOUT_SECONDS")
    follow_knowledge_cache_ttl_seconds: float = Field(default=60.0, alias="FOLLOW_KNOWLEDGE_CACHE_TTL_SECONDS")
    service_rule_data_enabled: bool = Field(default=False, alias="SERVICE_RULE_DATA_ENABLED")
    service_rule_data_base_url: str = Field(default="https://test.api.customer.4ba.cn", alias="SERVICE_RULE_DATA_BASE_URL")
    service_rule_data_token: str = Field(default="", alias="SERVICE_RULE_DATA_TOKEN", repr=False)
    service_rule_data_timeout_seconds: float = Field(default=6.0, alias="SERVICE_RULE_DATA_TIMEOUT_SECONDS")
    service_rule_data_poll_seconds: float = Field(default=2.0, alias="SERVICE_RULE_DATA_POLL_SECONDS")
    service_rule_data_batch_size: int = Field(default=10, alias="SERVICE_RULE_DATA_BATCH_SIZE")
    service_rule_data_max_attempts: int = Field(default=6, alias="SERVICE_RULE_DATA_MAX_ATTEMPTS")
    service_rule_data_retry_base_seconds: float = Field(default=10.0, alias="SERVICE_RULE_DATA_RETRY_BASE_SECONDS")
    deepseek_api_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_API_BASE_URL")
    deepseek_semantic_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_SEMANTIC_MODEL")
    deepseek_semantic_timeout_seconds: float = Field(default=10.0, alias="DEEPSEEK_SEMANTIC_TIMEOUT_SECONDS")
    deepseek_semantic_max_tokens: int = Field(default=800, alias="DEEPSEEK_SEMANTIC_MAX_TOKENS")
    deepseek_semantic_script_threshold: int = Field(default=12, alias="DEEPSEEK_SEMANTIC_SCRIPT_THRESHOLD")
    deepseek_semantic_max_scripts: int = Field(default=6, alias="DEEPSEEK_SEMANTIC_MAX_SCRIPTS")
    sop_objection_materials_path: Path = Field(
        default=Path("config/sop_objection_materials.json"),
        alias="SOP_OBJECTION_MATERIALS_PATH",
    )
    outreach_send_base_url: str = Field(default="https://wecom.cs.4ba.cn", alias="OUTREACH_SEND_BASE_URL")
    outreach_send_agent_token: str = Field(default="", alias="OUTREACH_SEND_AGENT_TOKEN", repr=False)
    outreach_send_timeout_seconds: int = Field(default=12, alias="OUTREACH_SEND_TIMEOUT_SECONDS")
    outreach_system_base_url: str = Field(default="https://wecom.cs.4ba.cn", alias="OUTREACH_SYSTEM_BASE_URL")
    outreach_system_token: str = Field(default="", alias="OUTREACH_SYSTEM_TOKEN", repr=False)
    outreach_system_timeout_seconds: int = Field(default=12, alias="OUTREACH_SYSTEM_TIMEOUT_SECONDS")
    outreach_system_send_conversation_id_enabled: bool = Field(
        default=False,
        alias="OUTREACH_SYSTEM_SEND_CONVERSATION_ID_ENABLED",
    )
    background_workers_enabled: bool = Field(default=False, alias="AI_PATHS_BACKGROUND_WORKERS_ENABLED")
    message_delivery_callback_required: bool = Field(
        default=False,
        alias="MESSAGE_DELIVERY_CALLBACK_REQUIRED",
    )
    message_delivery_callback_public_url: str = Field(
        default="",
        alias="MESSAGE_DELIVERY_CALLBACK_PUBLIC_URL",
    )
    message_delivery_callback_token: str = Field(
        default="",
        alias="MESSAGE_DELIVERY_CALLBACK_TOKEN",
        repr=False,
    )
    outreach_auto_send_poll_seconds: float = Field(default=5.0, alias="OUTREACH_AUTO_SEND_POLL_SECONDS")
    outreach_auto_send_batch_size: int = Field(default=20, alias="OUTREACH_AUTO_SEND_BATCH_SIZE")
    outreach_before_send_retry_seconds: int = Field(default=60, alias="OUTREACH_BEFORE_SEND_RETRY_SECONDS")
    outreach_plan_monitor_poll_seconds: float = Field(default=60.0, alias="OUTREACH_PLAN_MONITOR_POLL_SECONDS")
    outreach_plan_monitor_batch_size: int = Field(default=5, alias="OUTREACH_PLAN_MONITOR_BATCH_SIZE")
    outreach_plan_monitor_auto_activate: bool = Field(
        default=True,
        alias="OUTREACH_PLAN_MONITOR_AUTO_ACTIVATE",
    )
    outreach_first_day_silence_enabled: bool = Field(default=False, alias="OUTREACH_FIRST_DAY_SILENCE_ENABLED")
    outreach_first_day_silence_minutes: int = Field(default=3, alias="OUTREACH_FIRST_DAY_SILENCE_MINUTES")
    outreach_first_day_wechat_allowlist: str = Field(default="", alias="OUTREACH_FIRST_DAY_WECHAT_ALLOWLIST")
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
    audio_to_text_workflow_id: str = Field(
        default="7664438534082789417",
        alias="AUDIO_TO_TEXT_WORKFLOW_ID",
    )
    doubao_asr_api_key: str = Field(default="", alias="DOUBAO_ASR_API_KEY", repr=False)
    doubao_asr_app_key: str = Field(default="", alias="DOUBAO_ASR_APP_KEY", repr=False)
    doubao_asr_access_key: str = Field(default="", alias="DOUBAO_ASR_ACCESS_KEY", repr=False)
    doubao_asr_secret_key: str = Field(default="", alias="DOUBAO_ASR_SECRET_KEY", repr=False)
    doubao_asr_resource_id: str = Field(default="volc.seedasr.auc", alias="DOUBAO_ASR_RESOURCE_ID")
    doubao_asr_submit_url: str = Field(
        default="https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit",
        alias="DOUBAO_ASR_SUBMIT_URL",
    )
    doubao_asr_query_url: str = Field(
        default="https://openspeech.bytedance.com/api/v3/auc/bigmodel/query",
        alias="DOUBAO_ASR_QUERY_URL",
    )
    doubao_asr_timeout_seconds: float = Field(default=15.0, alias="DOUBAO_ASR_TIMEOUT_SECONDS")
    doubao_asr_poll_interval_seconds: float = Field(default=1.0, alias="DOUBAO_ASR_POLL_INTERVAL_SECONDS")
    doubao_asr_poll_attempts: int = Field(default=8, alias="DOUBAO_ASR_POLL_ATTEMPTS")
    platform_voice_batch_enabled: bool = Field(default=True, alias="PLATFORM_VOICE_BATCH_ENABLED")
    platform_voice_batch_settle_seconds: float = Field(default=1.2, alias="PLATFORM_VOICE_BATCH_SETTLE_SECONDS")
    platform_voice_batch_hard_window_seconds: float = Field(
        default=4.0,
        alias="PLATFORM_VOICE_BATCH_HARD_WINDOW_SECONDS",
    )
    platform_voice_batch_timeout_seconds: float = Field(
        default=15.0,
        alias="PLATFORM_VOICE_BATCH_TIMEOUT_SECONDS",
    )
    platform_voice_batch_max_items: int = Field(default=6, alias="PLATFORM_VOICE_BATCH_MAX_ITEMS")
    platform_voice_transcript_cache_seconds: float = Field(
        default=900.0,
        alias="PLATFORM_VOICE_TRANSCRIPT_CACHE_SECONDS",
    )

    log_dir: Path = Path("logs/runs")
    trace_log_dir: Path | None = Field(default=None, alias="AI_PATHS_TRACE_LOG_DIR")

    @property
    def runtime_role(self) -> RuntimeRole:
        return normalize_runtime_role(self.service_role)

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> "Settings":
        role = self.runtime_role
        if role is RuntimeRole.REPLY and self.background_workers_enabled:
            raise ValueError("reply role requires AI_PATHS_BACKGROUND_WORKERS_ENABLED=false")
        if role is RuntimeRole.CONTROL and self.background_workers_enabled:
            raise ValueError("control role requires AI_PATHS_BACKGROUND_WORKERS_ENABLED=false")
        if role is RuntimeRole.WORKER and not self.background_workers_enabled:
            raise ValueError("worker role requires AI_PATHS_BACKGROUND_WORKERS_ENABLED=true")
        if self.message_delivery_callback_required and not str(self.message_delivery_callback_token or "").strip():
            raise ValueError("MESSAGE_DELIVERY_CALLBACK_REQUIRED=true requires MESSAGE_DELIVERY_CALLBACK_TOKEN")
        if self.sop_platform_pull_enabled:
            if role is not RuntimeRole.WORKER:
                raise ValueError("SOP_PLATFORM_PULL_ENABLED=true is only valid for the worker role")
            if not str(self.sop_platform_token or "").strip():
                raise ValueError("SOP_PLATFORM_PULL_ENABLED=true requires SOP_PLATFORM_TOKEN")
            if not str(self.sop_platform_base_url or "").strip():
                raise ValueError("SOP_PLATFORM_PULL_ENABLED=true requires SOP_PLATFORM_BASE_URL")
            if not str(self.outreach_system_base_url or "").strip() or not str(self.outreach_system_token or "").strip():
                raise ValueError("SOP_PLATFORM_PULL_ENABLED=true requires OUTREACH_SYSTEM_BASE_URL and OUTREACH_SYSTEM_TOKEN")
        if role is RuntimeRole.WORKER and self.service_rule_data_enabled:
            if not str(self.service_rule_data_base_url or "").strip() or not str(self.service_rule_data_token or "").strip():
                raise ValueError("SERVICE_RULE_DATA_ENABLED=true requires SERVICE_RULE_DATA_BASE_URL and SERVICE_RULE_DATA_TOKEN for worker")
        if role is RuntimeRole.WORKER and self.outreach_first_day_silence_enabled:
            if not str(self.outreach_system_base_url or "").strip() or not str(self.outreach_system_token or "").strip():
                raise ValueError("OUTREACH_FIRST_DAY_SILENCE_ENABLED=true requires OUTREACH_SYSTEM_BASE_URL and OUTREACH_SYSTEM_TOKEN for worker")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

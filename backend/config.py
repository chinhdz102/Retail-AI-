from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_GROQ_CHAT_OVERRIDE_FIELDS = frozenset(
    {
        "groq_base_url",
        "groq_api_key",
        "groq_api_key_fallback_1",
        "groq_api_key_fallback_2",
        "groq_api_key_fallback_3",
        "groq_api_key_fallback_4",
        "groq_model",
        "groq_fallback_model",
        "groq_fallback_model_2",
        "groq_consultation_review_model",
    }
)
_GROQ_FALLBACK_KEY_FIELDS = (
    "groq_api_key_fallback_1",
    "groq_api_key_fallback_2",
    "groq_api_key_fallback_3",
    "groq_api_key_fallback_4",
)
_CHAT_LLM_FALLBACK_KEY_FIELDS = (
    "chat_llm_api_key_fallback_1",
    "chat_llm_api_key_fallback_2",
    "chat_llm_api_key_fallback_3",
    "chat_llm_api_key_fallback_4",
)
_CHAT_LLM_SECRET_FIELDS = frozenset({"chat_llm_api_key", *_CHAT_LLM_FALLBACK_KEY_FIELDS})
_CHAT_LLM_FIELDS = frozenset(
    {
        "chat_llm_base_url",
        "chat_llm_api_key",
        *_CHAT_LLM_FALLBACK_KEY_FIELDS,
        "chat_llm_model",
        "chat_llm_fallback_model",
        "chat_llm_fallback_model_2",
        "chat_llm_consultation_review_model",
    }
)


class Settings(BaseSettings):
    def __init__(self, **values: object) -> None:
        passed = {key.lower() for key in values}
        if (_GROQ_CHAT_OVERRIDE_FIELDS & passed) and not (_CHAT_LLM_FIELDS & passed):
            for field_name in _CHAT_LLM_FIELDS:
                if field_name not in passed:
                    values[field_name] = (
                        SecretStr("") if field_name in _CHAT_LLM_SECRET_FIELDS else ""
                    )
        if "chat_llm_api_key" in passed:
            for field_name in _CHAT_LLM_FALLBACK_KEY_FIELDS:
                if field_name not in passed:
                    values[field_name] = SecretStr("")
        if "groq_api_key" in passed:
            for field_name in _GROQ_FALLBACK_KEY_FIELDS:
                if field_name not in passed:
                    values[field_name] = SecretStr("")
        super().__init__(**values)
    app_env: str = "development"
    app_version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://retail:retail@localhost:5432/retail"
    catalog_repository: Literal["demo", "postgres", "snapshot"] = "demo"
    catalog_snapshot_path: str = "data/thanh_lam_products.json"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 1800
    catalog_tool_workers: int = Field(default=8, ge=1, le=64)
    catalog_tool_max_in_flight: int = Field(default=16, ge=1, le=256)
    postgres_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    postgres_command_timeout_seconds: float = Field(default=2.5, gt=0, le=60)
    demo_sales_enabled: bool = False
    demo_sales_database_path: str = "data/demo_sales.sqlite3"
    # Experimental dialogue policy: opt in locally until live review passes.
    customer_consultation_enabled: bool = False
    customer_consultation_review_enabled: bool = False
    customer_consultation_review_high_risk_only: bool = True
    # A deterministic, catalog-only degraded mode for the local demo. It may
    # publish only typed price/category/size facts after the provider is down;
    # production keeps it off until it has a separate acceptance review.
    customer_consultation_local_fallback_enabled: bool = False
    # Opt-in low-latency optimizations:
    # - stream_llm_use_groq_lpu: route lean plain-text SSE streaming & micro-prompts
    #   through Groq LPU first (~300-500ms TTFT, free tier) with seamless failover
    #   to the configured dedicated chat LLM.
    # - consultation_cache_enabled: bounded TTL semantic/normalized-intent cache
    #   for stateless Turn-1 catalog consultations (zero extra tokens on repeats).
    stream_llm_use_groq_lpu: bool = False
    consultation_cache_enabled: bool = True

    chat_llm_base_url: str = ""
    chat_llm_api_key: SecretStr = SecretStr("")
    chat_llm_api_key_fallback_1: SecretStr = SecretStr("")
    chat_llm_api_key_fallback_2: SecretStr = SecretStr("")
    chat_llm_api_key_fallback_3: SecretStr = SecretStr("")
    chat_llm_api_key_fallback_4: SecretStr = SecretStr("")
    chat_llm_model: str = ""
    chat_llm_fallback_model: str = ""
    chat_llm_fallback_model_2: str = ""
    chat_llm_consultation_review_model: str = ""

    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: SecretStr = SecretStr("")
    groq_api_key_fallback_1: SecretStr = SecretStr("")
    groq_api_key_fallback_2: SecretStr = SecretStr("")
    groq_api_key_fallback_3: SecretStr = SecretStr("")
    groq_api_key_fallback_4: SecretStr = SecretStr("")
    groq_model: str = "qwen/qwen3.8-27b"
    groq_fallback_model: str = "openai/gpt-oss-120b"
    groq_fallback_model_2: str = "openai/gpt-oss-20b"
    groq_structured_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    groq_stt_model: str = "whisper-large-v3"
    groq_stt_fallback_model: str = "whisper-large-v3-turbo"
    vietnamese_asr_backend: Literal["groq", "zipformer"] = "groq"
    vietnamese_asr_model_dir: str = ""
    vietnamese_asr_verify_uncertain: bool = False
    vietnamese_asr_decoding: Literal["greedy_search", "modified_beam_search"] = "greedy_search"
    vietnamese_stt_model_dir: str = "data/models/gipformer1.5-68M-rnnt"
    vietnamese_stt_timeout_seconds: float = Field(default=15.0, gt=0, le=45)
    vietnamese_stt_max_audio_seconds: float = Field(default=15.0, gt=0, le=60)
    groq_consultation_review_model: str = "openai/gpt-oss-120b"
    groq_timeout_seconds: float = 45.0
    groq_chat_attempt_timeout_seconds: float = Field(default=12.0, gt=0, le=45)
    groq_stt_total_timeout_seconds: float = 20.0
    groq_stt_attempt_timeout_seconds: float = Field(default=20.0, gt=0, le=45)

    azure_speech_key: SecretStr = SecretStr("")
    azure_speech_region: str = ""
    azure_speech_timeout_seconds: float = 20.0
    stt_provider: Literal["groq", "azure", "local"] = "groq"
    azure_stt_use_sdk: bool = True
    speech_denoiser_enabled: bool = True
    speech_denoiser_model_path: str = "data/models/speech-enhancement/dpdfnet2.onnx"

    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_model: str = "eleven_flash_v2_5"
    elevenlabs_output_format: str = "mp3_44100_64"
    elevenlabs_speed: float = 0.88
    elevenlabs_vi_voice_name: str = "Giang - Northern female Narrator"
    elevenlabs_vi_voice_id: str = ""
    elevenlabs_vi_fallback_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
    elevenlabs_en_voice_name: str = "Sarah - Mature, Reassuring, Confident"
    elevenlabs_en_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
    elevenlabs_zh_voice_name: str = "River - Relaxed, Neutral, Informative"
    elevenlabs_zh_voice_id: str = "SAz9YHcvj6GT2YYXdXww"
    elevenlabs_timeout_seconds: float = 45.0

    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:5174,http://127.0.0.1:5174"
    )

    # Manager escalation notifications (priority: Zalo -> Messenger -> Gmail).
    store_name: str = "Thanh Lam Mart"
    manager_notify_priority: str = "zalo_bot,zalo_oa,zalo_webhook,messenger,gmail"
    manager_notify_timeout_seconds: float = Field(default=8.0, gt=0, le=30)

    # 1. Zalo Bot Platform (bot.zaloplatforms.com) — fastest setup for personal Zalo accounts.
    zalo_bot_token: SecretStr = SecretStr("")
    zalo_bot_chat_id: str = ""
    zalo_bot_api_base: str = "https://bot-api.zaloplatforms.com"

    # 2. Zalo Official Account (OA) OpenAPI v3.0 (optional OAuth v4 auto-refresh).
    zalo_oa_access_token: SecretStr = SecretStr("")
    zalo_oa_refresh_token: SecretStr = SecretStr("")
    zalo_oa_app_id: str = ""
    zalo_oa_secret_key: SecretStr = SecretStr("")
    zalo_oa_manager_user_id: str = ""

    # 3. Zalo Custom Webhook / Relay (e.g. n8n, Make, self-hosted Zalo bridge).
    zalo_webhook_url: str = ""
    zalo_webhook_secret: SecretStr = SecretStr("")

    # 4. Facebook Messenger Platform Send API.
    messenger_page_access_token: SecretStr = SecretStr("")
    messenger_manager_psid: str = ""
    messenger_api_version: str = "v21.0"

    # 5. Gmail SMTP fallback (uses Google App Password).
    manager_gmail_sender: str = ""
    manager_gmail_app_password: SecretStr = SecretStr("")
    manager_gmail_to: str = ""
    manager_gmail_smtp_host: str = "smtp.gmail.com"
    manager_gmail_smtp_port: int = Field(default=465, ge=1, le=65535)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def has_groq_key(self) -> bool:
        return bool(self.groq_api_credentials)

    @property
    def has_chat_llm_key(self) -> bool:
        return bool(self.chat_api_credentials)

    @property
    def uses_dedicated_chat_llm(self) -> bool:
        return bool(
            self.chat_llm_api_key.get_secret_value().strip()
            or self.chat_llm_base_url.strip()
            or self.chat_llm_model.strip()
        )

    @property
    def effective_chat_base_url(self) -> str:
        return self.chat_llm_base_url.strip() or self.groq_base_url

    @property
    def effective_chat_model(self) -> str:
        return self.chat_llm_model.strip() or self.groq_model

    @property
    def effective_chat_fallback_model(self) -> str:
        if self.uses_dedicated_chat_llm:
            return self.chat_llm_fallback_model.strip()
        return self.groq_fallback_model

    @property
    def effective_chat_fallback_model_2(self) -> str:
        if self.uses_dedicated_chat_llm:
            return self.chat_llm_fallback_model_2.strip()
        return self.groq_fallback_model_2

    @property
    def effective_consultation_review_model(self) -> str:
        if self.uses_dedicated_chat_llm:
            return (
                self.chat_llm_consultation_review_model.strip()
                or self.effective_chat_fallback_model
                or self.effective_chat_model
            )
        return self.groq_consultation_review_model

    @property
    def chat_api_credentials(self) -> tuple[tuple[str, str], ...]:
        """Dedicated chat-LLM credentials (primary + fallbacks) when configured, else Groq pool."""
        configured = (
            ("chat_primary", self.chat_llm_api_key),
            ("chat_fallback_1", self.chat_llm_api_key_fallback_1),
            ("chat_fallback_2", self.chat_llm_api_key_fallback_2),
            ("chat_fallback_3", self.chat_llm_api_key_fallback_3),
            ("chat_fallback_4", self.chat_llm_api_key_fallback_4),
        )
        credentials: list[tuple[str, str]] = []
        seen: set[str] = set()
        for slot, secret in configured:
            value = secret.get_secret_value().strip()
            if value and value not in seen:
                credentials.append((slot, value))
                seen.add(value)
        if credentials:
            return tuple(credentials)
        return self.groq_api_credentials

    @property
    def groq_api_credentials(self) -> tuple[tuple[str, str], ...]:
        """Configured Groq credentials in primary-to-fallback order."""
        configured = (
            ("primary", self.groq_api_key),
            ("fallback_1", self.groq_api_key_fallback_1),
            ("fallback_2", self.groq_api_key_fallback_2),
            ("fallback_3", self.groq_api_key_fallback_3),
            ("fallback_4", self.groq_api_key_fallback_4),
        )
        credentials: list[tuple[str, str]] = []
        seen: set[str] = set()
        for slot, secret in configured:
            value = secret.get_secret_value().strip()
            if value and value not in seen:
                credentials.append((slot, value))
                seen.add(value)
        return tuple(credentials)

    @property
    def has_elevenlabs_key(self) -> bool:
        return bool(self.elevenlabs_api_key.get_secret_value().strip())

    @property
    def has_azure_speech(self) -> bool:
        return bool(
            self.azure_speech_key.get_secret_value().strip()
            and self.azure_speech_region.strip()
        )

    @property
    def has_zalo_bot(self) -> bool:
        return bool(
            self.zalo_bot_token.get_secret_value().strip() and self.zalo_bot_chat_id.strip()
        )

    @property
    def has_zalo_oa(self) -> bool:
        has_token = bool(self.zalo_oa_access_token.get_secret_value().strip())
        has_refresh = bool(
            self.zalo_oa_refresh_token.get_secret_value().strip()
            and self.zalo_oa_app_id.strip()
            and self.zalo_oa_secret_key.get_secret_value().strip()
        )
        return bool((has_token or has_refresh) and self.zalo_oa_manager_user_id.strip())

    @property
    def has_zalo_webhook(self) -> bool:
        return bool(self.zalo_webhook_url.strip())

    @property
    def has_messenger_notifier(self) -> bool:
        return bool(
            self.messenger_page_access_token.get_secret_value().strip()
            and self.messenger_manager_psid.strip()
        )

    @property
    def has_gmail_notifier(self) -> bool:
        return bool(
            self.manager_gmail_sender.strip()
            and self.manager_gmail_app_password.get_secret_value().strip()
            and (self.manager_gmail_to.strip() or self.manager_gmail_sender.strip())
        )

    @property
    def manager_notify_order(self) -> tuple[str, ...]:
        valid = {"zalo_bot", "zalo_oa", "zalo_webhook", "messenger", "gmail"}
        ordered: list[str] = []
        for raw in self.manager_notify_priority.split(","):
            key = raw.strip().lower()
            if key in valid and key not in ordered:
                ordered.append(key)
        for fallback in ("zalo_bot", "zalo_oa", "zalo_webhook", "messenger", "gmail"):
            if fallback not in ordered:
                ordered.append(fallback)
        return tuple(ordered)

    @property
    def configured_manager_notify_channels(self) -> tuple[str, ...]:
        availability = {
            "zalo_bot": self.has_zalo_bot,
            "zalo_oa": self.has_zalo_oa,
            "zalo_webhook": self.has_zalo_webhook,
            "messenger": self.has_messenger_notifier,
            "gmail": self.has_gmail_notifier,
        }
        return tuple(name for name in self.manager_notify_order if availability.get(name))

    @property
    def has_manager_notifier(self) -> bool:
        return bool(self.configured_manager_notify_channels)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

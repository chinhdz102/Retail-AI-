from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://retail:retail@localhost:5432/retail"
    catalog_repository: Literal["demo", "postgres", "snapshot"] = "demo"
    catalog_snapshot_path: str = "data/thanh_lam_products.json"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 1800

    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "qwen/qwen3.8-27b"
    groq_fallback_model: str = "openai/gpt-oss-120b"
    groq_fallback_model_2: str = "openai/gpt-oss-20b"
    groq_timeout_seconds: float = 45.0
    groq_strict_outputs: bool = False
    groq_api_key_fallback_1: SecretStr = SecretStr("")
    groq_api_key_fallback_2: SecretStr = SecretStr("")
    groq_api_key_fallback_3: SecretStr = SecretStr("")
    groq_api_key_fallback_4: SecretStr = SecretStr("")
    groq_api_key_fallback_5: SecretStr = SecretStr("")
    whisper_timeout_seconds: float = 25.0
    stt_provider: Literal["groq", "azure"] = "groq"
    stt_debug_capture: bool = False

    azure_speech_key: SecretStr = SecretStr("")
    azure_speech_region: str = ""
    azure_speech_timeout_seconds: float = 20.0

    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_model: str = "eleven_flash_v2_5"
    elevenlabs_output_format: str = "mp3_44100_64"
    elevenlabs_speed: float = 0.88
    elevenlabs_vi_voice_name: str = "Giang - Northern female Narrator"
    elevenlabs_vi_voice_id: str = ""
    elevenlabs_vi_fallback_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
    elevenlabs_timeout_seconds: float = 45.0

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

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
        return bool(self.groq_api_key.get_secret_value().strip())

    @property
    def has_elevenlabs_key(self) -> bool:
        return bool(self.elevenlabs_api_key.get_secret_value().strip())

    @property
    def has_azure_speech(self) -> bool:
        return bool(
            self.azure_speech_key.get_secret_value().strip()
            and self.azure_speech_region.strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

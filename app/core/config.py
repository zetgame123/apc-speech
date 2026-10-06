from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application Profile
    app_profile: str = Field(default="small", alias="APP_PROFILE")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    debug: bool = Field(default=False, alias="DEBUG")
    build_version: str = Field(default="0.1.0", alias="BUILD_VERSION")

    # HTTP Server
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    workers: int = Field(default=1, alias="WORKERS")

    # Auth & Security
    # Format: token:scope:namespace (comma-separated), or plain tokens in dev mode
    api_tokens_hashed: str = Field(default="", alias="API_TOKENS_HASHED")
    dev_token: str | None = Field(default="dev-token", alias="DEV_TOKEN")
    callback_signing_secret: str = Field(
        default="dev-secret-change-me-in-production-32chars!!", alias="CALLBACK_SIGNING_SECRET"
    )

    # CORS — comma-separated list of allowed origins, "*" allows all (dev only)
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000", alias="CORS_ALLOWED_ORIGINS"
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://apc_user:secure_pass@localhost:5432/apc_speech",
        alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, alias="DB_MAX_OVERFLOW")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_max_connections: int = Field(default=20, alias="REDIS_MAX_CONNECTIONS")

    # MinIO / S3
    s3_endpoint_url: str = Field(default="http://localhost:9000", alias="S3_ENDPOINT_URL")
    s3_access_key: str = Field(default="minio_admin", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="minio_secret_pass", alias="S3_SECRET_KEY")
    s3_bucket_audio: str = Field(default="raw-audio", alias="S3_BUCKET_AUDIO")
    s3_bucket_exports: str = Field(default="exports", alias="S3_BUCKET_EXPORTS")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")

    # ASR & Audio
    asr_model_name: str = Field(default="small", alias="ASR_MODEL_NAME")
    asr_compute_type: str = Field(default="int8", alias="ASR_COMPUTE_TYPE")
    asr_device: str = Field(default="cpu", alias="ASR_DEVICE")
    asr_threads: int = Field(default=2, alias="ASR_THREADS")
    vad_threshold: float = Field(default=0.5, alias="VAD_THRESHOLD")
    max_audio_duration_hours: int = Field(default=3, alias="MAX_AUDIO_DURATION_HOURS")
    max_upload_size_mb: int = Field(default=500, alias="MAX_UPLOAD_SIZE_MB")

    # Voice Search
    voice_search_max_duration_sec: int = Field(default=60, alias="VOICE_SEARCH_MAX_DURATION_SEC")
    voice_search_model_name: str = Field(default="base", alias="VOICE_SEARCH_MODEL_NAME")
    voice_search_beam_size: int = Field(default=2, alias="VOICE_SEARCH_BEAM_SIZE")
    voice_search_max_concurrency: int = Field(default=1, alias="VOICE_SEARCH_MAX_CONCURRENCY")
    voice_search_queue_wait_sec: float = Field(default=0.1, alias="VOICE_SEARCH_QUEUE_WAIT_SEC")
    voice_search_use_worker: bool = Field(default=True, alias="VOICE_SEARCH_USE_WORKER")
    voice_search_result_timeout_sec: float = Field(
        default=2.4, alias="VOICE_SEARCH_RESULT_TIMEOUT_SEC"
    )

    # Diarization
    diarization_min_speaker_distance: float = Field(
        default=0.18, alias="DIARIZATION_MIN_SPEAKER_DISTANCE"
    )

    # LLM
    local_llm_url: str = Field(default="http://localhost:8080/v1", alias="LOCAL_LLM_URL")
    local_llm_model: str = Field(default="qwen2.5-7b-instruct", alias="LOCAL_LLM_MODEL")

    # External LLM (Disabled by default)
    llm_external_provider_enabled: bool = Field(
        default=False, alias="LLM_EXTERNAL_PROVIDER_ENABLED"
    )
    llm_external_provider_url: str = Field(default="", alias="LLM_EXTERNAL_PROVIDER_URL")
    llm_external_provider_api_key: str = Field(default="", alias="LLM_EXTERNAL_PROVIDER_API_KEY")

    # Retention
    retention_audio_days: int = Field(default=14, alias="RETENTION_AUDIO_DAYS")
    retention_text_days: int = Field(default=365, alias="RETENTION_TEXT_DAYS")

    # Rate Limiting
    rate_limit_requests_per_window: int = Field(default=5, alias="RATE_LIMIT_REQUESTS_PER_WINDOW")
    rate_limit_window_sec: int = Field(default=10, alias="RATE_LIMIT_WINDOW_SEC")


@lru_cache
def get_settings() -> Settings:
    return Settings()

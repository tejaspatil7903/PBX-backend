import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Production Multi-Tenant PBX"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"

    # Security
    SECRET_KEY: str = "super-secret-jwt-signing-key-change-this-in-production-min-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "pbx_user"
    POSTGRES_PASSWORD: str = "pbx_password_secure"
    POSTGRES_DB: str = "pbx_db"
    DATABASE_URL: str = "postgresql+asyncpg://pbx_user:pbx_password_secure@localhost:5432/pbx_db"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"

    # FreeSWITCH ESL
    FREESWITCH_ESL_HOST: str = "localhost"
    FREESWITCH_ESL_PORT: int = 8021
    FREESWITCH_ESL_PASSWORD: str = "ClueCon"
    FREESWITCH_DOMAIN: str = "pbx.local"

    # Storage
    STORAGE_PROVIDER: str = "local"
    STORAGE_LOCAL_DIR: str = "./recordings"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = ""
    GCS_BUCKET_NAME: str = ""

    # Virtual QA & AI Settings
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_STT_MODEL: str = "scribe_v1"
    ELEVENLABS_TTS_MODEL: str = "eleven_flash_v2_5"
    ELEVENLABS_VOICE_ID: str = "JBFqnCBsd6RMkjVDRZzb"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_LOCATION: str = "global"
    GEMINI_QA_MODEL: str = "gemini-3.5-flash"

    QA_DEMO_MODE: bool = False
    QA_RECORDINGS_DIR: str = "/var/lib/freeswitch/recordings"
    QA_POLL_INTERVAL_SEC: float = 2.0
    QA_WINDOW_SEC: float = 6.0
    QA_WINDOW_OVERLAP_SEC: float = 1.5
    QA_SILENCE_RMS: float = 350.0
    QA_MAX_CONCURRENT_AI: int = 8
    QA_LEADER_LOCK_TTL_SEC: int = 30
    QA_EVENT_RETENTION_DAYS: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

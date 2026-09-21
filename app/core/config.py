"""Application settings loaded from environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration – all values come from .env or OS env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ── API Keys ─────────────────────────────────────────
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    pinecone_api_key: str = Field(default="", validation_alias="PINECONE_API_KEY")
    pinecone_index: str = Field(default="doc-chatbot", validation_alias="PINECONE_INDEX")
    pinecone_cloud: str = Field(default="aws", validation_alias="PINECONE_CLOUD")
    pinecone_region: str = Field(default="us-east-1", validation_alias="PINECONE_REGION")
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")

    # ── Auth ─────────────────────────────────────────────
    jwt_secret: str = Field(default="dev-jwt-secret-key-change-me", validation_alias="JWT_SECRET")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    demo_username: str = Field(default="demo", validation_alias="DEMO_USERNAME")
    demo_password: str = Field(default="demo123", validation_alias="DEMO_PASSWORD")

    # ── Database ─────────────────────────────────────────
    database_url: str = Field(default="sqlite+aiosqlite:///./storage/app.db", validation_alias="DATABASE_URL")

    # ── Upload ───────────────────────────────────────────
    max_upload_mb: int = Field(default=25, validation_alias="MAX_UPLOAD_MB")

    # ── LLM ──────────────────────────────────────────────
    gemini_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_MODEL")
    gemini_fallback_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_FALLBACK_MODEL")
    llm_max_retries: int = 3
    mock_llm: bool = Field(default=False, validation_alias="MOCK_LLM")

    @property
    def upload_max_mb(self) -> int:
        return self.max_upload_mb

    @property
    def llm_primary_model(self) -> str:
        return self.gemini_model

    @property
    def llm_fallback_model(self) -> str:
        return self.gemini_fallback_model


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

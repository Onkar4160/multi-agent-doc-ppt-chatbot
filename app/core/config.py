"""Application settings loaded from environment variables."""

from pydantic import AliasChoices, Field
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

    # ── Database & Storage ───────────────────────────────
    database_url: str = Field(default="sqlite+aiosqlite:///./storage/app.db", validation_alias="DATABASE_URL")
    storage_dir: str = Field(default="storage", validation_alias="STORAGE_DIR")

    # ── Upload ───────────────────────────────────────────
    max_upload_mb: int = Field(default=25, validation_alias=AliasChoices("UPLOAD_MAX_MB", "MAX_UPLOAD_MB"))

    # ── Templates ─────────────────────────────────────────
    default_docx_template_path: str = Field(
        default="data/sample_templates/Company_Proposal.docx",
        validation_alias="DEFAULT_DOCX_TEMPLATE_PATH",
    )
    default_pptx_template_path: str = Field(
        default="data/sample_templates/Green Cream Simple Aesthetic Watercolor Presentation.pptx",
        validation_alias="DEFAULT_PPTX_TEMPLATE_PATH",
    )

    # ── LLM ──────────────────────────────────────────────
    gemini_model: str = Field(
        default="gemini-3.8-flash", validation_alias=AliasChoices("GEMINI_MODEL", "LLM_PRIMARY_MODEL")
    )
    # Comma-separated; tried in order when a model is retired, out of quota or keeps failing.
    gemini_fallback_model: str = Field(
        default="gemini-3.7-flash,gemini-3.5-flash,gemini-3.1-flash-lite",
        validation_alias=AliasChoices("GEMINI_FALLBACK_MODEL", "LLM_FALLBACK_MODEL"),
    )
    llm_max_retries: int = 3
    # Reasoning tokens per call for models that "think" (Gemini 2.5+). Structured planning/writing does not need
    # long hidden reasoning; a small budget cuts latency and output tokens several-fold. -1 = model default.
    llm_thinking_budget: int = Field(default=512, validation_alias="LLM_THINKING_BUDGET")
    llm_max_calls_per_run: int = Field(default=30, validation_alias="LLM_MAX_CALLS_PER_RUN")  # real requests per chat run
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="text", validation_alias="LOG_FORMAT")  # text | json

    # ── Research & media ─────────────────────────────────
    academic_search: bool = Field(default=True, validation_alias="ACADEMIC_SEARCH")  # OpenAlex, arXiv, S2
    semantic_scholar_api_key: str = Field(default="", validation_alias="SEMANTIC_SCHOLAR_API_KEY")
    images_enabled: bool = Field(default=True, validation_alias="IMAGES_ENABLED")  # photos in template frames
    pexels_api_key: str = Field(default="", validation_alias="PEXELS_API_KEY")  # optional; Openverse otherwise
    max_deck_images: int = Field(default=4, validation_alias="MAX_DECK_IMAGES")
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

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in (self.gemini_fallback_model or "").split(",") if m.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset cached singleton settings instance."""
    global _settings
    _settings = None

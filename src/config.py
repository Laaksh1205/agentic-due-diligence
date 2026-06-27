from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────────────
    google_api_key: str
    gemini_fast_model: str = "gemini-2.5-flash"
    gemini_smart_model: str = "gemini-2.5-pro"

    # ── External APIs ─────────────────────────────────────────────────────────
    tavily_api_key: str
    registry_lookup_api_key: str
    companies_house_api_key: str
    news_api_key: Optional[str] = None  # NewsAPI.org (Phase 5.6); source skipped if absent
    contact_email: str = "laakshparikh@gmail.com"  # required by SEC EDGAR User-Agent policy

    # ── Storage ───────────────────────────────────────────────────────────────
    database_path: str = "data/due_diligence.db"

    # ── Guardrails (design doc Section 8e) ───────────────────────────────────
    max_llm_calls: int = 50
    max_cost_usd: float = 1.00
    max_wall_clock_seconds: int = 600
    max_docs_per_source: int = 10
    max_signals_per_doc: int = 7
    max_agent_retries: int = 2
    # Calls reserved out of max_llm_calls for the final synthesis step (≤7 category
    # sections + 1 overall) so upstream per-signal scoring can't starve the report.
    synthesis_call_reserve: int = 8
    hitl_timeout_seconds: int = 86400  # HITL review timeout; 24h prod, use --hitl-timeout 60 in dev

    # ── Cache ─────────────────────────────────────────────────────────────────
    use_cache: bool = True
    cache_ttl_hours: int = 168  # 7-day entity resolution cache

    # ── Langfuse monitoring (Task 3.3) — optional; tracing disabled if absent ─
    langfuse_secret_key: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_base_url: str = "https://cloud.langfuse.com"


settings = Settings()

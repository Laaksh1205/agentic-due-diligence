import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.models.documents import DataSufficiency
from src.models.entities import ResolvedEntity
from src.models.signals import RiskCategory, RiskSignal, SourceType


class ActionPriority(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    SHORT_TERM = "SHORT_TERM"
    MONITOR = "MONITOR"


class Action(BaseModel):
    description: str
    priority: ActionPriority
    related_signals: list[uuid.UUID] = []


class Source(BaseModel):
    url: str
    source_type: SourceType
    name: str = ""
    error: Optional[str] = None  # populated for sources_failed


class ReportMetadata(BaseModel):
    run_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_versions: dict[str, str] = {}  # {"fast": "gemini-2.5-flash", ...}
    estimated_cost_usd: float = 0.0
    latency_seconds: float = 0.0
    llm_call_count: int = 0
    signals_extracted: int = 0
    signals_rejected: int = 0


class DueDiligenceReport(BaseModel):
    target_entity: ResolvedEntity
    evaluation_scope: str
    data_sufficiency: DataSufficiency
    risk_signals: list[RiskSignal] = []
    positive_signals: list[RiskSignal] = []
    category_scores: dict[RiskCategory, float] = {}  # 0–10 per category
    overall_risk_score: float = 0.0
    executive_summary: str = ""
    strengths_section: str = ""
    detailed_sections: dict[RiskCategory, str] = {}
    recommended_actions: list[Action] = []
    sources_consulted: list[Source] = []
    sources_failed: list[Source] = []
    metadata: ReportMetadata = Field(default_factory=ReportMetadata)

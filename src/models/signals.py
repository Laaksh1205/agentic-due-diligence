import uuid
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.models.entities import Entity


class RiskCategory(str, Enum):
    FINANCIAL = "FINANCIAL"
    LEGAL = "LEGAL"
    REGULATORY = "REGULATORY"
    REPUTATIONAL = "REPUTATIONAL"
    OPERATIONAL = "OPERATIONAL"
    CYBERSECURITY = "CYBERSECURITY"
    ESG = "ESG"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class SignalPolarity(str, Enum):
    NEGATIVE = "NEGATIVE"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"


class SourceType(str, Enum):
    COMPANY_REGISTRY = "COMPANY_REGISTRY"
    NEWS_ARTICLE = "NEWS_ARTICLE"
    SEC_FILING = "SEC_FILING"
    COURT_RECORD = "COURT_RECORD"
    COMPANY_WEBSITE = "COMPANY_WEBSITE"
    SANCTIONS_LIST = "SANCTIONS_LIST"
    INTERNAL_DOC = "INTERNAL_DOC"


class HumanVerdict(str, Enum):
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"
    NEEDS_INVESTIGATION = "NEEDS_INVESTIGATION"


class RiskSignal(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    text: str
    normalized_text: str = ""
    source_url: str
    source_type: SourceType
    source_snippet: str  # verbatim 20–50 word quote from source (quote anchor)
    extraction_date: datetime = Field(default_factory=datetime.utcnow)
    data_date: Optional[date] = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    temporal_weight: float = Field(default=1.0, ge=0.0, le=1.0)

    risk_category: RiskCategory
    risk_subcategory: str = ""
    severity: Severity
    signal_polarity: SignalPolarity

    entity_name: str
    related_entities: list[Entity] = []

    is_corroborated: bool = False
    corroborating_signals: list[uuid.UUID] = []

    # Source credibility (src/analysis/source_credibility.py). Defaults keep older
    # stored reports valid; set during extraction / risk analysis.
    source_credibility: float = Field(default=1.0, ge=0.0, le=1.0)  # tier weight
    credibility_tier: str = ""                                       # PRIMARY/ESTABLISHED/GENERAL/LOW
    independent_source_count: int = Field(default=1, ge=1)           # distinct corroborating domains
    is_unverified: bool = False                                      # severity-capped: single low-trust source

    requires_human_review: bool = False
    human_verdict: Optional[HumanVerdict] = None
    is_contradictory: bool = False

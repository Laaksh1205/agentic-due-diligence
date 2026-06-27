from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.models.signals import SourceType


class DataSufficiency(str, Enum):
    RICH = "RICH"          # 15+ docs, 4+ source types
    ADEQUATE = "ADEQUATE"  # 8–14 docs, 3+ source types
    LIMITED = "LIMITED"    # 4–7 docs, 2+ source types
    SPARSE = "SPARSE"      # <4 docs or only 1 source type


class RawDocument(BaseModel):
    source_url: str
    source_type: SourceType
    raw_text: str
    fetch_timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = {}
    entity_name: Optional[str] = None

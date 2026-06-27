from enum import Enum
from typing import Optional

from pydantic import BaseModel


class EntityType(str, Enum):
    PERSON = "PERSON"
    COMPANY = "COMPANY"
    REGULATOR = "REGULATOR"
    COURT = "COURT"
    GOVERNMENT_BODY = "GOVERNMENT_BODY"


class Entity(BaseModel):
    name: str
    entity_type: EntityType
    role: str
    canonical_id: Optional[str] = None


class ResolvedEntity(BaseModel):
    canonical_name: str
    aliases: list[str] = []
    jurisdiction: Optional[str] = None
    industry: Optional[str] = None
    is_public: bool = False
    registry_lookup_id: Optional[str] = None
    companies_house_number: Optional[str] = None
    sec_cik: Optional[str] = None

"""Provider-independent domain types used by collectors, matching and storage."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Provider(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WEBSITE = "website"


class WorkplaceType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    NEW = "new"
    INTERESTING = "interesting"
    REJECTED = "rejected"
    APPLIED = "applied"


class CollectedJob(BaseModel):
    """A normalized job posting returned by every collector."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    provider: Provider
    external_id: str
    company: str
    title: str
    description: str = ""
    job_url: str
    apply_url: str | None = None
    locations: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    workplace_type: WorkplaceType = WorkplaceType.UNKNOWN
    language: str = "unknown"
    seniority: str = "unknown"
    skills: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id", "external_id", "company", "title", "job_url")
    @classmethod
    def non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("job_url", "apply_url")
    @classmethod
    def valid_external_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        return value

    @field_validator("countries")
    @classmethod
    def uppercase_countries(cls, values: list[str]) -> list[str]:
        return sorted({value.upper() for value in values if value})


class MatchResult(BaseModel):
    score: int = Field(ge=0, le=100)
    excluded: bool = False
    meets_threshold: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ScanSummary(BaseModel):
    run_id: int
    status: str
    sources_total: int
    sources_succeeded: int
    sources_failed: int
    jobs_seen: int
    jobs_saved: int
    errors: list[str] = Field(default_factory=list)

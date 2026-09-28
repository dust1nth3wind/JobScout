"""SmartRecruiters public postings API collector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from jobscout.collectors.base import get_json
from jobscout.config import SmartRecruitersSource, SourceConfig
from jobscout.domain import CollectedJob, Provider, WorkplaceType
from jobscout.normalization import (
    countries_from_locations,
    html_to_text,
    infer_language,
    infer_seniority,
)


PAGE_LIMIT = 100


class _PostingSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str


class _PostingList(BaseModel):
    model_config = ConfigDict(extra="allow")

    content: list[_PostingSummary]
    totalFound: int = Field(ge=0)


class _Location(BaseModel):
    model_config = ConfigDict(extra="allow")

    city: str | None = None
    country: str | None = None
    fullLocation: str | None = None
    remote: bool | None = None
    hybrid: bool | None = None


class _Language(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str | None = None


class _CodedLabel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    label: str | None = None


class _PostingDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    active: bool | None = None
    visibility: str | None = None
    postingUrl: str | None = None
    applyUrl: str | None = None
    location: _Location | None = None
    language: _Language | None = None
    releasedDate: datetime | None = None
    jobAd: dict[str, Any] | None = None
    typeOfEmployment: _CodedLabel | None = None
    experienceLevel: _CodedLabel | None = None


def _description(job_ad: dict[str, Any] | None) -> str:
    sections = (job_ad or {}).get("sections")
    if not isinstance(sections, dict):
        return ""
    parts = []
    for section in sections.values():
        if isinstance(section, dict) and isinstance(section.get("text"), str):
            content = html_to_text(section["text"])
            if content:
                parts.append(content)
    return " ".join(parts)


def _workplace(location: _Location | None) -> WorkplaceType:
    if location is None:
        return WorkplaceType.UNKNOWN
    if location.hybrid:
        return WorkplaceType.HYBRID
    if location.remote:
        return WorkplaceType.REMOTE
    if location.city or location.fullLocation:
        return WorkplaceType.ONSITE
    return WorkplaceType.UNKNOWN


EXPERIENCE_LEVEL_MAP = {
    "internship": "intern",
    "entry_level": "junior",
    "associate": "junior",
    "mid_senior_level": "senior",
    "director": "executive",
    "executive": "executive",
}


def _seniority(detail: _PostingDetail) -> str:
    employment = detail.typeOfEmployment
    if employment and infer_seniority(
        " ".join(part for part in (employment.id, employment.label) if part)
    ) == "intern":
        return "intern"
    title_seniority = infer_seniority(detail.name)
    if title_seniority != "unknown":
        return title_seniority
    experience = detail.experienceLevel
    return EXPERIENCE_LEVEL_MAP.get(experience.id.lower(), "unknown") if experience and experience.id else "unknown"


class SmartRecruitersCollector:
    def __init__(self, attempts: int = 2) -> None:
        self.attempts = attempts

    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]:
        if not isinstance(source, SmartRecruitersSource):
            raise TypeError("SmartRecruitersCollector requires SmartRecruitersSource")
        url = f"https://api.smartrecruiters.com/v1/companies/{source.company_identifier}/postings"
        summaries: list[_PostingSummary] = []
        seen_ids: set[str] = set()
        offset = 0
        while True:
            raw_page: dict[str, Any] = get_json(
                client,
                url,
                params={"limit": PAGE_LIMIT, "offset": offset, "destination": "PUBLIC"},
                attempts=self.attempts,
            )
            page = _PostingList.model_validate(raw_page)
            for summary in page.content:
                if summary.id not in seen_ids:
                    seen_ids.add(summary.id)
                    summaries.append(summary)
            if not page.content:
                if offset < page.totalFound:
                    raise ValueError("SmartRecruiters returned an incomplete posting list")
                break
            offset += len(page.content)
            if offset >= page.totalFound:
                if len(seen_ids) < page.totalFound:
                    raise ValueError("SmartRecruiters returned duplicate or missing postings")
                break

        results: list[CollectedJob] = []
        for summary in summaries:
            try:
                raw_detail: dict[str, Any] = get_json(
                    client,
                    f"{url}/{summary.id}",
                    attempts=self.attempts,
                )
            except httpx.HTTPStatusError as exc:
                # A posting can close between listing and detail retrieval.
                if exc.response.status_code in {404, 410}:
                    continue
                raise
            detail = _PostingDetail.model_validate(raw_detail)
            if detail.active is False or (detail.visibility and detail.visibility != "PUBLIC"):
                continue
            location = detail.location
            locations = []
            if location is not None:
                value = location.fullLocation or ", ".join(
                    part for part in (location.city, location.country) if part
                )
                if value:
                    locations.append(value)
            description = _description(detail.jobAd)
            explicit_country = [location.country] if location and location.country else []
            job_url = detail.postingUrl or (
                f"https://jobs.smartrecruiters.com/{source.company_identifier}/{detail.id}"
            )
            language_code = detail.language.code if detail.language else None
            language = language_code.lower().split("-", 1)[0] if language_code else infer_language(
                f"{detail.name} {description}"
            )
            results.append(
                CollectedJob(
                    source_id=source.id,
                    provider=Provider.SMARTRECRUITERS,
                    external_id=detail.id,
                    company=source.company,
                    title=detail.name,
                    description=description,
                    job_url=job_url,
                    apply_url=detail.applyUrl or job_url,
                    locations=locations,
                    countries=countries_from_locations(locations, explicit_country),
                    workplace_type=_workplace(location),
                    language=language,
                    seniority=_seniority(detail),
                    published_at=detail.releasedDate,
                    raw_payload=raw_detail,
                )
            )
        return results

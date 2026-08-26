"""Ashby public Job Postings API collector."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from jobscout.collectors.base import get_json
from jobscout.config import AshbySource, SourceConfig
from jobscout.domain import CollectedJob, Provider, WorkplaceType
from jobscout.normalization import (
    countries_from_locations,
    html_to_text,
    infer_language,
    infer_seniority,
    infer_workplace,
)


class _PostalAddress(BaseModel):
    model_config = ConfigDict(extra="allow")
    addressCountry: str | None = None


class _Address(BaseModel):
    model_config = ConfigDict(extra="allow")
    postalAddress: _PostalAddress | None = None


class _SecondaryLocation(BaseModel):
    model_config = ConfigDict(extra="allow")
    location: str = ""


class _Job(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    title: str
    location: str = ""
    secondaryLocations: list[_SecondaryLocation] = Field(default_factory=list)
    isRemote: bool | None = False
    workplaceType: str | None = ""
    descriptionHtml: str = ""
    descriptionPlain: str = ""
    publishedAt: datetime | None = None
    jobUrl: str
    applyUrl: str | None = None
    address: _Address | None = None


class _Payload(BaseModel):
    model_config = ConfigDict(extra="allow")
    jobs: list[_Job]


WORKPLACE_MAP = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "onsite": WorkplaceType.ONSITE,
}


class AshbyCollector:
    def __init__(self, attempts: int = 2) -> None:
        self.attempts = attempts

    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]:
        if not isinstance(source, AshbySource):
            raise TypeError("AshbyCollector requires AshbySource")
        url = f"https://api.ashbyhq.com/posting-api/job-board/{source.board_name}"
        raw: dict[str, Any] = get_json(
            client,
            url,
            params={"includeCompensation": str(source.include_compensation).lower()},
            attempts=self.attempts,
        )
        payload = _Payload.model_validate(raw)
        raw_jobs = raw.get("jobs", [])
        results: list[CollectedJob] = []
        for index, job in enumerate(payload.jobs):
            description = job.descriptionPlain or html_to_text(job.descriptionHtml)
            locations = [value for value in [job.location, *(item.location for item in job.secondaryLocations)] if value]
            explicit: list[str] = []
            if job.address and job.address.postalAddress and job.address.postalAddress.addressCountry:
                explicit.append(job.address.postalAddress.addressCountry)
            workplace = WORKPLACE_MAP.get((job.workplaceType or "").lower())
            if workplace is None:
                workplace = (
                    WorkplaceType.REMOTE
                    if job.isRemote
                    else infer_workplace(job.title, *locations, description)
                )
            external_id = job.id or hashlib.sha256(job.jobUrl.encode("utf-8")).hexdigest()[:24]
            results.append(
                CollectedJob(
                    source_id=source.id,
                    provider=Provider.ASHBY,
                    external_id=external_id,
                    company=source.company,
                    title=job.title,
                    description=description,
                    job_url=job.jobUrl,
                    apply_url=job.applyUrl,
                    locations=locations,
                    countries=countries_from_locations(locations, explicit),
                    workplace_type=workplace,
                    language=infer_language(f"{job.title} {description}"),
                    seniority=infer_seniority(job.title),
                    published_at=job.publishedAt,
                    raw_payload=raw_jobs[index] if index < len(raw_jobs) else job.model_dump(mode="json"),
                )
            )
        return results

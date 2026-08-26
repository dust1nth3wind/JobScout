"""Greenhouse public Job Board collector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from jobscout.collectors.base import get_json
from jobscout.config import GreenhouseSource, SourceConfig
from jobscout.domain import CollectedJob, Provider
from jobscout.normalization import (
    countries_from_locations,
    html_to_text,
    infer_language,
    infer_seniority,
    infer_workplace,
)


class _Location(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = ""


class _Job(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int | str
    title: str
    absolute_url: str
    content: str = ""
    location: _Location = Field(default_factory=_Location)
    updated_at: datetime | None = None


class _Payload(BaseModel):
    model_config = ConfigDict(extra="allow")
    jobs: list[_Job]


class GreenhouseCollector:
    def __init__(self, attempts: int = 2) -> None:
        self.attempts = attempts

    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]:
        if not isinstance(source, GreenhouseSource):
            raise TypeError("GreenhouseCollector requires GreenhouseSource")
        url = f"https://api.greenhouse.io/v1/boards/{source.board_token}/jobs"
        raw: dict[str, Any] = get_json(client, url, params={"content": "true"}, attempts=self.attempts)
        payload = _Payload.model_validate(raw)
        results: list[CollectedJob] = []
        raw_jobs = raw.get("jobs", [])
        for index, job in enumerate(payload.jobs):
            description = html_to_text(job.content)
            locations = [job.location.name] if job.location.name else []
            results.append(
                CollectedJob(
                    source_id=source.id,
                    provider=Provider.GREENHOUSE,
                    external_id=str(job.id),
                    company=source.company,
                    title=job.title,
                    description=description,
                    job_url=job.absolute_url,
                    apply_url=job.absolute_url,
                    locations=locations,
                    countries=countries_from_locations(locations),
                    workplace_type=infer_workplace(job.title, *locations, description),
                    language=infer_language(f"{job.title} {description}"),
                    seniority=infer_seniority(job.title),
                    published_at=job.updated_at,
                    raw_payload=raw_jobs[index] if index < len(raw_jobs) else job.model_dump(mode="json"),
                )
            )
        return results

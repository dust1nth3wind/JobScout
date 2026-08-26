"""Lever public Postings API collector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from jobscout.collectors.base import get_json
from jobscout.config import LeverSource, SourceConfig
from jobscout.domain import CollectedJob, Provider, WorkplaceType
from jobscout.normalization import (
    countries_from_locations,
    html_to_text,
    infer_language,
    infer_seniority,
    infer_workplace,
)


class _Categories(BaseModel):
    model_config = ConfigDict(extra="allow")
    location: str = ""
    allLocations: list[str] = Field(default_factory=list)


class _Job(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    text: str
    categories: _Categories = Field(default_factory=_Categories)
    country: str | None = None
    descriptionPlain: str = ""
    description: str = ""
    hostedUrl: str
    applyUrl: str | None = None
    workplaceType: str = "unspecified"
    createdAt: int | None = None


WORKPLACE_MAP = {
    "remote": WorkplaceType.REMOTE,
    "hybrid": WorkplaceType.HYBRID,
    "on-site": WorkplaceType.ONSITE,
}


class LeverCollector:
    def __init__(self, attempts: int = 2) -> None:
        self.attempts = attempts

    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]:
        if not isinstance(source, LeverSource):
            raise TypeError("LeverCollector requires LeverSource")
        host = "api.eu.lever.co" if source.instance == "eu" else "api.lever.co"
        url = f"https://{host}/v0/postings/{source.site}"
        raw: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for location in source.locations or [None]:
            params = {"mode": "json"}
            if location:
                params["location"] = location
            batch: list[dict[str, Any]] = get_json(
                client,
                url,
                params=params,
                attempts=self.attempts,
            )
            for item in batch:
                item_id = str(item.get("id", ""))
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    raw.append(item)
        jobs = [_Job.model_validate(item) for item in raw]
        results: list[CollectedJob] = []
        for item, job in zip(raw, jobs, strict=True):
            description = job.descriptionPlain or html_to_text(job.description)
            locations = list(dict.fromkeys(job.categories.allLocations or ([job.categories.location] if job.categories.location else [])))
            workplace = WORKPLACE_MAP.get(
                job.workplaceType.lower(), infer_workplace(job.text, *locations, description)
            )
            published = datetime.fromtimestamp(job.createdAt / 1000, tz=UTC) if job.createdAt else None
            explicit_country = [job.country] if job.country else []
            results.append(
                CollectedJob(
                    source_id=source.id,
                    provider=Provider.LEVER,
                    external_id=job.id,
                    company=source.company,
                    title=job.text,
                    description=description,
                    job_url=job.hostedUrl,
                    apply_url=job.applyUrl,
                    locations=locations,
                    countries=countries_from_locations(locations, explicit_country),
                    workplace_type=workplace,
                    language=infer_language(f"{job.text} {description}"),
                    seniority=infer_seniority(job.text),
                    published_at=published,
                    raw_payload=item,
                )
            )
        return results

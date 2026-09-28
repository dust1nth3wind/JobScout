"""Public Recruitee career-board collector."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from jobscout.collectors.base import get_json
from jobscout.config import RecruiteeSource, SourceConfig
from jobscout.domain import CollectedJob, Provider, WorkplaceType
from jobscout.normalization import (
    countries_from_locations,
    html_to_text,
    infer_language,
    infer_seniority,
)


def _published_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00"))
    except ValueError:
        return None


class RecruiteeCollector:
    def __init__(self, attempts: int = 2) -> None:
        self.attempts = attempts

    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]:
        if not isinstance(source, RecruiteeSource):
            raise TypeError("RecruiteeCollector requires RecruiteeSource")
        url = f"https://api.recruitee.com/c/{source.account}/careers/offers"
        payload: dict[str, Any] = get_json(client, url, attempts=self.attempts)
        results: list[CollectedJob] = []
        for item in payload["offers"]:
            title = str(item["title"])
            description = html_to_text(item.get("description"))
            requirements = html_to_text(item.get("requirements"))
            if requirements:
                description = f"{description} {requirements}".strip()
            location = str(item.get("location") or "").strip()
            if not location:
                location = ", ".join(
                    str(item[key]).strip() for key in ("city", "country") if item.get(key)
                )
            locations = [location] if location else []
            country = str(item.get("country_code") or item.get("country") or "").strip()
            if item.get("remote"):
                workplace = WorkplaceType.REMOTE
            elif item.get("hybrid"):
                workplace = WorkplaceType.HYBRID
            elif item.get("on_site"):
                workplace = WorkplaceType.ONSITE
            else:
                workplace = WorkplaceType.UNKNOWN
            results.append(
                CollectedJob(
                    source_id=source.id,
                    provider=Provider.RECRUITEE,
                    external_id=str(item["id"]),
                    company=source.company,
                    title=title,
                    description=description,
                    job_url=str(item["careers_url"]),
                    apply_url=item.get("careers_apply_url") or item["careers_url"],
                    locations=locations,
                    countries=countries_from_locations(locations, [country] if country else []),
                    workplace_type=workplace,
                    language=infer_language(f"{title} {description}"),
                    seniority=infer_seniority(title),
                    published_at=_published_at(item.get("published_at")),
                    raw_payload=item,
                )
            )
        return results

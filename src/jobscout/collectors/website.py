"""Collector for explicitly seeded public job-advertisement webpages."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from jobscout.collectors.base import get_text
from jobscout.config import SourceConfig, WebsiteSource
from jobscout.domain import CollectedJob, Provider, WorkplaceType
from jobscout.normalization import (
    canonicalize_url,
    countries_from_locations,
    html_to_text,
    infer_language,
    infer_seniority,
    infer_workplace,
)


class _JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depths = {"h1": 0, "title": 0, "script": 0, "style": 0}
        self.itemprop: str | None = None
        self.itemprop_depth = 0
        self.og_title = ""
        self.h1_candidates: list[str] = []
        self.current_h1_parts: list[str] = []
        self.title_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.itemprop_parts: dict[str, list[str]] = {}
        self.json_ld_parts: list[str] = []
        self.in_json_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "h1" and self.depths["h1"] == 0:
            self.current_h1_parts = []
        if tag in self.depths:
            self.depths[tag] += 1
        if tag == "meta" and values.get("property") == "og:title":
            self.og_title = values.get("content") or ""
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.in_json_ld = True
        if self.itemprop is not None:
            self.itemprop_depth += 1
        elif values.get("itemprop") in {"title", "description", "location"}:
            self.itemprop = values["itemprop"]
            self.itemprop_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.itemprop is not None:
            self.itemprop_depth -= 1
            if self.itemprop_depth == 0:
                self.itemprop = None
        if tag == "script" and self.in_json_ld:
            self.in_json_ld = False
        if tag == "h1" and self.depths["h1"] == 1:
            candidate = " ".join(self.current_h1_parts).strip()
            if candidate:
                self.h1_candidates.append(candidate)
        if tag in self.depths and self.depths[tag]:
            self.depths[tag] -= 1

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if not value:
            return
        if self.in_json_ld:
            self.json_ld_parts.append(data)
            return
        if self.depths["script"] or self.depths["style"]:
            return
        if self.depths["h1"]:
            self.current_h1_parts.append(value)
        if self.depths["title"]:
            self.title_parts.append(value)
        if self.itemprop is not None:
            self.itemprop_parts.setdefault(self.itemprop, []).append(value)
        self.visible_parts.append(value)


def _job_posting(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        for item in value:
            posting = _job_posting(item)
            if posting:
                return posting
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
            return value
        for key in ("@graph", "mainEntity", "itemListElement"):
            posting = _job_posting(value.get(key))
            if posting:
                return posting
    return None


def _parse_json_ld(parts: list[str]) -> dict[str, Any]:
    for raw in parts:
        try:
            posting = _job_posting(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
        if posting:
            return posting
    return {}


def _location_from_posting(posting: dict[str, Any]) -> tuple[list[str], list[str]]:
    raw_locations = posting.get("jobLocation", [])
    if isinstance(raw_locations, dict):
        raw_locations = [raw_locations]
    locations: list[str] = []
    countries: list[str] = []
    for raw in raw_locations if isinstance(raw_locations, list) else []:
        if not isinstance(raw, dict):
            continue
        address = raw.get("address", raw)
        if not isinstance(address, dict):
            continue
        country = str(address.get("addressCountry", "")).strip()
        parts = [
            str(address.get(key, "")).strip()
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        location = ", ".join(part for part in parts if part)
        if location:
            locations.append(location)
        if country:
            countries.append(country)
    return locations, countries


def _published_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


class WebsiteCollector:
    def __init__(self, attempts: int = 2) -> None:
        self.attempts = attempts

    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]:
        if not isinstance(source, WebsiteSource):
            raise TypeError("WebsiteCollector requires WebsiteSource")
        results: list[CollectedJob] = []
        for configured_url in source.job_urls:
            url = canonicalize_url(configured_url)
            html = get_text(client, url, attempts=self.attempts)
            parser = _JobPageParser()
            parser.feed(html)
            posting = _parse_json_ld(parser.json_ld_parts)

            title = str(posting.get("title", "")).strip()
            if not title:
                title = " ".join(parser.itemprop_parts.get("title", [])).strip()
            if not title:
                title = (parser.h1_candidates[0] if parser.h1_candidates else "") or parser.og_title.strip()
            if not title:
                title = " ".join(parser.title_parts).split("|")[0].strip()
            if not title:
                raise ValueError(f"Could not find a job title on {url}")

            description = html_to_text(str(posting.get("description", "")))
            if not description:
                description = " ".join(parser.itemprop_parts.get("description", [])).strip()
            if not description:
                description = " ".join(parser.visible_parts).strip()

            locations, explicit_countries = _location_from_posting(posting)
            if not locations:
                microdata_location = " ".join(parser.itemprop_parts.get("location", [])).strip()
                locations = [microdata_location] if microdata_location else list(source.default_locations)
            if not explicit_countries:
                explicit_countries = list(source.default_countries)

            workplace = infer_workplace(title, *locations, description)
            if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE":
                workplace = WorkplaceType.REMOTE
            identifier = posting.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value")
            external_id = str(identifier).strip() if identifier else ""
            if not external_id:
                external_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]

            results.append(
                CollectedJob(
                    source_id=source.id,
                    provider=Provider.WEBSITE,
                    external_id=external_id,
                    company=source.company,
                    title=title,
                    description=description,
                    job_url=url,
                    apply_url=url,
                    locations=locations,
                    countries=countries_from_locations(locations, explicit_countries),
                    workplace_type=workplace,
                    language=infer_language(f"{title} {description}"),
                    seniority=infer_seniority(title),
                    published_at=_published_at(posting.get("datePosted")),
                    raw_payload={"url": url, "job_posting": posting},
                )
            )
        return results

"""Collector for explicitly seeded public job-advertisement webpages."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
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
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depths = {"h1": 0, "h2": 0, "title": 0, "script": 0, "style": 0}
        self.itemprop: str | None = None
        self.itemprop_depth = 0
        self.og_title = ""
        self.h1_candidates: list[str] = []
        self.h2_candidates: list[str] = []
        self.current_h1_parts: list[str] = []
        self.current_h2_parts: list[str] = []
        self.title_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.itemprop_parts: dict[str, list[str]] = {}
        self.json_ld_parts: list[str] = []
        self.in_json_ld = False
        self.job_body_depth = 0
        self.job_body_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "h1" and self.depths["h1"] == 0:
            self.current_h1_parts = []
        if tag == "h2" and self.depths["h2"] == 0:
            self.current_h2_parts = []
        if tag in self.depths:
            self.depths[tag] += 1
        classes = (values.get("class") or "").split()
        if self.job_body_depth and tag not in self._VOID_TAGS:
            self.job_body_depth += 1
        elif tag == "div" and "jobDisplay" in classes:
            self.job_body_depth = 1
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
        if tag == "h2" and self.depths["h2"] == 1:
            candidate = " ".join(self.current_h2_parts).strip()
            if candidate:
                self.h2_candidates.append(candidate)
        if self.job_body_depth:
            self.job_body_depth -= 1
        if tag in self.depths and self.depths[tag]:
            self.depths[tag] -= 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

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
        if self.depths["h2"]:
            self.current_h2_parts.append(value)
        if self.depths["title"]:
            self.title_parts.append(value)
        if self.itemprop is not None:
            self.itemprop_parts.setdefault(self.itemprop, []).append(value)
        if self.job_body_depth:
            self.job_body_parts.append(value)
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


def _is_expired(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return datetime.fromisoformat(value.strip()).date() < datetime.now(UTC).date()
    try:
        expires = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires < datetime.now(UTC)


def _organization_name(posting: dict[str, Any], fallback: str) -> str:
    organization = posting.get("hiringOrganization")
    if isinstance(organization, dict):
        name = str(organization.get("name", "")).strip()
        if name:
            return name
    return fallback


def parse_job_page(
    *,
    source_id: str,
    provider: Provider,
    company: str,
    url: str,
    html: str,
    default_locations: list[str] | None = None,
    default_countries: list[str] | None = None,
    page_format: str = "schema_org",
    require_job_posting: bool = False,
) -> CollectedJob | None:
    """Normalize one public job detail page shared by seeded and discovered sources."""
    parser = _JobPageParser()
    parser.feed(html)
    posting = _parse_json_ld(parser.json_ld_parts)
    if require_job_posting and not posting:
        return None
    if _is_expired(posting.get("validThrough")):
        return None

    title = str(posting.get("title", "")).strip()
    if not title:
        title = " ".join(parser.itemprop_parts.get("title", [])).strip()
    if not title:
        title = (parser.h1_candidates[0] if parser.h1_candidates else "") or parser.og_title.strip()
    if not title:
        title = " ".join(parser.title_parts).split("|")[0].strip()
    if not title:
        raise ValueError(f"Could not find a job title on {url}")

    resolved_company = _organization_name(posting, company)
    locations, explicit_countries = _location_from_posting(posting)
    if page_format == "eurojobsites" and parser.h2_candidates:
        details = [item for item in parser.h2_candidates if item.casefold() != title.casefold()]
        if details:
            resolved_company = details[0]
        if len(details) > 1:
            locations = [details[1]]
    if not locations:
        microdata_location = " ".join(parser.itemprop_parts.get("location", [])).strip()
        locations = [microdata_location] if microdata_location else list(default_locations or [])
    if not explicit_countries:
        explicit_countries = list(default_countries or [])

    description = html_to_text(str(posting.get("description", "")))
    if not description:
        description = " ".join(parser.itemprop_parts.get("description", [])).strip()
    if not description and page_format == "eurojobsites":
        description = " ".join(parser.job_body_parts).strip()
    if not description:
        description = " ".join(parser.visible_parts).strip()

    if page_format == "eurojobsites" and re.search(
        r"\b(?:this job at .+ is not available|job is no longer available)\b", title, re.I
    ):
        return None

    workplace = infer_workplace(title, *locations, description)
    if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        workplace = WorkplaceType.REMOTE
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    external_id = str(identifier).strip() if identifier else ""
    if not external_id:
        external_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]

    employment_type = posting.get("employmentType", "")
    if isinstance(employment_type, list):
        employment_type = " ".join(str(value) for value in employment_type)
    seniority = infer_seniority(f"{title} {employment_type}")
    if seniority == "unknown" and re.search(
        r"\b(?:at least|minimum(?: of)?|over|more than)?\s*"
        r"(?:[4-9]|\d{2,}|four|five|six|seven|eight|nine|ten)"
        r"(?:\s*[-–]\s*\d+|\+)?\s+years?\s+"
        r"(?:of\s+)?(?:(?:relevant|professional|work|post-degree)\s+)*experience\b",
        description,
        re.I,
    ):
        seniority = "senior"
    if seniority == "unknown" and re.search(
        r"\b(?:contract|position|employment)\s*type\s*:\s*"
        r"(?:paid\s+)?(?:internship|traineeship|trainee|working student|werkstudent)\b",
        description,
        re.I,
    ):
        seniority = "intern"
    return CollectedJob(
        source_id=source_id,
        provider=provider,
        external_id=external_id,
        company=resolved_company,
        title=title,
        description=description,
        job_url=url,
        apply_url=url,
        locations=locations,
        countries=countries_from_locations(locations, explicit_countries),
        workplace_type=workplace,
        language=infer_language(f"{title} {description}"),
        seniority=seniority,
        published_at=_published_at(posting.get("datePosted")),
        raw_payload={"url": url, "job_posting": posting},
    )


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
            job = parse_job_page(
                source_id=source.id,
                provider=Provider.WEBSITE,
                company=source.company,
                url=url,
                html=html,
                default_locations=source.default_locations,
                default_countries=source.default_countries,
            )
            if job is not None:
                results.append(job)
        return results

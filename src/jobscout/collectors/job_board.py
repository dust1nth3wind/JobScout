"""Discovery collector for public job-board listings and XML sitemaps."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

from jobscout.collectors.base import get_text
from jobscout.collectors.website import parse_job_page
from jobscout.config import JobBoardSource, SourceConfig
from jobscout.domain import CollectedJob, Provider
from jobscout.normalization import canonicalize_url, normalized_text


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.in_loc = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "loc":
            self.in_loc = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "loc":
            self.in_loc = False

    def handle_data(self, data: str) -> None:
        if self.in_loc and data.strip():
            self.links.append(data.strip())


def _listing_links(content: str) -> tuple[list[str], list[str]]:
    stripped = content.lstrip()
    if stripped.startswith(("<?xml", "<urlset", "<sitemapindex")):
        root = ElementTree.fromstring(content)
        kind = root.tag.rsplit("}", 1)[-1]
        urls = [
            loc.text.strip()
            for entry in root
            for loc in entry
            if loc.tag.rsplit("}", 1)[-1] == "loc" and loc.text and loc.text.strip()
        ]
        if kind == "sitemapindex":
            return [], urls
        if kind == "urlset":
            return urls, []
        raise ValueError(f"Unsupported sitemap type: {kind}")
    parser = _ListingParser()
    parser.feed(content)
    return parser.links, []


def _matches_prefix(url: str, prefix: str) -> bool:
    clean_prefix = prefix.rstrip("/")
    return url == clean_prefix or url.startswith(clean_prefix + "/")


def _contains_term(text: str, term: str) -> bool:
    normalized_term = normalized_text(term)
    return bool(normalized_term) and bool(
        re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", text)
    )


def _matches_term_groups(url: str, groups: list[list[str]]) -> bool:
    text = normalized_text(url)
    return all(
        any(_contains_term(text, term) for term in group)
        for group in groups
        if group
    )


def _is_excluded_url(url: str, terms: list[str]) -> bool:
    text = normalized_text(url)
    return any(_contains_term(text, term) for term in terms)


class JobBoardCollector:
    def __init__(self, attempts: int = 2) -> None:
        self.attempts = attempts

    def collect(self, source: SourceConfig, client: httpx.Client) -> list[CollectedJob]:
        if not isinstance(source, JobBoardSource):
            raise TypeError("JobBoardCollector requires JobBoardSource")

        discovered: list[str] = []
        seen_urls: set[str] = set()
        visited_listings: set[str] = set()
        pending_listings = list(source.listing_urls)
        allowed_origins = {urlsplit(url).netloc for url in source.listing_urls}
        while pending_listings and len(discovered) < source.max_jobs:
            listing_url = pending_listings.pop(0)
            if listing_url in visited_listings:
                continue
            visited_listings.add(listing_url)
            if len(visited_listings) > 25:
                raise ValueError("Too many nested job-board sitemaps")
            content = get_text(client, listing_url, attempts=self.attempts)
            links, child_sitemaps = _listing_links(content)
            for child in child_sitemaps:
                child_url = urljoin(listing_url, child)
                if urlsplit(child_url).netloc not in allowed_origins:
                    raise ValueError(f"Sitemap outside configured host: {child_url}")
                pending_listings.append(child_url)
            for raw_link in links:
                url = canonicalize_url(urljoin(listing_url, raw_link))
                if not any(_matches_prefix(url, prefix) for prefix in source.job_url_prefixes):
                    continue
                if source.url_term_groups and not _matches_term_groups(url, source.url_term_groups):
                    continue
                if _is_excluded_url(url, source.excluded_url_terms):
                    continue
                if url not in seen_urls:
                    discovered.append(url)
                    seen_urls.add(url)
                if len(discovered) >= source.max_jobs:
                    break

        if not discovered:
            raise ValueError(f"No job detail links found for source {source.id}")

        results: list[CollectedJob] = []
        for url in discovered:
            try:
                html = get_text(client, url, attempts=self.attempts)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    continue
                raise
            job = parse_job_page(
                source_id=source.id,
                provider=Provider.JOB_BOARD,
                company=source.company,
                url=url,
                html=html,
                default_locations=source.default_locations,
                default_countries=source.default_countries,
                page_format=source.page_format,
                require_job_posting=source.page_format == "schema_org",
            )
            if job is None or job.seniority in source.excluded_seniorities:
                continue
            results.append(job)
        return results

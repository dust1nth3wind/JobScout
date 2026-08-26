from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jobscout.collectors.ashby import AshbyCollector
from jobscout.collectors.greenhouse import GreenhouseCollector
from jobscout.collectors.lever import LeverCollector
from jobscout.collectors.website import WebsiteCollector
from jobscout.config import AshbySource, GreenhouseSource, LeverSource, WebsiteSource
from jobscout.domain import WorkplaceType

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name / "jobs.json").read_text(encoding="utf-8"))


def client_for(payload, status: int = 200, calls: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_greenhouse_collector_normalizes_html_country_and_language() -> None:
    calls: list[httpx.Request] = []
    source = GreenhouseSource(id="gh", company="Example", provider="greenhouse", board_token="board")
    with client_for(fixture("greenhouse"), calls=calls) as client:
        jobs = GreenhouseCollector(attempts=1).collect(source, client)

    assert jobs[0].external_id == "101"
    assert jobs[0].countries == ["DE"]
    assert jobs[0].language == "en"
    assert "<p>" not in jobs[0].description
    assert calls[0].url.params["content"] == "true"


def test_lever_collector_uses_eu_instance_and_structured_workplace() -> None:
    calls: list[httpx.Request] = []
    source = LeverSource(
        id="lever",
        company="Example",
        provider="lever",
        site="example",
        instance="eu",
        locations=["Berlin, Germany"],
    )
    with client_for(fixture("lever"), calls=calls) as client:
        jobs = LeverCollector(attempts=1).collect(source, client)

    assert calls[0].url.host == "api.eu.lever.co"
    assert calls[0].url.params["location"] == "Berlin, Germany"
    assert jobs[0].workplace_type == WorkplaceType.HYBRID
    assert jobs[0].countries == ["DE"]


def test_ashby_collector_uses_stable_url_hash_when_id_is_missing() -> None:
    source = AshbySource(id="ashby", company="Example", provider="ashby", board_name="example")
    with client_for(fixture("ashby")) as client:
        jobs = AshbyCollector(attempts=1).collect(source, client)

    assert len(jobs[0].external_id) == 24
    assert jobs[0].language == "de"
    assert jobs[0].countries == ["AT", "DE"]
    assert jobs[0].workplace_type == WorkplaceType.REMOTE


def test_ashby_collector_accepts_null_optional_workplace_fields() -> None:
    payload = fixture("ashby")
    payload["jobs"][0]["isRemote"] = None
    payload["jobs"][0]["workplaceType"] = None
    source = AshbySource(id="ashby", company="Example", provider="ashby", board_name="example")

    with client_for(payload) as client:
        jobs = AshbyCollector(attempts=1).collect(source, client)

    assert jobs[0].workplace_type == WorkplaceType.UNKNOWN


def test_collector_raises_after_bounded_http_failure() -> None:
    source = GreenhouseSource(id="gh", company="Example", provider="greenhouse", board_token="board")
    with (
        client_for({"error": "unavailable"}, status=503) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        GreenhouseCollector(attempts=1).collect(source, client)


def test_website_collector_uses_jobposting_json_ld() -> None:
    html = """
    <html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"JobPosting",
     "title":"Konstruktionsingenieur (m/w/d)",
     "description":"<p>Konstruktion mit CAD und FEM.</p>",
     "datePosted":"2026-08-01",
     "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
       "addressLocality":"Verl","addressCountry":"DE"}}}
    </script></head><body><h1>Fallback title</h1></body></html>
    """
    source = WebsiteSource(
        id="website",
        company="Example",
        provider="website",
        job_urls=["https://example.com/jobs/1"],
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    ) as client:
        jobs = WebsiteCollector(attempts=1).collect(source, client)

    assert jobs[0].title == "Konstruktionsingenieur (m/w/d)"
    assert jobs[0].description == "Konstruktion mit CAD und FEM."
    assert jobs[0].locations == ["Verl, DE"]
    assert jobs[0].countries == ["DE"]
    assert jobs[0].published_at is not None


def test_website_collector_falls_back_to_h1_and_configured_location() -> None:
    html = (
        "<html><body><h1>Konstrukteur Sondermaschinenbau</h1><p>SolidWorks</p>"
        "<h1>Jetzt bewerben</h1></body></html>"
    )
    source = WebsiteSource(
        id="website",
        company="Example",
        provider="website",
        job_urls=["https://example.com/jobs/2"],
        default_locations=["Nordwalde, Deutschland"],
        default_countries=["DE"],
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    ) as client:
        jobs = WebsiteCollector(attempts=1).collect(source, client)

    assert jobs[0].title == "Konstrukteur Sondermaschinenbau"
    assert jobs[0].locations == ["Nordwalde, Deutschland"]
    assert jobs[0].countries == ["DE"]

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from jobscout.collectors.ashby import AshbyCollector
from jobscout.collectors.greenhouse import GreenhouseCollector
from jobscout.collectors.job_board import JobBoardCollector
from jobscout.collectors.lever import LeverCollector
from jobscout.collectors.recruitee import RecruiteeCollector
from jobscout.collectors.smartrecruiters import SmartRecruitersCollector
from jobscout.collectors.website import WebsiteCollector
from jobscout.collectors.website import parse_job_page
from jobscout.config import (
    AshbySource,
    GreenhouseSource,
    JobBoardSource,
    LeverSource,
    RecruiteeSource,
    SmartRecruitersSource,
    WebsiteSource,
)
from jobscout.domain import Provider, WorkplaceType

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


def test_lever_collector_includes_requirements_from_lists() -> None:
    payload = fixture("lever")
    payload[0]["lists"] = [{"text": "Your profile", "content": "<li>Fluent German is required.</li>"}]
    payload[0]["additionalPlain"] = "Visa sponsorship unavailable."
    source = LeverSource(id="lever", company="Example", provider="lever", site="example")

    with client_for(payload) as client:
        jobs = LeverCollector(attempts=1).collect(source, client)

    assert "Fluent German is required" in jobs[0].description
    assert "Visa sponsorship unavailable" in jobs[0].description


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


def test_recruitee_collector_normalizes_policy_job() -> None:
    payload = {"offers": [{
        "id": 123,
        "title": "Climate Policy Intern",
        "description": "<p>Research EU climate legislation and draft policy briefings.</p>",
        "requirements": "<p>Excellent English skills.</p>",
        "location": "Brussels, Belgium",
        "country_code": "BE",
        "hybrid": True,
        "remote": False,
        "on_site": False,
        "published_at": "2026-09-23 10:00:08 UTC",
        "careers_url": "https://jobs.example.org/o/climate-policy-intern",
        "careers_apply_url": "https://jobs.example.org/o/climate-policy-intern/apply",
    }]}
    source = RecruiteeSource(
        id="transport-environment", company="Transport & Environment",
        provider="recruitee", account="transportenvironment",
    )
    calls: list[httpx.Request] = []
    with client_for(payload, calls=calls) as client:
        jobs = RecruiteeCollector(attempts=1).collect(source, client)

    assert calls[0].url.path == "/c/transportenvironment/careers/offers"
    assert len(jobs) == 1
    assert jobs[0].countries == ["BE"]
    assert jobs[0].workplace_type == WorkplaceType.HYBRID
    assert jobs[0].seniority == "intern"
    assert jobs[0].published_at is not None
    assert "<p>" not in jobs[0].description
    assert "Excellent English skills" in jobs[0].description


def test_smartrecruiters_collector_paginates_public_posts_and_loads_details() -> None:
    calls: list[httpx.Request] = []
    details = {
        "101": {
            "id": "101",
            "name": "Sustainability Specialist",
            "active": True,
            "visibility": "PUBLIC",
            "postingUrl": "https://jobs.smartrecruiters.com/Autodoc3/101-sustainability-specialist",
            "applyUrl": "https://jobs.smartrecruiters.com/Autodoc3/101-sustainability-specialist?oga=true",
            "location": {
                "city": "Berlin", "country": "de", "fullLocation": "Berlin, BE, Germany",
                "remote": False, "hybrid": True,
            },
            "language": {"code": "en"},
            "releasedDate": "2026-09-15T12:57:36.782Z",
            "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
            "experienceLevel": {"id": "associate", "label": "Associate"},
            "jobAd": {"sections": {
                "jobDescription": {"text": "<p>Coordinate CSRD reporting.</p>"},
                "qualifications": {"text": "<p>English required; German a plus.</p>"},
            }},
        },
        "102": {
            "id": "102",
            "name": "Policy Assistant",
            "active": True,
            "visibility": "PUBLIC",
            "location": {"city": "Brussels", "country": "BE", "remote": True, "hybrid": False},
            "language": {"code": "en-GB"},
            "typeOfEmployment": {"id": "internship", "label": "Internship"},
            "experienceLevel": {"id": "entry_level", "label": "Entry level"},
            "jobAd": {"sections": {"jobDescription": {"text": "<p>Research EU policy.</p>"}}},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/postings"):
            offset = int(request.url.params["offset"])
            content = [{"id": "101"}] if offset == 0 else [{"id": "102"}]
            return httpx.Response(200, json={"content": content, "totalFound": 2}, request=request)
        return httpx.Response(200, json=details[request.url.path.rsplit("/", 1)[1]], request=request)

    source = SmartRecruitersSource(
        id="autodoc", company="AUTODOC", provider="smartrecruiters",
        company_identifier="Autodoc3",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = SmartRecruitersCollector(attempts=1).collect(source, client)

    assert len(jobs) == 2
    assert [request.url.params["offset"] for request in calls if request.url.path.endswith("/postings")] == ["0", "1"]
    assert all(request.url.params["destination"] == "PUBLIC" for request in calls if request.url.path.endswith("/postings"))
    assert all(request.url.params["limit"] == "100" for request in calls if request.url.path.endswith("/postings"))
    assert jobs[0].external_id == "101"
    assert jobs[0].countries == ["DE"]
    assert jobs[0].workplace_type == WorkplaceType.HYBRID
    assert jobs[0].language == "en"
    assert jobs[0].published_at is not None
    assert jobs[0].seniority == "junior"
    assert jobs[0].apply_url == details["101"]["applyUrl"]
    assert jobs[0].description == "Coordinate CSRD reporting. English required; German a plus."
    assert jobs[1].countries == ["BE"]
    assert jobs[1].workplace_type == WorkplaceType.REMOTE
    assert jobs[1].language == "en"
    assert jobs[1].seniority == "intern"
    assert jobs[1].job_url == "https://jobs.smartrecruiters.com/Autodoc3/102"


def test_smartrecruiters_collector_uses_structured_senior_experience_level() -> None:
    source = SmartRecruitersSource(
        id="autodoc", company="AUTODOC", provider="smartrecruiters",
        company_identifier="Autodoc3",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={"content": [{"id": "201"}], "totalFound": 1}, request=request)
        return httpx.Response(200, json={
            "id": "201",
            "name": "ESG Analyst",
            "experienceLevel": {"id": "mid_senior_level", "label": "Mid-Senior Level"},
            "location": {"city": "Berlin", "country": "de"},
        }, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = SmartRecruitersCollector(attempts=1).collect(source, client)

    assert jobs[0].seniority == "senior"


def test_smartrecruiters_collector_skips_post_closed_during_detail_fetch() -> None:
    source = SmartRecruitersSource(
        id="autodoc", company="AUTODOC", provider="smartrecruiters",
        company_identifier="Autodoc3",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={"content": [{"id": "101"}], "totalFound": 1}, request=request)
        return httpx.Response(404, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert SmartRecruitersCollector(attempts=1).collect(source, client) == []


def test_smartrecruiters_collector_propagates_detail_failure() -> None:
    source = SmartRecruitersSource(
        id="autodoc", company="AUTODOC", provider="smartrecruiters",
        company_identifier="Autodoc3",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={"content": [{"id": "101"}], "totalFound": 1}, request=request)
        return httpx.Response(503, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        SmartRecruitersCollector(attempts=1).collect(source, client)


def test_smartrecruiters_collector_rejects_incomplete_page() -> None:
    source = SmartRecruitersSource(
        id="autodoc", company="AUTODOC", provider="smartrecruiters",
        company_identifier="Autodoc3",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [], "totalFound": 1}, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="incomplete posting list"),
    ):
        SmartRecruitersCollector(attempts=1).collect(source, client)


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


def test_job_board_collector_discovers_filtered_schema_org_jobs() -> None:
    sitemap = """<?xml version="1.0"?><urlset>
      <url><loc>https://board.example/jobs/climate-policy-officer</loc></url>
      <url><loc>https://board.example/jobs/climate-policy-intern</loc></url>
      <url><loc>https://board.example/jobs/software-engineer</loc></url>
    </urlset>"""
    detail = """<html><head><script type="application/ld+json">
    {"@type":"JobPosting","title":"Climate Policy Officer",
     "description":"<p>Research EU climate policy in English.</p>",
     "datePosted":"2026-09-25","validThrough":"2099-10-31",
     "hiringOrganization":{"name":"Climate Network"},
     "jobLocation":{"address":{"addressLocality":"Brussels","addressCountry":"BE"}}}
    </script></head></html>"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        body = sitemap if request.url.path == "/sitemap.xml" else detail
        return httpx.Response(200, text=body, request=request)

    source = JobBoardSource(
        id="policy-board",
        company="Policy Board",
        provider="job_board",
        listing_urls=["https://board.example/sitemap.xml"],
        job_url_prefixes=["https://board.example/jobs/"],
        url_term_groups=[["policy", "sustainability"]],
        excluded_url_terms=["intern", "trainee"],
        excluded_seniorities=["intern"],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = JobBoardCollector(attempts=1).collect(source, client)

    assert calls == ["/sitemap.xml", "/jobs/climate-policy-officer"]
    assert len(jobs) == 1
    assert jobs[0].company == "Climate Network"
    assert jobs[0].countries == ["BE"]
    assert jobs[0].provider.value == "job_board"


def test_job_board_collector_skips_expired_and_structured_intern_jobs() -> None:
    sitemap = """<urlset>
      <url><loc>https://board.example/jobs/policy-assistant</loc></url>
      <url><loc>https://board.example/jobs/policy-officer</loc></url>
    </urlset>"""
    pages = {
        "/jobs/policy-assistant": """<script type="application/ld+json">
          {"@type":"JobPosting","title":"Policy Assistant","employmentType":"Internship",
           "description":"English climate policy work.","validThrough":"2099-01-01"}
        </script>""",
        "/jobs/policy-officer": """<script type="application/ld+json">
          {"@type":"JobPosting","title":"Policy Officer",
           "description":"English climate policy work.","validThrough":"2000-01-01"}
        </script>""",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = sitemap if request.url.path == "/sitemap.xml" else pages[request.url.path]
        return httpx.Response(200, text=body, request=request)

    source = JobBoardSource(
        id="policy-board", company="Policy Board", provider="job_board",
        listing_urls=["https://board.example/sitemap.xml"],
        job_url_prefixes=["https://board.example/jobs/"],
        excluded_seniorities=["intern"],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert JobBoardCollector(attempts=1).collect(source, client) == []


def test_job_board_collector_parses_eurojobsites_fallback() -> None:
    listing = '<a href="/job_display/123/ESG_Analyst_Example_Amsterdam_Netherlands">Job</a>'
    detail = """<html><body><h1>ESG Analyst</h1><div class="jobDisplay">
      <h2>ESG Analyst</h2><h2>Example Climate</h2><h2>Amsterdam, Netherlands</h2>
      <p>Support CSRD reporting and climate policy research in English.</p>
    </div></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = listing if request.url.path == "/" else detail
        return httpx.Response(200, text=body, request=request)

    source = JobBoardSource(
        id="climate-board", company="Climate Board", provider="job_board",
        listing_urls=["https://climate.example/"],
        job_url_prefixes=["https://climate.example/job_display/"],
        page_format="eurojobsites",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = JobBoardCollector(attempts=1).collect(source, client)

    assert jobs[0].company == "Example Climate"
    assert jobs[0].locations == ["Amsterdam, Netherlands"]
    assert jobs[0].countries == ["NL"]
    assert "CSRD reporting" in jobs[0].description


def test_job_board_collector_skips_closed_pages_without_jobposting() -> None:
    listing = '<a href="/jobs/climate-policy-officer">Job</a>'
    closed = "<html><body><h1>Find your new job</h1>This vacancy has closed.</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=listing if request.url.path == "/" else closed, request=request)

    source = JobBoardSource(
        id="policy-board", company="Policy Board", provider="job_board",
        listing_urls=["https://board.example/"],
        job_url_prefixes=["https://board.example/jobs/"],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert JobBoardCollector(attempts=1).collect(source, client) == []


def test_job_board_collector_follows_sitemap_index_without_treating_it_as_job() -> None:
    pages = {
        "/sitemap.xml": """<sitemapindex><sitemap>
          <loc>https://board.example/jobs-sitemap.xml</loc>
        </sitemap></sitemapindex>""",
        "/jobs-sitemap.xml": """<urlset><url>
          <loc>https://board.example/jobs/climate-policy-officer</loc>
        </url></urlset>""",
        "/jobs/climate-policy-officer": """<script type="application/ld+json">
          {"@type":"JobPosting","title":"Climate Policy Officer",
           "description":"Research climate policy in English."}
        </script>""",
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=pages[request.url.path], request=request)

    source = JobBoardSource(
        id="policy-board", company="Policy Board", provider="job_board",
        listing_urls=["https://board.example/sitemap.xml"],
        job_url_prefixes=["https://board.example/jobs/"],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = JobBoardCollector(attempts=1).collect(source, client)

    assert calls == ["/sitemap.xml", "/jobs-sitemap.xml", "/jobs/climate-policy-officer"]
    assert len(jobs) == 1


def test_job_board_excludes_internship_with_neutral_title_and_slug() -> None:
    listing = '<a href="/jobs/policy-assistant">Job</a>'
    detail = """<script type="application/ld+json">
      {"@type":"JobPosting","title":"Policy Assistant",
       "description":"Contract type: Internship. Research EU climate policy in English.",
       "validThrough":"2099-12-31"}
    </script>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=listing if request.url.path == "/" else detail, request=request)

    source = JobBoardSource(
        id="policy-board", company="Policy Board", provider="job_board",
        listing_urls=["https://board.example/"],
        job_url_prefixes=["https://board.example/jobs/"],
        excluded_seniorities=["intern"],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert JobBoardCollector(attempts=1).collect(source, client) == []


def test_job_page_treats_date_only_expiry_as_end_of_day() -> None:
    today = datetime.now(UTC).date().isoformat()
    html = f"""<script type="application/ld+json">
      {{"@type":"JobPosting","title":"Climate Policy Officer",
        "validThrough":"{today}","description":"Research climate policy."}}
    </script>"""
    job = parse_job_page(
        source_id="board", provider=Provider.JOB_BOARD, company="Board",
        url="https://board.example/jobs/1", html=html, require_job_posting=True,
    )
    assert job is not None


def test_eurojobsites_body_survives_self_closing_html_tags() -> None:
    html = """<h1>ESG Analyst</h1><div class="jobDisplay">
      <h2>ESG Analyst</h2><h2>Climate Company</h2><h2>Berlin, Germany</h2>
      <p>First requirement<br/>Second requirement</p><p>Third requirement</p>
    </div>"""
    job = parse_job_page(
        source_id="board", provider=Provider.JOB_BOARD, company="Board",
        url="https://board.example/job_display/1", html=html, page_format="eurojobsites",
    )
    assert job is not None
    assert "Third requirement" in job.description


def test_job_page_infers_senior_from_required_experience() -> None:
    html = """<script type="application/ld+json">
      {"@type":"JobPosting","title":"Programme Manager",
       "description":"At least five years of experience working on EU climate policy."}
    </script>"""
    job = parse_job_page(
        source_id="board", provider=Provider.JOB_BOARD, company="Board",
        url="https://board.example/jobs/1", html=html, require_job_posting=True,
    )
    assert job is not None
    assert job.seniority == "senior"

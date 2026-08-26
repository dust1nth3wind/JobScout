from __future__ import annotations

import pytest

from jobscout.config import ProfileConfig
from jobscout.domain import CollectedJob, Provider, WorkplaceType
from jobscout.matching import Matcher


def job(**overrides) -> CollectedJob:
    values = {
        "source_id": "source",
        "provider": Provider.GREENHOUSE,
        "external_id": "1",
        "company": "Example",
        "title": "Senior Python Engineer",
        "description": "We are looking for an engineer with Python and API experience.",
        "job_url": "https://example.com/jobs/1",
        "locations": ["Berlin, Germany"],
        "countries": ["DE"],
        "workplace_type": WorkplaceType.REMOTE,
        "language": "en",
        "seniority": "senior",
    }
    values.update(overrides)
    return CollectedJob(**values)


def profile(**overrides) -> ProfileConfig:
    values = {
        "id": "friend-a",
        "display_name": "Friend A",
        "required_languages": ["en"],
        "allowed_regions": ["europe"],
        "remote_preference": "prefer",
        "required_skills": ["python"],
        "preferred_skills": ["api"],
        "preferred_terms": ["engineer"],
        "allowed_seniorities": ["senior"],
        "minimum_score": 40,
    }
    values.update(overrides)
    return ProfileConfig(**values)


def test_matching_job_gets_explainable_full_score() -> None:
    result = Matcher().evaluate(job(), profile())

    assert result.score == 100
    assert result.meets_threshold is True
    assert result.excluded is False
    assert any("skills matched" in reason for reason in result.reasons)


def test_explicit_country_mismatch_is_hard_exclusion() -> None:
    result = Matcher().evaluate(job(countries=["US"]), profile())

    assert result.excluded is True
    assert "location outside allowed countries" in result.exclusion_reasons


def test_excluded_location_term_only_checks_job_locations() -> None:
    excluded = Matcher().evaluate(
        job(locations=["Dresden, Sachsen, Deutschland"]),
        profile(excluded_location_terms=["sachsen"]),
    )
    mentioned_in_description = Matcher().evaluate(
        job(description="We collaborate with a team in Sachsen."),
        profile(excluded_location_terms=["sachsen"]),
    )

    assert excluded.excluded is True
    assert "excluded locations: sachsen" in excluded.exclusion_reasons
    assert mentioned_in_description.excluded is False


def test_unknown_location_and_language_are_not_hard_exclusions() -> None:
    result = Matcher().evaluate(job(countries=[], language="unknown"), profile())

    assert result.excluded is False
    assert result.score < 100
    assert "location unknown" in result.reasons
    assert "language unknown" in result.reasons


def test_missing_required_skill_and_excluded_term_are_reported() -> None:
    result = Matcher().evaluate(
        job(title="Sales Manager", description="This job requires cold calling."),
        profile(required_skills=["python"], excluded_terms=["cold calling"]),
    )

    assert result.excluded is True
    assert len(result.exclusion_reasons) == 2


def test_job_urls_must_be_safe_absolute_http_urls() -> None:
    with pytest.raises(ValueError, match="HTTP"):
        job(job_url="javascript:alert(1)")


def test_skill_matching_does_not_use_partial_words() -> None:
    result = Matcher().evaluate(
        job(title="JavaScript Engineer", description="We build browser applications."),
        profile(required_skills=["java"], preferred_skills=[]),
    )

    assert result.excluded is True
    assert "missing required skills: java" in result.exclusion_reasons


def test_preferred_title_terms_are_alternatives() -> None:
    result = Matcher().evaluate(
        job(title="Konstruktionsingenieur", description=""),
        profile(
            required_skills=[],
            preferred_skills=[],
            preferred_terms=["konstrukteur", "konstruktionsingenieur", "design engineer"],
        ),
    )

    assert result.score == 100


def test_required_title_terms_are_alternative_hard_filters() -> None:
    matching = Matcher().evaluate(
        job(title="Mechanical Engineer"),
        profile(required_title_terms=["konstrukteur", "mechanical engineer"]),
    )
    unrelated = Matcher().evaluate(
        job(title="Executive Assistant"),
        profile(required_title_terms=["konstrukteur", "mechanical engineer"]),
    )

    assert matching.excluded is False
    assert unrelated.excluded is True
    assert "title does not match the required role family" in unrelated.exclusion_reasons

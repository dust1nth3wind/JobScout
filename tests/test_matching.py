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


def xinning_profile(**overrides) -> ProfileConfig:
    """The proposed Xinning settings, independent of the private local TOML file."""
    title_terms = [
        "policy", "compliance", "regulatory", "public affairs", "research",
        "environmental", "environment", "sustainability", "sustainable", "climate",
        "carbon", "standards", "standardisation", "standardization",
        "business developer", "business development", "market intelligence",
        "e-mobility", "electrification", "circular economy",
        "esg", "impact analyst", "project coordinator", "project officer",
        "program coordinator", "programme coordinator", "program officer",
        "programme officer", "market analyst", "market research",
        "supply chain analyst", "partnerships associate",
    ]
    values = {
        "id": "friend-a",
        "display_name": "Xinning",
        "required_languages": ["en"],
        "allowed_countries": ["BE", "DE", "NL"],
        "remote_preference": "any",
        "preferred_skill_groups": [
            ["mandarin", "chinese language", "fluency in chinese", "fluent in chinese", "chinese and english"],
            ["master's degree", "masters degree", "master of science", "msc"],
        ],
        "preferred_industry_groups": [
            ["climate policy", "environmental policy", "carbon pricing", "cbam", "eu ets", "decarbonisation", "decarbonization", "energy transition", "ghg accounting"],
            ["european standards", "standardisation", "standardization", "environmental compliance", "sustainability regulation", "esg", "csrd", "esrs", "sustainability reporting", "eu taxonomy"],
            ["e-mobility", "electric mobility", "electrification", "battery", "ev charging", "circular economy", "sustainable mobility", "clean transport", "sustainable supply chain"],
        ],
        "excluded_terms": [
            "fluent german", "fluent in german", "fluency in german", "native german",
            "german is required", "german is mandatory", "fluency in bulgarian", "fluency in arabic",
        ],
        "required_title_terms": title_terms,
        "preferred_terms": title_terms,
        "allowed_seniorities": ["junior"],
        "minimum_score": 65,
        "weights": {
            "skills": 10, "industry": 30, "title": 25,
            "seniority": 10, "location": 15, "language": 10,
        },
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


def test_skill_synonyms_count_as_one_alternative_group() -> None:
    grouped_profile = profile(
        required_languages=[],
        allowed_regions=[],
        remote_preference="any",
        required_skills=[],
        preferred_skills=[],
        preferred_skill_groups=[["nx", "siemens nx"], ["cfd"]],
        preferred_terms=[],
        allowed_seniorities=[],
    )

    one_synonym = Matcher().evaluate(
        job(description="Mechanical design with Siemens NX.", language="unknown"),
        grouped_profile,
    )
    repeated_synonyms = Matcher().evaluate(
        job(description="Mechanical design with NX and Siemens NX.", language="unknown"),
        grouped_profile,
    )

    assert one_synonym.score == 50
    assert repeated_synonyms.score == 50


def test_one_matching_preferred_industry_earns_full_industry_score() -> None:
    industry_profile = profile(
        required_languages=[],
        allowed_regions=[],
        remote_preference="any",
        required_skills=[],
        preferred_skills=[],
        preferred_terms=[],
        allowed_seniorities=[],
        preferred_industry_groups=[
            ["luft- und raumfahrttechnik", "aerospace"],
            ["automobilindustrie", "automotive"],
        ],
    )

    result = Matcher().evaluate(
        job(description="We develop aerospace structures.", language="unknown"),
        industry_profile,
    )

    assert result.score == 100
    assert "preferred industries matched" in result.reasons[0]


def test_unknown_seniority_receives_neutral_half_credit() -> None:
    seniority_profile = profile(
        required_languages=[],
        allowed_regions=[],
        remote_preference="any",
        required_skills=[],
        preferred_skills=[],
        preferred_terms=[],
        allowed_seniorities=["junior"],
    )

    result = Matcher().evaluate(job(seniority="unknown", language="unknown"), seniority_profile)

    assert result.score == 50
    assert "seniority unknown (neutral score)" in result.reasons


@pytest.mark.parametrize(
    ("title", "description", "country", "workplace_type"),
    [
        pytest.param(
            "Policy Assistant, Carbon Pricing and Trade", "Climate policy and the EU ETS.",
            "BE", WorkplaceType.HYBRID, id="bellona-policy-assistant",
        ),
        pytest.param(
            "Project Manager for European Standards in Sustainable Systems and Consumers",
            "Develop European standards for sustainable products.",
            "BE", WorkplaceType.ONSITE, id="cen-cenelec-project-manager",
        ),
        pytest.param(
            "Business Developer", "Develop e-mobility and battery partnerships.",
            "BE", WorkplaceType.ONSITE, id="toyota-business-developer",
        ),
        pytest.param(
            "Customer Success Manager, Sustainability",
            "Help companies improve sustainable supply chain performance.",
            "NL", WorkplaceType.REMOTE, id="worldly-sustainability-customer-success",
        ),
        pytest.param(
            "Sustainability Specialist", "Support CSRD and ESRS reporting.",
            "DE", WorkplaceType.HYBRID, id="autodoc-sustainability-specialist",
        ),
    ],
)
def test_xinning_reference_roles_meet_threshold(
    title: str, description: str, country: str, workplace_type: WorkplaceType
) -> None:
    result = Matcher().evaluate(
        job(
            title=title,
            description=description,
            locations=[country],
            countries=[country],
            workplace_type=workplace_type,
            seniority="junior",
        ),
        xinning_profile(),
    )

    assert result.excluded is False
    assert result.meets_threshold is True
    assert result.score >= 65


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("ESG Analyst", "CSRD reporting and the EU taxonomy."),
        ("Impact Analyst", "GHG accounting and the energy transition."),
        ("Project Coordinator", "Coordinate climate policy projects."),
        ("Programme Officer", "Support environmental policy work."),
        ("Market Analyst", "Research clean transport and EV charging."),
        ("Supply Chain Analyst", "Improve sustainable supply chain practices."),
        ("Partnerships Associate", "Build sustainable mobility partnerships."),
    ],
)
def test_xinning_adjacent_role_titles_are_accepted(title: str, description: str) -> None:
    result = Matcher().evaluate(
        job(title=title, description=description, seniority="junior"),
        xinning_profile(),
    )

    assert result.excluded is False
    assert result.meets_threshold is True


@pytest.mark.parametrize(
    ("overrides", "expected_exclusion"),
    [
        pytest.param(
            {"title": "Backend Developer", "description": "Build EV charging software."},
            "title does not match the required role family", id="generic-tech",
        ),
        pytest.param(
            {"title": "Customer Success Manager", "description": "Work on sustainability reporting."},
            "title does not match the required role family", id="generic-customer-success",
        ),
        pytest.param(
            {"title": "ESG Analyst", "description": "CSRD work. Applicants must be fluent in German."},
            "excluded terms: fluent in german", id="mandatory-german",
        ),
        pytest.param(
            {"title": "Senior Policy Analyst", "description": "Climate policy.", "seniority": "senior"},
            "seniority senior is not allowed", id="senior",
        ),
        pytest.param(
            {"title": "Lead ESG Analyst", "description": "CSRD reporting.", "seniority": "lead"},
            "seniority lead is not allowed", id="lead",
        ),
        pytest.param(
            {"title": "Sustainability Specialist", "description": "CSRD reporting.", "countries": ["GB"]},
            "location outside allowed countries", id="outside-countries",
        ),
        pytest.param(
            {"title": "Sustainability Trainee", "description": "CSRD reporting.", "seniority": "intern"},
            "seniority intern is not allowed", id="traineeship",
        ),
        pytest.param(
            {"title": "Sustainability Specialist", "description": "CSRD reporting.", "language": "de"},
            "language de is not allowed", id="non-english-ad",
        ),
    ],
)
def test_xinning_hard_filters_keep_unwanted_roles_out(
    overrides: dict, expected_exclusion: str
) -> None:
    job_values = {"title": "ESG Analyst", "description": "CSRD reporting.", "seniority": "junior"}
    job_values.update(overrides)
    result = Matcher().evaluate(
        job(**job_values),
        xinning_profile(),
    )

    assert result.excluded is True
    assert result.meets_threshold is False
    assert expected_exclusion in result.exclusion_reasons


def test_xinning_generic_project_coordinator_stays_below_threshold() -> None:
    result = Matcher().evaluate(
        job(title="Project Coordinator", description="Coordinate software projects.", seniority="junior"),
        xinning_profile(),
    )

    assert result.excluded is False
    assert result.score == 60
    assert result.meets_threshold is False


@pytest.mark.parametrize(
    ("extra_description", "expected_score"),
    [
        ("", 90),
        ("Mandarin preferred.", 95),
        ("A Master of Science is preferred.", 95),
        ("Mandarin and a Master of Science are preferred.", 100),
    ],
)
def test_xinning_mandarin_and_masters_are_optional_bonuses(
    extra_description: str, expected_score: int
) -> None:
    result = Matcher().evaluate(
        job(
            title="Policy Assistant",
            description=f"Support climate policy. {extra_description}",
            seniority="junior",
        ),
        xinning_profile(),
    )

    assert result.excluded is False
    assert result.meets_threshold is True
    assert result.score == expected_score


@pytest.mark.parametrize(
    ("description", "expected_reason"),
    [
        ("Candidates must already have the right to work in Belgium.", "existing work authorization required"),
        ("No visa sponsorship is offered.", "visa sponsorship appears unavailable"),
        ("Visa and relocation support is provided.", "visa sponsorship appears available"),
    ],
)
def test_sponsorship_is_annotated_without_excluding_job(
    description: str, expected_reason: str
) -> None:
    result = Matcher().evaluate(job(description=description), profile())

    assert result.excluded is False
    assert expected_reason in result.reasons

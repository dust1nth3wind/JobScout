"""Deterministic, explainable profile matching without an LLM."""

from __future__ import annotations

import re

from jobscout.config import ProfileConfig
from jobscout.domain import CollectedJob, MatchResult, WorkplaceType
from jobscout.normalization import normalized_text

DACH = {"DE", "AT", "CH"}
EUROPE = {
    "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI",
    "FR", "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU", "MT", "MD",
    "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "SM", "RS", "SK", "SI", "ES",
    "SE", "CH", "UA", "GB", "VA",
}


def _contains(haystack: str, needle: str) -> bool:
    normalized_needle = normalized_text(needle)
    return bool(normalized_needle) and bool(
        re.search(rf"(?<![a-z0-9]){re.escape(normalized_needle)}(?![a-z0-9])", haystack)
    )


def _allowed_countries(profile: ProfileConfig) -> set[str]:
    allowed = set(profile.allowed_countries)
    for region in profile.allowed_regions:
        allowed.update(DACH if region == "dach" else EUROPE)
    return allowed


def _unique_groups(groups: list[list[str]]) -> list[list[str]]:
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        signature = tuple(group)
        if signature and signature not in seen:
            unique.append(group)
            seen.add(signature)
    return unique


def _matched_groups(haystack: str, groups: list[list[str]]) -> list[list[str]]:
    return [group for group in groups if any(_contains(haystack, term) for term in group)]


def _group_labels(groups: list[list[str]]) -> str:
    return ", ".join(" / ".join(group) for group in groups)


class Matcher:
    def evaluate(self, job: CollectedJob, profile: ProfileConfig) -> MatchResult:
        haystack = normalized_text(f"{job.title} {job.description} {' '.join(job.skills)}")
        location_text = normalized_text(" ".join(job.locations))
        exclusions: list[str] = []
        reasons: list[str] = []

        excluded_terms = [term for term in profile.excluded_terms if _contains(haystack, term)]
        if excluded_terms:
            exclusions.append(f"excluded terms: {', '.join(excluded_terms)}")

        excluded_locations = [
            term for term in profile.excluded_location_terms if _contains(location_text, term)
        ]
        if excluded_locations:
            exclusions.append(f"excluded locations: {', '.join(excluded_locations)}")

        if profile.required_title_terms:
            title_text = normalized_text(job.title)
            if not any(_contains(title_text, term) for term in profile.required_title_terms):
                exclusions.append("title does not match the required role family")

        missing_skills = [skill for skill in profile.required_skills if not _contains(haystack, skill)]
        if missing_skills:
            exclusions.append(f"missing required skills: {', '.join(missing_skills)}")

        if job.language != "unknown" and profile.required_languages and job.language not in profile.required_languages:
            exclusions.append(f"language {job.language} is not allowed")

        allowed_countries = _allowed_countries(profile)
        if allowed_countries and job.countries and allowed_countries.isdisjoint(job.countries):
            exclusions.append("location outside allowed countries")

        if job.seniority in profile.excluded_seniorities:
            exclusions.append(f"seniority {job.seniority} is excluded")
        if (
            job.seniority != "unknown"
            and profile.allowed_seniorities
            and job.seniority not in profile.allowed_seniorities
        ):
            exclusions.append(f"seniority {job.seniority} is not allowed")

        if profile.remote_preference == "require" and job.workplace_type == WorkplaceType.ONSITE:
            exclusions.append("on-site role conflicts with required remote work")
        if profile.remote_preference == "exclude" and job.workplace_type == WorkplaceType.REMOTE:
            exclusions.append("remote role is excluded")

        earned = 0.0
        available = 0.0
        weights = profile.weights

        skill_groups = _unique_groups(
            [[skill] for skill in profile.required_skills + profile.preferred_skills]
            + profile.preferred_skill_groups
        )
        if skill_groups:
            available += weights.skills
            matches = _matched_groups(haystack, skill_groups)
            earned += weights.skills * len(matches) / len(skill_groups)
            if matches:
                reasons.append(f"skills matched: {_group_labels(matches)}")

        if profile.preferred_industry_groups:
            available += weights.industry
            industry_matches = _matched_groups(haystack, profile.preferred_industry_groups)
            if industry_matches:
                earned += weights.industry
                reasons.append(f"preferred industries matched: {_group_labels(industry_matches)}")

        if profile.preferred_terms:
            available += weights.title
            title_text = normalized_text(job.title)
            matches = [term for term in profile.preferred_terms if _contains(title_text, term)]
            if matches:
                earned += weights.title
            if matches:
                reasons.append(f"preferred title terms: {', '.join(matches)}")

        if profile.allowed_seniorities:
            available += weights.seniority
            if job.seniority in profile.allowed_seniorities:
                earned += weights.seniority
                reasons.append(f"seniority matched: {job.seniority}")
            elif job.seniority == "unknown":
                earned += weights.seniority * 0.5
                reasons.append("seniority unknown (neutral score)")

        if allowed_countries or profile.remote_preference != "any":
            available += weights.location
            location_fraction = 0.0
            if job.countries and (not allowed_countries or not allowed_countries.isdisjoint(job.countries)):
                location_fraction = 1.0
                reasons.append("location matched")
            elif not job.countries:
                location_fraction = 0.25
                reasons.append("location unknown")
            if profile.remote_preference == "prefer" and job.workplace_type == WorkplaceType.REMOTE:
                location_fraction = max(location_fraction, 1.0)
                reasons.append("remote preference matched")
            elif profile.remote_preference == "require" and job.workplace_type in {
                WorkplaceType.REMOTE, WorkplaceType.HYBRID
            }:
                location_fraction = max(location_fraction, 1.0)
                reasons.append("remote requirement matched")
            earned += weights.location * location_fraction

        languages = list(dict.fromkeys(profile.required_languages + profile.preferred_languages))
        if languages:
            available += weights.language
            if job.language in languages:
                earned += weights.language
                reasons.append(f"language matched: {job.language}")
            elif job.language == "unknown":
                earned += weights.language * 0.25
                reasons.append("language unknown")

        score = round(100 * earned / available) if available else 0
        excluded = bool(exclusions)
        return MatchResult(
            score=max(0, min(100, score)),
            excluded=excluded,
            meets_threshold=not excluded and score >= profile.minimum_score,
            exclusion_reasons=exclusions,
            reasons=reasons,
        )

"""TOML configuration loading and validation."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)

from jobscout.domain import Provider


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_path: Path = Path("data/jobscout.sqlite3")
    request_timeout_seconds: PositiveFloat = 20
    retry_attempts: PositiveInt = Field(default=2, le=5)


class SourceBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    company: str = Field(min_length=1)
    enabled: bool = True


class GreenhouseSource(SourceBase):
    provider: Literal[Provider.GREENHOUSE]
    board_token: str = Field(min_length=1)


class LeverSource(SourceBase):
    provider: Literal[Provider.LEVER]
    site: str = Field(min_length=1)
    instance: Literal["global", "eu"] = "global"
    locations: list[str] = Field(default_factory=list)


class AshbySource(SourceBase):
    provider: Literal[Provider.ASHBY]
    board_name: str = Field(min_length=1)
    include_compensation: bool = True


class WebsiteSource(SourceBase):
    provider: Literal[Provider.WEBSITE]
    job_urls: list[str] = Field(min_length=1)
    default_locations: list[str] = Field(default_factory=list)
    default_countries: list[str] = Field(default_factory=list)

    @field_validator("job_urls")
    @classmethod
    def absolute_http_urls(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith(("http://", "https://")):
                raise ValueError("website job URLs must be absolute HTTP(S) URLs")
        return values

    @field_validator("default_countries")
    @classmethod
    def uppercase_countries(cls, values: list[str]) -> list[str]:
        return sorted({value.upper() for value in values if value})


SourceConfig = Annotated[
    GreenhouseSource | LeverSource | AshbySource | WebsiteSource,
    Field(discriminator="provider"),
]


class MatchingWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: int = Field(default=30, ge=0)
    industry: int = Field(default=15, ge=0)
    title: int = Field(default=20, ge=0)
    seniority: int = Field(default=10, ge=0)
    location: int = Field(default=15, ge=0)
    language: int = Field(default=10, ge=0)


class ProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1)
    required_languages: list[Literal["de", "en"]] = Field(default_factory=list)
    preferred_languages: list[Literal["de", "en"]] = Field(default_factory=list)
    allowed_regions: list[Literal["europe", "dach"]] = Field(default_factory=list)
    allowed_countries: list[str] = Field(default_factory=list)
    remote_preference: Literal["any", "prefer", "require", "exclude"] = "any"
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_skill_groups: list[list[str]] = Field(default_factory=list)
    preferred_industry_groups: list[list[str]] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    excluded_location_terms: list[str] = Field(default_factory=list)
    required_title_terms: list[str] = Field(default_factory=list)
    preferred_terms: list[str] = Field(default_factory=list)
    allowed_seniorities: list[str] = Field(default_factory=list)
    excluded_seniorities: list[str] = Field(default_factory=list)
    minimum_score: int = Field(default=40, ge=0, le=100)
    weights: MatchingWeights = Field(default_factory=MatchingWeights)

    @model_validator(mode="after")
    def normalize_values(self) -> ProfileConfig:
        self.allowed_countries = sorted({country.upper() for country in self.allowed_countries})
        for field_name in (
            "required_skills",
            "preferred_skills",
            "excluded_terms",
            "excluded_location_terms",
            "required_title_terms",
            "preferred_terms",
            "allowed_seniorities",
            "excluded_seniorities",
        ):
            values = getattr(self, field_name)
            setattr(self, field_name, list(dict.fromkeys(value.strip().lower() for value in values if value.strip())))
        for field_name in ("preferred_skill_groups", "preferred_industry_groups"):
            normalized_groups: list[list[str]] = []
            seen_groups: set[tuple[str, ...]] = set()
            for group in getattr(self, field_name):
                normalized = list(
                    dict.fromkeys(value.strip().lower() for value in group if value.strip())
                )
                signature = tuple(normalized)
                if signature and signature not in seen_groups:
                    normalized_groups.append(normalized)
                    seen_groups.add(signature)
            setattr(self, field_name, normalized_groups)
        return self

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JobScoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: AppSettings = Field(default_factory=AppSettings)
    sources: list[SourceConfig] = Field(default_factory=list)
    profiles: list[ProfileConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> JobScoutConfig:
        for label, items in (("source", self.sources), ("profile", self.profiles)):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} id")
        return self


@dataclass(frozen=True)
class LoadedConfig:
    path: Path
    settings: JobScoutConfig

    @property
    def database_path(self) -> Path:
        configured = self.settings.app.database_path
        if configured.is_absolute():
            return configured
        return (self.path.parent / configured).resolve()


def resolve_config_path(explicit_path: str | Path | None = None) -> Path:
    candidate = explicit_path or os.environ.get("JOBSCOUT_CONFIG") or Path("config/jobscout.toml")
    return Path(candidate).expanduser().resolve()


def load_config(explicit_path: str | Path | None = None) -> LoadedConfig:
    path = resolve_config_path(explicit_path)
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Configuration not found: {path}. Copy config/jobscout.example.toml to config/jobscout.toml."
        ) from exc
    return LoadedConfig(path=path, settings=JobScoutConfig.model_validate(raw))

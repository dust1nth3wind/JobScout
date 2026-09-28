from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobscout.config import load_config


def test_loads_config_and_resolves_database_relative_to_config(config_path: Path) -> None:
    loaded = load_config(config_path)

    assert loaded.settings.profiles[0].id == "friend-a"
    assert loaded.settings.sources[0].provider.value == "greenhouse"
    assert loaded.database_path == config_path.parent / "jobscout.sqlite3"


def test_rejects_duplicate_profile_ids(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.toml"
    path.write_text(
        """
[[profiles]]
id = "same"
display_name = "One"
[[profiles]]
id = "same"
display_name = "Two"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="duplicate profile id"):
        load_config(path)


def test_rejects_provider_specific_missing_field(tmp_path: Path) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text(
        """
[[sources]]
id = "lever-one"
company = "Example"
provider = "lever"
[[profiles]]
id = "profile"
display_name = "Profile"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="site"):
        load_config(path)


def test_smartrecruiters_source_requires_company_identifier(tmp_path: Path) -> None:
    path = tmp_path / "smartrecruiters.toml"
    path.write_text(
        """
[[sources]]
id = "autodoc"
company = "AUTODOC"
provider = "smartrecruiters"
[[profiles]]
id = "friend-a"
display_name = "Xinning"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="company_identifier"):
        load_config(path)

    path.write_text(path.read_text(encoding="utf-8").replace(
        'provider = "smartrecruiters"',
        'provider = "smartrecruiters"\ncompany_identifier = "Autodoc3"',
    ), encoding="utf-8")
    assert load_config(path).settings.sources[0].company_identifier == "Autodoc3"


def test_missing_config_has_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="jobscout.example.toml"):
        load_config(tmp_path / "missing.toml")


def test_rejects_source_with_unknown_profile_id(tmp_path: Path) -> None:
    path = tmp_path / "invalid-profile-reference.toml"
    path.write_text(
        """
[[sources]]
id = "policy-board"
company = "Policy Board"
provider = "recruitee"
account = "policyboard"
profile_ids = ["missing"]
[[profiles]]
id = "friend-a"
display_name = "Friend A"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unknown profiles"):
        load_config(path)


def test_loads_job_board_source_and_normalizes_countries(tmp_path: Path) -> None:
    path = tmp_path / "job-board.toml"
    path.write_text(
        """
[[sources]]
id = "policy-board"
company = "Policy Board"
provider = "job_board"
listing_urls = ["https://example.com/sitemap.xml"]
job_url_prefixes = ["https://example.com/jobs/"]
url_term_groups = [["policy", "sustainability"]]
excluded_url_terms = ["intern", "trainee"]
excluded_seniorities = ["intern"]
default_countries = ["be"]
[[profiles]]
id = "friend-a"
display_name = "Xinning"
""",
        encoding="utf-8",
    )

    source = load_config(path).settings.sources[0]
    assert source.provider.value == "job_board"
    assert source.default_countries == ["BE"]

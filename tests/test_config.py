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


def test_missing_config_has_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="jobscout.example.toml"):
        load_config(tmp_path / "missing.toml")

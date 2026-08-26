from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def config_text() -> str:
    return """
[app]
database_path = "jobscout.sqlite3"
request_timeout_seconds = 5
retry_attempts = 1

[[sources]]
id = "greenhouse-one"
company = "Example"
provider = "greenhouse"
board_token = "example"
enabled = true

[[profiles]]
id = "friend-a"
display_name = "Friend A"
required_languages = ["en"]
allowed_regions = ["europe"]
remote_preference = "prefer"
preferred_skills = ["python"]
preferred_terms = ["engineer"]
minimum_score = 40
"""


@pytest.fixture
def config_path(tmp_path: Path, config_text: str) -> Path:
    path = tmp_path / "jobscout.toml"
    path.write_text(config_text, encoding="utf-8")
    return path

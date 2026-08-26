from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import func, select

from jobscout.config import load_config
from jobscout.db import (
    JobPosting,
    JobState,
    create_db_engine,
    create_session_factory,
    init_db,
    set_job_status,
)
from jobscout.domain import CollectedJob, JobStatus, Provider
from jobscout.scanner import Scanner


class MutableCollector:
    def __init__(self) -> None:
        self.fail_sources: set[str] = set()
        self.title = "Python Engineer"

    def collect(self, source, client):
        if source.id in self.fail_sources:
            raise httpx.ConnectError("offline")
        return [
            CollectedJob(
                source_id=source.id,
                provider=Provider.GREENHOUSE,
                external_id="job-1",
                company=source.company,
                title=self.title,
                description="We are looking for an engineer with Python experience.",
                job_url=f"https://example.com/{source.id}/job-1",
                locations=["Berlin, Germany"],
                countries=["DE"],
                language="en",
            )
        ]


def two_source_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[app]
database_path = "db.sqlite3"
retry_attempts = 1
[[sources]]
id = "one"
company = "One"
provider = "greenhouse"
board_token = "one"
[[sources]]
id = "two"
company = "Two"
provider = "greenhouse"
board_token = "two"
[[profiles]]
id = "friend-a"
display_name = "Friend A"
required_languages = ["en"]
allowed_regions = ["europe"]
preferred_skills = ["python"]
minimum_score = 10
""",
        encoding="utf-8",
    )
    return path


def test_scan_continues_after_source_failure_and_is_idempotent(tmp_path: Path) -> None:
    loaded = load_config(two_source_config(tmp_path))
    engine = create_db_engine(loaded.database_path)
    init_db(engine)
    collector = MutableCollector()
    collector.fail_sources.add("two")
    scanner = Scanner(loaded, engine, collectors={Provider.GREENHOUSE: collector}, client=httpx.Client())

    first = scanner.run()
    collector.title = "Updated Python Engineer"
    second = scanner.run(source_id="one")

    assert first.status == "partial"
    assert first.sources_succeeded == 1
    assert first.errors and first.errors[0].startswith("two: ConnectError")
    assert second.status == "success"
    sessions = create_session_factory(engine)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JobPosting)) == 1
        assert session.scalar(select(JobPosting)).title == "Updated Python Engineer"


def test_status_survives_rescan_and_failed_scan_does_not_deactivate(tmp_path: Path) -> None:
    loaded = load_config(two_source_config(tmp_path))
    engine = create_db_engine(loaded.database_path)
    init_db(engine)
    collector = MutableCollector()
    scanner = Scanner(loaded, engine, collectors={Provider.GREENHOUSE: collector}, client=httpx.Client())
    scanner.run(source_id="one")

    sessions = create_session_factory(engine)
    with sessions() as session:
        persisted_job = session.scalar(select(JobPosting))
        set_job_status(session, persisted_job.id, "friend-a", JobStatus.INTERESTING)
        session.commit()

    collector.fail_sources.add("one")
    failed = scanner.run(source_id="one")

    assert failed.status == "failed"
    with sessions() as session:
        assert session.scalar(select(JobPosting)).is_active is True
        assert session.scalar(select(JobState)).status == "interesting"

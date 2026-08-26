from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from jobscout.config import load_config
from jobscout.db import (
    create_db_engine,
    create_session_factory,
    init_db,
    save_match_result,
    upsert_job,
)
from jobscout.domain import CollectedJob, MatchResult, Provider
from jobscout.web.app import create_app


def seed(config_path: Path, description: str = "A safe plain-text job description.") -> int:
    loaded = load_config(config_path)
    engine = create_db_engine(loaded.database_path)
    init_db(engine)
    sessions = create_session_factory(engine)
    with sessions() as session:
        job, _ = upsert_job(
            session,
            CollectedJob(
                source_id="greenhouse-one",
                provider=Provider.GREENHOUSE,
                external_id="1",
                company="Example",
                title="Python Engineer",
                description=description,
                job_url="https://example.com/jobs/1",
                countries=["DE"],
                language="en",
            ),
        )
        save_match_result(
            session,
            job.id,
            loaded.settings.profiles[0],
            MatchResult(score=87, meets_threshold=True, reasons=["skills matched: python"]),
        )
        session.commit()
        return job.id


def test_dashboard_detail_and_htmx_status_update(config_path: Path) -> None:
    job_id = seed(config_path)
    with TestClient(create_app(config_path)) as client:
        index = client.get("/")
        detail = client.get(f"/jobs/{job_id}?profile=friend-a")
        status = client.post(
            f"/jobs/{job_id}/status",
            data={"profile": "friend-a", "status": "interesting"},
            headers={"HX-Request": "true"},
        )
        filtered = client.get("/?profile=friend-a&status=interesting")

    assert index.status_code == 200
    assert "Python Engineer" in index.text
    assert detail.status_code == 200
    assert "safe plain-text" in detail.text
    assert status.status_code == 200
    assert 'value="interesting" selected' in status.text
    assert "Python Engineer" in filtered.text


def test_invalid_status_returns_400(config_path: Path) -> None:
    job_id = seed(config_path)
    with TestClient(create_app(config_path)) as client:
        response = client.post(
            f"/jobs/{job_id}/status",
            data={"profile": "friend-a", "status": "invalid"},
        )
    assert response.status_code == 400


def test_detail_safely_formats_external_html_description(config_path: Path) -> None:
    job_id = seed(
        config_path,
        (
            '<p class="external"><strong>Mission Brief</strong></p>'
            '<ul><li>Design mechanical systems</li></ul>'
            '<a href="javascript:alert(1)">Company page</a>'
            '<iframe src="https://example.com/video"></iframe>'
            '<script>alert("unsafe")</script>'
        ),
    )

    with TestClient(create_app(config_path)) as client:
        detail = client.get(f"/jobs/{job_id}?profile=friend-a")

    assert detail.status_code == 200
    assert "<p><strong>Mission Brief</strong></p>" in detail.text
    assert "<li>Design mechanical systems</li>" in detail.text
    assert "Company page" in detail.text
    assert 'class="external"' not in detail.text
    assert "javascript:" not in detail.text
    assert "<iframe" not in detail.text
    assert "example.com/video" not in detail.text
    assert 'alert("unsafe")' not in detail.text

"""Scan orchestration across configured ATS sources."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from sqlalchemy.engine import Engine

from jobscout.collectors import build_collectors
from jobscout.collectors.base import Collector
from jobscout.config import LoadedConfig
from jobscout.db import (
    ScanRun,
    ScanSourceResult,
    create_session_factory,
    mark_missing_jobs_inactive,
    save_match_result,
    upsert_job,
    utc_now,
)
from jobscout.domain import Provider, ScanSummary
from jobscout.matching import Matcher


class Scanner:
    def __init__(
        self,
        config: LoadedConfig,
        engine: Engine,
        *,
        client: httpx.Client | None = None,
        collectors: Mapping[Provider, Collector] | None = None,
    ) -> None:
        self.config = config
        self.session_factory = create_session_factory(engine)
        self.client = client
        self.collectors = collectors or build_collectors(config.settings.app.retry_attempts)
        self.matcher = Matcher()

    def run(self, *, profile_id: str | None = None, source_id: str | None = None) -> ScanSummary:
        sources = [source for source in self.config.settings.sources if source.enabled]
        profiles = self.config.settings.profiles
        if source_id:
            sources = [source for source in sources if source.id == source_id]
            if not sources:
                raise ValueError(f"Enabled source not found: {source_id}")
        if profile_id:
            profiles = [profile for profile in profiles if profile.id == profile_id]
            if not profiles:
                raise ValueError(f"Profile not found: {profile_id}")

        with self.session_factory() as session:
            run = ScanRun(sources_total=len(sources))
            session.add(run)
            session.commit()
            run_id = run.id

        owns_client = self.client is None
        client = self.client or httpx.Client(
            timeout=self.config.settings.app.request_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0.0.0 Safari/537.36 JobScout/0.1"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            },
        )
        succeeded = failed = jobs_seen = jobs_saved = 0
        errors: list[str] = []
        try:
            for source in sources:
                try:
                    collector = self.collectors[source.provider]
                    collected = collector.collect(source, client)
                    with self.session_factory() as session:
                        seen_ids: set[str] = set()
                        for job in collected:
                            row, _created = upsert_job(session, job)
                            seen_ids.add(job.external_id)
                            for profile in profiles:
                                result = self.matcher.evaluate(job, profile)
                                save_match_result(session, row.id, profile, result)
                        mark_missing_jobs_inactive(session, source.id, seen_ids)
                        session.add(
                            ScanSourceResult(
                                scan_run_id=run_id,
                                source_id=source.id,
                                status="success",
                                jobs_fetched=len(collected),
                            )
                        )
                        session.commit()
                    succeeded += 1
                    jobs_seen += len(collected)
                    jobs_saved += len(collected)
                except Exception as exc:  # noqa: BLE001 - isolate failures to one configured source
                    failed += 1
                    error = f"{source.id}: {type(exc).__name__}: {exc}"
                    errors.append(error)
                    with self.session_factory() as session:
                        session.add(
                            ScanSourceResult(
                                scan_run_id=run_id,
                                source_id=source.id,
                                status="failed",
                                error=error[:4000],
                            )
                        )
                        session.commit()
        finally:
            if owns_client:
                client.close()

        status = "success" if failed == 0 else ("failed" if succeeded == 0 else "partial")
        with self.session_factory() as session:
            run = session.get(ScanRun, run_id)
            assert run is not None
            run.finished_at = utc_now()
            run.status = status
            run.sources_succeeded = succeeded
            run.sources_failed = failed
            run.jobs_seen = jobs_seen
            run.jobs_saved = jobs_saved
            session.commit()

        return ScanSummary(
            run_id=run_id,
            status=status,
            sources_total=len(sources),
            sources_succeeded=succeeded,
            sources_failed=failed,
            jobs_seen=jobs_seen,
            jobs_saved=jobs_saved,
            errors=errors,
        )

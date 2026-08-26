"""SQLAlchemy persistence for the local SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    URL,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from jobscout.config import ProfileConfig
from jobscout.domain import CollectedJob, JobStatus, MatchResult
from jobscout.normalization import canonicalize_url, job_fingerprint


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_job_source_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(120), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(32), index=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    job_url: Mapped[str] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    countries: Mapped[list[str]] = mapped_column(JSON, default=list)
    workplace_type: Mapped[str] = mapped_column(String(32), default="unknown")
    language: Mapped[str] = mapped_column(String(16), default="unknown")
    seniority: Mapped[str] = mapped_column(String(32), default="unknown")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    sources_total: Mapped[int] = mapped_column(Integer, default=0)
    sources_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_seen: Mapped[int] = mapped_column(Integer, default=0)
    jobs_saved: Mapped[int] = mapped_column(Integer, default=0)


class ScanSourceResult(Base):
    __tablename__ = "scan_source_results"
    __table_args__ = (UniqueConstraint("scan_run_id", "source_id", name="uq_scan_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32))
    jobs_fetched: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MatchResultRow(Base):
    __tablename__ = "match_results"
    __table_args__ = (UniqueConstraint("job_id", "profile_id", name="uq_match_job_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str] = mapped_column(String(120), index=True)
    profile_hash: Mapped[str] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    meets_threshold: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class JobState(Base):
    __tablename__ = "job_states"
    __table_args__ = (UniqueConstraint("job_id", "profile_id", name="uq_state_job_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.NEW.value)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


def create_db_engine(database_path: Path) -> Engine:
    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(database_path)), future=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def upsert_job(session: Session, job: CollectedJob, *, seen_at: datetime | None = None) -> tuple[JobPosting, bool]:
    seen_at = seen_at or utc_now()
    row = session.scalar(
        select(JobPosting).where(
            JobPosting.source_id == job.source_id,
            JobPosting.external_id == job.external_id,
        )
    )
    created = row is None
    if row is None:
        row = JobPosting(source_id=job.source_id, external_id=job.external_id, first_seen=seen_at)
        session.add(row)

    row.provider = job.provider.value
    row.company = job.company
    row.title = job.title
    row.description = job.description
    row.job_url = canonicalize_url(job.job_url)
    row.apply_url = canonicalize_url(job.apply_url) if job.apply_url else None
    row.locations = job.locations
    row.countries = job.countries
    row.workplace_type = job.workplace_type.value
    row.language = job.language
    row.seniority = job.seniority
    row.skills = job.skills
    row.published_at = job.published_at
    row.raw_payload = job.raw_payload
    row.fingerprint = job_fingerprint(job.company, job.title, job.locations)
    row.last_seen = seen_at
    row.is_active = True
    session.flush()
    return row, created


def mark_missing_jobs_inactive(session: Session, source_id: str, seen_external_ids: set[str]) -> None:
    statement = update(JobPosting).where(JobPosting.source_id == source_id)
    if seen_external_ids:
        statement = statement.where(JobPosting.external_id.not_in(seen_external_ids))
    session.execute(statement.values(is_active=False))


def save_match_result(
    session: Session,
    job_id: int,
    profile: ProfileConfig,
    result: MatchResult,
) -> MatchResultRow:
    row = session.scalar(
        select(MatchResultRow).where(
            MatchResultRow.job_id == job_id,
            MatchResultRow.profile_id == profile.id,
        )
    )
    if row is None:
        row = MatchResultRow(job_id=job_id, profile_id=profile.id)
        session.add(row)
    row.profile_hash = profile.fingerprint()
    row.score = result.score
    row.excluded = result.excluded
    row.meets_threshold = result.meets_threshold
    row.exclusion_reasons = result.exclusion_reasons
    row.reasons = result.reasons
    row.evaluated_at = utc_now()
    return row


def set_job_status(session: Session, job_id: int, profile_id: str, status: JobStatus) -> JobState:
    if session.get(JobPosting, job_id) is None:
        raise LookupError(f"Job {job_id} does not exist")
    row = session.scalar(
        select(JobState).where(JobState.job_id == job_id, JobState.profile_id == profile_id)
    )
    if row is None:
        row = JobState(job_id=job_id, profile_id=profile_id)
        session.add(row)
    row.status = status.value
    row.updated_at = utc_now()
    session.flush()
    return row


def list_jobs(
    session: Session,
    profile_id: str,
    *,
    status: str | None = None,
    source_id: str | None = None,
) -> list[tuple[JobPosting, MatchResultRow, JobState | None]]:
    statement = (
        select(JobPosting, MatchResultRow, JobState)
        .join(MatchResultRow, MatchResultRow.job_id == JobPosting.id)
        .outerjoin(
            JobState,
            (JobState.job_id == JobPosting.id) & (JobState.profile_id == profile_id),
        )
        .where(JobPosting.is_active.is_(True), MatchResultRow.profile_id == profile_id)
        .order_by(MatchResultRow.excluded.asc(), MatchResultRow.score.desc(), JobPosting.first_seen.desc())
    )
    if source_id:
        statement = statement.where(JobPosting.source_id == source_id)
    if status == JobStatus.NEW.value:
        statement = statement.where(or_(JobState.status.is_(None), JobState.status == JobStatus.NEW.value))
    elif status:
        statement = statement.where(JobState.status == status)
    return list(session.execute(statement).all())


def get_job_with_profile(
    session: Session, job_id: int, profile_id: str
) -> tuple[JobPosting, MatchResultRow, JobState | None] | None:
    statement = (
        select(JobPosting, MatchResultRow, JobState)
        .join(MatchResultRow, MatchResultRow.job_id == JobPosting.id)
        .outerjoin(
            JobState,
            (JobState.job_id == JobPosting.id) & (JobState.profile_id == profile_id),
        )
        .where(JobPosting.id == job_id, MatchResultRow.profile_id == profile_id)
    )
    return session.execute(statement).one_or_none()

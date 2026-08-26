"""FastAPI application factory for the local dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobscout.config import LoadedConfig, load_config
from jobscout.db import (
    create_db_engine,
    create_session_factory,
    get_job_with_profile,
    init_db,
    list_jobs,
    set_job_status,
)
from jobscout.domain import JobStatus
from jobscout.web.formatting import safe_description_html

WEB_DIR = Path(__file__).parent


def create_app(config: LoadedConfig | str | Path | None = None) -> FastAPI:
    loaded = config if isinstance(config, LoadedConfig) else load_config(config)
    engine = create_db_engine(loaded.database_path)
    init_db(engine)
    sessions = create_session_factory(engine)
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.filters["safe_description_html"] = safe_description_html

    app = FastAPI(title="JobScout", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.state.loaded_config = loaded
    app.state.sessions = sessions

    def profile_or_404(profile_id: str | None):
        selected = profile_id or loaded.settings.profiles[0].id
        profile = next((item for item in loaded.settings.profiles if item.id == selected), None)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        return profile

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        profile: str | None = Query(default=None),
        status: str | None = Query(default=None),
        source: str | None = Query(default=None),
    ) -> HTMLResponse:
        selected_profile = profile_or_404(profile)
        if status and status not in {item.value for item in JobStatus}:
            raise HTTPException(status_code=400, detail="Invalid status")
        with sessions() as session:
            rows = list_jobs(session, selected_profile.id, status=status, source_id=source)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "profiles": loaded.settings.profiles,
                "selected_profile": selected_profile,
                "sources": loaded.settings.sources,
                "selected_source": source or "",
                "selected_status": status or "",
                "statuses": list(JobStatus),
                "rows": rows,
            },
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def detail(request: Request, job_id: int, profile: str | None = Query(default=None)) -> HTMLResponse:
        selected_profile = profile_or_404(profile)
        with sessions() as session:
            row = get_job_with_profile(session, job_id, selected_profile.id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return templates.TemplateResponse(
            request=request,
            name="detail.html",
            context={
                "selected_profile": selected_profile,
                "row": row,
                "statuses": list(JobStatus),
            },
        )

    @app.post("/jobs/{job_id}/status", response_class=HTMLResponse)
    def update_status(
        request: Request,
        job_id: int,
        profile: str = Form(...),
        status: str = Form(...),
        hx_request: str | None = Header(default=None, alias="HX-Request"),
    ):
        selected_profile = profile_or_404(profile)
        try:
            selected_status = JobStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc
        with sessions() as session:
            try:
                state = set_job_status(session, job_id, selected_profile.id, selected_status)
                session.commit()
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        if hx_request and hx_request.lower() == "true":
            return templates.TemplateResponse(
                request=request,
                name="_status_form.html",
                context={
                    "job_id": job_id,
                    "selected_profile": selected_profile,
                    "current_status": state.status,
                    "statuses": list(JobStatus),
                },
            )
        return RedirectResponse(url=f"/jobs/{job_id}?profile={selected_profile.id}", status_code=303)

    return app

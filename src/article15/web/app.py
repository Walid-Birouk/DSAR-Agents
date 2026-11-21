"""The web app's API (FastAPI) and the built React app (PLAN.md 11.2).

No accounts: an anonymous session cookie (HttpOnly, SameSite=Lax) for that browser only. Every run belongs to
the session that started it; another session gets a 404. Live progress is a server-sent event stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from article15.web.service import DeskService, Visitor, WebConfig, WebError

COOKIE = "a15_session"
FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "dist"


class StartRun(BaseModel):
    case_id: str | None = None
    customer_id: str | None = None
    subject: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=4000)
    source: str = Field(default="preset", pattern="^(preset|visitor|break)$")


class Review(BaseModel):
    action: str
    note: str = Field(default="", max_length=1000)
    special_category_acknowledged: bool = False


def create_app(cfg: WebConfig, frontend: Path | None = FRONTEND) -> FastAPI:
    service = DeskService(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async def sweep() -> None:
            while True:
                service.cleanup()
                await asyncio.sleep(3600)

        task = asyncio.create_task(sweep())
        yield
        task.cancel()
        for t in service.tasks.values():
            t.cancel()

    app = FastAPI(
        title="Article 15 desk", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.state.service = service

    @app.exception_handler(WebError)
    async def web_error(_: Request, exc: WebError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def session(request: Request, response: Response) -> str:
        sid = request.cookies.get(COOKIE)
        if not sid or not service.store.has_session(sid):
            sid = secrets.token_urlsafe(18)
            service.store.add_session(sid)
            response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=24 * 3600)
        return sid

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/system")
    def system() -> dict[str, Any]:
        return service.system()

    @app.get("/api/customers")
    def customers() -> list[dict[str, Any]]:
        return service.customers()

    @app.get("/api/presets")
    def presets(kind: str = "try") -> list[dict[str, Any]]:
        return service.presets(break_it=kind == "break")

    @app.post("/api/runs")
    async def start(body: StartRun, sid: str = Depends(session)) -> dict[str, str]:
        visitor = None
        if body.customer_id:
            visitor = Visitor(body.customer_id, body.subject, body.text)
        elif not body.case_id:
            raise HTTPException(400, "give a prepared case_id, or a customer_id and your text")
        run_id = await service.start(sid, case_id=body.case_id, visitor=visitor, source=body.source)
        return {"run_id": run_id}

    @app.get("/api/runs")
    def runs(sid: str = Depends(session)) -> list[dict[str, Any]]:
        return service.store.runs_of(sid)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, sid: str = Depends(session)) -> dict[str, Any]:
        return service.detail(sid, run_id)

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str, sid: str = Depends(session)) -> list[dict[str, Any]]:
        return service.trace(sid, run_id)

    @app.get("/api/runs/{run_id}/files/{name}")
    def download(run_id: str, name: str, sid: str = Depends(session)) -> FileResponse:
        return FileResponse(service.file(sid, run_id, name), filename=f"{run_id}-{name}")

    @app.post("/api/runs/{run_id}/review")
    async def review(run_id: str, body: Review, sid: str = Depends(session)) -> dict[str, Any]:
        return await service.review(sid, run_id, body.action, body.note, body.special_category_acknowledged)

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, sid: str = Depends(session)) -> EventSourceResponse:
        service.owned(sid, run_id)

        async def stream() -> AsyncIterator[dict[str, str]]:
            offset = 0
            while True:
                rows, offset, done = service.events(sid, run_id, offset)
                for row in rows:
                    yield {"event": "step", "data": json.dumps(row, ensure_ascii=False)}
                if done:
                    yield {"event": "done", "data": json.dumps(service.store.run(run_id))}
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.3)

        return EventSourceResponse(stream())

    @app.get("/api/evals")
    def evals() -> list[dict[str, Any]]:
        return service.eval_reports()

    if frontend is not None and frontend.exists():
        assets = frontend / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = (frontend / path).resolve()
            with contextlib.suppress(ValueError):
                if path and target.is_file() and target.relative_to(frontend.resolve()):
                    return FileResponse(target)
            return FileResponse(frontend / "index.html")

    return app

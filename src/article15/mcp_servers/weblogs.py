"""Web logs server: page views, logged in or not."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from article15.company.store import read_only
from article15.mcp_servers.common import (
    CaseToken,
    RecordDetail,
    RecordSummary,
    SearchResult,
    ServerContext,
    build_server,
    detail,
    run_tool,
)
from article15.schemas import System
from article15.scope.authority import Scope, ScopeRefused

SYSTEM = System.WEBLOGS
MAX_PAGE = 500

INSTRUCTIONS = (
    "Ferlane web logs. Works only after the requester's identity is verified. Returns the person's logged-in "
    "page views and the anonymous ones from browsers they logged in with; events matched only by IP address "
    "are never linked, because an IP can be shared."
)


def _scope_clause(scope: Scope) -> tuple[str, list[str]]:
    subject = scope.require_subject()
    cookies = sorted(scope.cookies)
    marks = ", ".join("?" for _ in cookies)
    clause = f"(account_id = ? OR cookie_id IN ({marks}))" if cookies else "account_id = ?"
    return clause, [subject, *cookies]


class WeblogsService:
    def __init__(self, ctx: ServerContext) -> None:
        self.ctx = ctx

    def search_events(self, token: str, offset: int, limit: int) -> SearchResult:
        if offset < 0 or not 1 <= limit <= MAX_PAGE:
            raise ValueError(f"offset must be >= 0 and limit between 1 and {MAX_PAGE}")
        clause, args = _scope_clause(self.ctx.resolver.resolve(token))
        with read_only(self.ctx.paths.db(SYSTEM)) as con:
            total = con.execute(f"SELECT count(*) FROM events WHERE {clause}", args).fetchone()[0]
            rows = con.execute(
                f"SELECT event_id, ts, path, account_id FROM events WHERE {clause} ORDER BY ts, event_id "
                "LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        records = [
            RecordSummary(
                system=SYSTEM,
                table="events",
                record_id=r["event_id"],
                summary=f"{r['ts'][:16]} {r['path']} ({'logged in' if r['account_id'] else 'not logged in'})",
            )
            for r in rows
        ]
        return SearchResult(records=records, total=total, truncated=offset + len(records) < total)

    def read_record(self, token: str, record_id: str) -> RecordDetail:
        clause, args = _scope_clause(self.ctx.resolver.resolve(token))
        with read_only(self.ctx.paths.db(SYSTEM)) as con:
            row = con.execute(
                f"SELECT * FROM events WHERE event_id = ? AND {clause}", [record_id, *args]
            ).fetchone()
        if row is None:
            raise ScopeRefused(f"web log event {record_id} is not part of this case's scope")
        return detail(SYSTEM, "events", record_id, row)


def build(ctx: ServerContext) -> MCPServer:
    svc = WeblogsService(ctx)

    async def weblogs_search_events(
        case_token: CaseToken,
        offset: Annotated[int, Field(ge=0, description="Skip this many events (paging).")] = 0,
        limit: Annotated[int, Field(ge=1, le=MAX_PAGE, description="Events per page.")] = 200,
    ) -> SearchResult:
        """List the verified customer's web log events, oldest first, as references with short summaries."""
        return await run_tool(ctx, SYSTEM, lambda: svc.search_events(case_token, offset, limit))

    async def weblogs_read_record(case_token: CaseToken, record_id: str) -> RecordDetail:
        """Read every field of one web log event of the verified customer (time, page, IP, browser)."""
        return await run_tool(ctx, SYSTEM, lambda: svc.read_record(case_token, record_id))

    return build_server(
        "ferlane-weblogs", INSTRUCTIONS, [weblogs_search_events, weblogs_read_record], read_only=True
    )

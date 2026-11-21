"""Build any Ferlane MCP server by name."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server import MCPServer

from article15.mcp_servers import crm, helpdesk, notifier, orders, weblogs
from article15.mcp_servers.common import ServerContext
from article15.notifier.store import NotifierStore
from article15.schemas import System
from article15.settings import Settings

SYSTEM_SERVERS: dict[str, Callable[[ServerContext], MCPServer]] = {
    System.CRM.value: crm.build,
    System.HELPDESK.value: helpdesk.build,
    System.ORDERS.value: orders.build,
    System.WEBLOGS.value: weblogs.build,
}
SERVER_NAMES = (*SYSTEM_SERVERS, "notifier")


def build_named(name: str, ctx: ServerContext, notifier_store: NotifierStore | None = None) -> MCPServer:
    if name == "notifier":
        if notifier_store is None:
            raise ValueError("the notifier server needs its message store")
        return notifier.build(ctx, notifier_store)
    return SYSTEM_SERVERS[name](ctx)


def build_from_settings(name: str, settings: Settings) -> MCPServer:
    store = NotifierStore(settings.notifier_db) if name == "notifier" else None
    return build_named(name, ServerContext.from_settings(settings), store)

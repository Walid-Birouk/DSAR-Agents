"""MCP servers over Ferlane's systems: CRM, helpdesk, orders, web logs, and the notifier.

Each server is a thin MCP layer over a service class that holds the logic, so the logic is testable on its own
and the protocol layer stays small. Run one with `a15 serve <system>` (stdio).
"""

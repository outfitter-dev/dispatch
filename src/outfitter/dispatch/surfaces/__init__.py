"""Derived surfaces: thin projections of the op registry (CLI now, MCP next).

Surfaces contain projection wiring only — no operation logic. The CLI is a sync
client of the daemon control socket; it never drives the app-server itself.
"""

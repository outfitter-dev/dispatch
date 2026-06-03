"""dispatch daemon host (``dispatchd``).

Owns the single ``codex app-server`` subprocess, hosts the core (registry,
scheduler, reactor, handlers), and exposes the Unix-socket control API. Phase 0
ships only the entrypoint stub; supervision lands in Phase 5.
"""

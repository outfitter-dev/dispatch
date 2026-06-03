"""MCP tool derivation from the op registry."""

from __future__ import annotations

from outfitter.dispatch.contracts.derive_mcp import derive_mcp
from outfitter.dispatch.core.ops import REGISTRY


def test_tools_match_registry_ops() -> None:
    assert [t.name for t in derive_mcp(REGISTRY)] == REGISTRY.ids()


def test_schema_and_annotations_from_op() -> None:
    tools = {t.name: t for t in derive_mcp(REGISTRY)}

    open_tool = tools["open"]
    assert set(open_tool.inputSchema["properties"]) == {"name", "cwd"}
    assert open_tool.outputSchema is not None
    assert open_tool.annotations is not None
    assert open_tool.annotations.readOnlyHint is False  # write

    roster = tools["roster"]
    assert roster.annotations is not None
    assert roster.annotations.readOnlyHint is True
    assert roster.annotations.idempotentHint is True

    archive = tools["archive"]
    assert archive.annotations is not None
    assert archive.annotations.destructiveHint is True

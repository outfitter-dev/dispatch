"""Derive MCP tools from the op registry (ADR-0000 projection).

Tool name from op id, ``inputSchema``/``outputSchema`` from the Pydantic models,
annotations from ``intent``/``idempotent`` — the same single source the CLI
derives from, so the two surfaces cannot drift (enforced by the parity test).
"""

from __future__ import annotations

from mcp.types import Tool, ToolAnnotations

from .registry import OpRegistry


def derive_mcp(registry: OpRegistry) -> list[Tool]:
    tools: list[Tool] = []
    for op in registry:
        tools.append(
            Tool(
                name=op.id,
                description=op.summary,
                inputSchema=op.input.model_json_schema(),
                outputSchema=op.output.model_json_schema(),
                annotations=ToolAnnotations(
                    readOnlyHint=op.intent == "read",
                    destructiveHint=op.intent == "destroy",
                    idempotentHint=op.idempotent,
                ),
            )
        )
    return tools

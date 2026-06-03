"""Derive a Typer CLI from the op registry (ADR-0000 projection).

One command per op; input model fields project to CLI options; ``intent:
destroy`` adds a confirm prompt. The command marshals options into params and
calls the injected ``invoke`` (which talks to the daemon control socket) — no
per-op CLI code is ever hand-written.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import typer
from rich import print_json

from .op import Op
from .registry import OpRegistry

Invoker = Callable[[str, dict[str, object]], dict[str, object]]
Renderer = Callable[[Op, dict[str, object]], None]


def derive_cli(
    registry: OpRegistry, invoke: Invoker, render: Renderer | None = None
) -> typer.Typer:
    app = typer.Typer(
        name="dispatch",
        help="Local control plane for orchestrating Codex agent lanes.",
        no_args_is_help=True,
        add_completion=False,
    )
    renderer = render if render is not None else _default_render
    for op in registry:
        app.command(name=op.id, help=op.summary)(_make_command(op, invoke, renderer))
    return app


def _make_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    parameters: list[inspect.Parameter] = []
    for name, field in op.input.model_fields.items():
        help_text = field.description or ""
        if field.is_required():
            option = typer.Option(..., help=help_text)
        else:
            option = typer.Option(field.default, help=help_text)
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=option,
                annotation=field.annotation,
            )
        )

    def command(**kwargs: object) -> None:
        if op.intent == "destroy":
            typer.confirm(f"Run destroy op {op.id!r}?", abort=True)
        result = invoke(op.id, dict(kwargs))
        render(op, result)

    # Typer reads the call signature to build options; set it from the op's model.
    command.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    command.__name__ = op.id.replace("-", "_")
    command.__doc__ = op.summary
    return command


def _default_render(op: Op, result: dict[str, object]) -> None:
    print_json(data=result)

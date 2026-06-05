"""Derive an ergonomic Typer CLI from the op registry (ADR-0010).

The registry owns semantic ops. The CLI projection owns shell ergonomics: command
grouping, positional arguments, and stable shell affordances such as ``stop``.
Schemas, handlers, safety intent, and output rendering still derive from ops.
"""

from __future__ import annotations

import inspect
import json
import os
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import typer

from .op import Op
from .registry import OpRegistry
from .schema import is_internal_field, public_schema

Invoker = Callable[[str, dict[str, object]], dict[str, object]]
Renderer = Callable[[Op, dict[str, object]], None]

_SendMode = Literal["send", "steer", "queue", "interject", "context"]
_SearchSortKey = Literal["created_at", "updated_at"]


@dataclass(frozen=True)
class CliRoute:
    path: tuple[str, ...]
    op_id: str
    positionals: tuple[str, ...] = ()


_ROUTES: tuple[CliRoute, ...] = (
    CliRoute(("new",), "new"),
    CliRoute(("attach",), "attach", ("thread",)),
    CliRoute(("get",), "show", ("lane",)),
    CliRoute(("tail",), "transcript", ("lane",)),
    CliRoute(("watch",), "watch", ("lane",)),
    CliRoute(("sync",), "sync", ("lane",)),
    CliRoute(("rename",), "lane-rename", ("old", "new")),
    CliRoute(("archive",), "archive", ("target",)),
    CliRoute(("restore",), "restore", ("target",)),
    CliRoute(("goal", "status"), "goal-get", ("lane",)),
    CliRoute(("goal", "clear"), "goal-clear", ("lane",)),
    CliRoute(("trigger", "add"), "trigger-add"),
    CliRoute(("trigger", "rm"), "trigger-rm", ("id",)),
    CliRoute(("trigger", "pause"), "trigger-pause", ("id",)),
    CliRoute(("trigger", "resume"), "trigger-resume", ("id",)),
    CliRoute(("daemon", "status"), "status"),
    CliRoute(("daemon", "log"), "log"),
)


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
    groups: dict[str, typer.Typer] = {}

    _register_command(app, ("send",), _send_command(registry.get("send"), invoke, renderer))
    _register_command(app, ("stop",), _stop_command(registry.get("stop"), invoke, renderer))
    _register_command(app, ("search",), _search_command(registry.get("search"), invoke, renderer))
    _register_command(app, ("list",), _list_command(registry, invoke, renderer))
    _register_command(
        app,
        ("trigger", "list"),
        _op_command(registry.get("trigger-list"), invoke, renderer),
        groups,
    )
    _register_command(
        app,
        ("goal", "set"),
        _goal_set_command(registry.get("goal-set"), invoke, renderer),
        groups,
    )
    _register_command(app, ("schema",), _schema_command(registry))

    for route in _ROUTES:
        _register_command(
            app,
            route.path,
            _op_command(registry.get(route.op_id), invoke, renderer, positionals=route.positionals),
            groups,
        )
    return app


def _register_command(
    app: typer.Typer,
    path: tuple[str, ...],
    callback: Callable[..., None],
    groups: dict[str, typer.Typer] | None = None,
) -> None:
    if len(path) == 1:
        app.command(name=path[0], help=callback.__doc__)(callback)
        return
    group_name, command_name = path
    if groups is None:
        groups = {}
    group = groups.get(group_name)
    if group is None:
        group = typer.Typer(no_args_is_help=True, add_completion=False)
        groups[group_name] = group
        app.add_typer(group, name=group_name)
    group.command(name=command_name, help=callback.__doc__)(callback)


def _op_command(
    op: Op,
    invoke: Invoker,
    render: Renderer,
    *,
    positionals: tuple[str, ...] = (),
) -> Callable[..., None]:
    parameters = _parameters(op, positionals=positionals)

    def command(**kwargs: object) -> None:
        json_requested = bool(kwargs.pop("json", False))
        if op.intent == "destroy":
            typer.confirm(f"Run destroy op {op.id!r}?", abort=True, err=json_requested)
        result = invoke(op.id, dict(kwargs))
        render(op, result)
        _ignore_json(json_requested)

    command.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    command.__name__ = "_".join((*op.id.split("-"), *positionals)) or "command"
    command.__doc__ = op.summary
    return command


def _parameters(op: Op, *, positionals: tuple[str, ...] = ()) -> list[inspect.Parameter]:
    parameters: list[inspect.Parameter] = []
    positional_set = set(positionals)
    for name, field in op.input.model_fields.items():
        if is_internal_field(field):
            continue
        help_text = field.description or ""
        kind: inspect._ParameterKind
        if name in positional_set:
            default = typer.Argument(..., help=help_text)
            kind = inspect.Parameter.POSITIONAL_OR_KEYWORD
        else:
            if field.is_required():
                default = typer.Option(..., help=help_text)
            elif field.default_factory is not None:
                default = typer.Option(field.get_default(call_default_factory=True), help=help_text)
            else:
                default = typer.Option(field.default, help=help_text)
            kind = inspect.Parameter.KEYWORD_ONLY
        parameters.append(
            inspect.Parameter(name, kind, default=default, annotation=field.annotation)
        )
    parameters.append(
        inspect.Parameter(
            "json",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(False, "--json", help="Render machine-readable JSON output."),
            annotation=bool,
        )
    )
    return parameters


def _send_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        lane: Annotated[str, typer.Argument(help="Thread selector.")],
        text: Annotated[str, typer.Argument(help="Message text.")],
        mode: Annotated[_SendMode, typer.Option("--mode", help="Delivery mode.")] = "send",
        steer: Annotated[bool, typer.Option("--steer", help="Steer an active turn.")] = False,
        queue: Annotated[bool, typer.Option("--queue", help="Queue after current work.")] = False,
        interject: Annotated[
            bool, typer.Option("--interject", help="Cancel active turn then send.")
        ] = False,
        context: Annotated[
            bool, typer.Option("--context", help="Inject context without waking.")
        ] = False,
        intro: Annotated[
            bool,
            typer.Option("--intro", help="Prepend dispatch sender intro from CODEX_THREAD_ID."),
        ] = False,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        chosen = [
            flag for flag, enabled in _flag_modes(steer, queue, interject, context) if enabled
        ]
        if len(chosen) > 1 or (chosen and mode != "send"):
            typer.secho("dispatch: choose exactly one send mode", fg="red", err=True)
            raise typer.Exit(code=2)
        params = {"lane": lane, "text": text, "mode": chosen[0] if chosen else mode, "intro": intro}
        if intro:
            params["caller_thread_id"] = os.environ.get("CODEX_THREAD_ID")
        result = invoke(
            op.id,
            params,
        )
        render(op, result)
        _ignore_json(json)

    command.__doc__ = op.summary
    return command


def _flag_modes(
    steer: bool, queue: bool, interject: bool, context: bool
) -> tuple[tuple[_SendMode, bool], ...]:
    return (("steer", steer), ("queue", queue), ("interject", interject), ("context", context))


def _stop_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        lane_arg: Annotated[str | None, typer.Argument(help="Thread selector.")] = None,
        lane: Annotated[str | None, typer.Option("--lane", help="Thread selector.")] = None,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        selected = lane_arg or lane
        if selected is None or (lane_arg is not None and lane is not None):
            typer.secho(
                "dispatch: provide one thread selector as an argument or with --lane",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=2)
        result = invoke(op.id, {"lane": selected})
        render(op, result)
        _ignore_json(json)

    command.__doc__ = "Stop a lane's active turn."
    return command


def _search_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        query: Annotated[str, typer.Argument(help="Substring/full-text query.")],
        lane: Annotated[
            str | None,
            typer.Option("--thread", "--lane", help="Limit to one thread selector."),
        ] = None,
        directory: Annotated[
            str | None,
            typer.Option("--directory", "--dir", help="Only include threads under this directory."),
        ] = None,
        repo: Annotated[
            str | None, typer.Option("--repo", help="Only include threads under this repo root.")
        ] = None,
        managed: Annotated[bool, typer.Option("--managed", help="Only managed threads.")] = False,
        unmanaged: Annotated[
            bool, typer.Option("--unmanaged", help="Only unmanaged Codex threads.")
        ] = False,
        archived: Annotated[
            bool, typer.Option("--archived", help="Search archived threads.")
        ] = False,
        since: Annotated[
            str | None, typer.Option("--since", help="Inclusive ISO date/time lower bound.")
        ] = None,
        until: Annotated[
            str | None, typer.Option("--until", help="Inclusive ISO date/time upper bound.")
        ] = None,
        date_field: Annotated[
            _SearchSortKey, typer.Option("--date-field", help="Timestamp field for date filters.")
        ] = "updated_at",
        sort: Annotated[_SearchSortKey, typer.Option("--sort", help="App Server sort key.")] = (
            "updated_at"
        ),
        ascending: Annotated[bool, typer.Option("--ascending", help="Sort oldest first.")] = False,
        limit: Annotated[int, typer.Option(help="Max matches to return.")] = 20,
        max_scan: Annotated[
            int, typer.Option("--max-scan", help="Max App Server matches to scan.")
        ] = 200,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        _invoke_search(
            op,
            invoke,
            render,
            query=query,
            lane=lane,
            directory=directory,
            repo=repo,
            managed=managed,
            unmanaged=unmanaged,
            archived=archived,
            since=since,
            until=until,
            date_field=date_field,
            sort=sort,
            ascending=ascending,
            limit=limit,
            max_scan=max_scan,
            json=json,
        )

    command.__doc__ = op.summary
    return command


def _invoke_search(
    op: Op,
    invoke: Invoker,
    render: Renderer,
    *,
    query: str,
    lane: str | None,
    directory: str | None,
    repo: str | None,
    managed: bool,
    unmanaged: bool,
    archived: bool,
    since: str | None,
    until: str | None,
    date_field: str,
    sort: str,
    ascending: bool,
    limit: int,
    max_scan: int,
    json: bool,
) -> None:
    result = invoke(
        op.id,
        {
            "query": query,
            "lane": lane,
            "directory": directory,
            "repo": repo,
            "managed": managed,
            "unmanaged": unmanaged,
            "archived": archived,
            "since": since,
            "until": until,
            "date_field": date_field,
            "sort": sort,
            "ascending": ascending,
            "limit": limit,
            "max_scan": max_scan,
        },
    )
    render(op, result)
    _ignore_json(json)


def _list_command(registry: OpRegistry, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    roster = registry.get("roster")
    discover = registry.get("discover")

    def command(
        unmanaged: Annotated[
            bool, typer.Option("--unmanaged", help="List attachable unmanaged sessions.")
        ] = False,
        include_archived: Annotated[
            bool, typer.Option(help="Include archived managed threads.")
        ] = False,
        limit: Annotated[int, typer.Option(help="Max unmanaged sessions to list.")] = 50,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        op = discover if unmanaged else roster
        params: dict[str, object] = (
            {"limit": limit} if unmanaged else {"include_archived": include_archived}
        )
        render(op, invoke(op.id, params))
        _ignore_json(json)

    command.__doc__ = "List managed threads, or unmanaged discoverable sessions."
    return command


def _goal_set_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        lane: Annotated[str, typer.Argument(help="Thread selector.")],
        objective: Annotated[str | None, typer.Argument(help="Goal objective text.")] = None,
        status: Annotated[str | None, typer.Option("--status", help="Goal status.")] = None,
        token_budget: Annotated[
            int | None, typer.Option("--token-budget", help="Optional token budget.")
        ] = None,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        result = invoke(
            op.id,
            {
                "lane": lane,
                "objective": objective,
                "status": status,
                "token_budget": token_budget,
            },
        )
        render(op, result)
        _ignore_json(json)

    command.__doc__ = op.summary
    return command


def _schema_command(registry: OpRegistry) -> Callable[..., None]:
    def command(
        command: Annotated[str, typer.Argument(help="Projected command or op id.")],
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = True,
    ) -> None:
        op_id = _schema_op_id(command)
        try:
            op = registry.get(op_id)
        except KeyError:
            typer.secho(
                f"dispatch: unknown command or op for schema: {command}", fg="red", err=True
            )
            raise typer.Exit(code=2) from None
        _print_json(
            data={
                "command": command,
                "op": op.id,
                "input": public_schema(op.input.model_json_schema()),
                "output": op.output.model_json_schema(),
            }
        )
        _ignore_json(json)

    command.__doc__ = "Print the derived JSON schemas for a command."
    return command


def _schema_op_id(command: str) -> str:
    aliases = {
        "stop": "stop",
        "list": "roster",
        "list-unmanaged": "discover",
        "attach": "attach",
        "get": "show",
        "tail": "transcript",
        "watch": "watch",
        "sync": "sync",
        "search": "search",
        "rename": "lane-rename",
        "archive": "archive",
        "restore": "restore",
        "goal-status": "goal-get",
        "goal-set": "goal-set",
        "goal-clear": "goal-clear",
        "daemon-status": "status",
        "daemon-log": "log",
    }
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        parts = command.strip().split()
    flags = {part for part in parts if part.startswith("--")}
    words = [part for part in parts if not part.startswith("--")]
    normalized = "-".join(words) if words else command.strip().replace(" ", "-")
    if normalized == "tail" and "--follow" in flags:
        return "__unknown_tail_follow__"
    if normalized == "list" and "--unmanaged" in flags:
        return "discover"
    return aliases.get(normalized, normalized)


def _default_render(op: Op, result: dict[str, object]) -> None:
    _print_json(data=result)


def _ignore_json(_requested: bool) -> None:
    """Successful CLI output is already JSON; keep --json as an explicit stable affordance."""


def _print_json(*, data: dict[str, object]) -> None:
    typer.echo(json.dumps(data, indent=2))

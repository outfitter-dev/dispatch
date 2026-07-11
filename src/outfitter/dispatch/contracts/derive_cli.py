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
import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from .op import Op
from .registry import OpRegistry
from .schema import is_internal_field, public_schema

Invoker = Callable[[str, dict[str, object]], dict[str, object]]
Renderer = Callable[[Op, dict[str, object]], None]

_SendMode = Literal["send", "steer", "queue", "interject", "context"]
_ImageDetail = Literal["auto", "low", "high", "original"]
_SearchSortKey = Literal["created_at", "updated_at"]
_QueryDateField = Literal["created_at", "updated_at"]
_HistoryView = Literal["auto", "overview", "summary", "items", "tools", "files"]


@dataclass(frozen=True)
class CliRoute:
    path: tuple[str, ...]
    op_id: str
    positionals: tuple[str, ...] = ()


_CUSTOM_ROUTES: tuple[CliRoute, ...] = (
    CliRoute(("new",), "new"),
    CliRoute(("send",), "send", ("lane", "text")),
    CliRoute(("stop",), "stop", ("lane",)),
    CliRoute(("search",), "search", ("query",)),
    CliRoute(("query",), "query"),
    CliRoute(("history",), "history"),
    CliRoute(("list",), "roster"),
    CliRoute(("subscribe",), "subscribe", ("target", "spec")),
    CliRoute(("inbox", "list"), "inbox-list"),
    CliRoute(("inbox", "ack"), "inbox-ack", ("id",)),
    CliRoute(("request", "respond"), "server-request-respond", ("id", "response")),
    CliRoute(("trigger", "list"), "trigger-list"),
    CliRoute(("goal", "set"), "goal-set", ("lane", "objective")),
)

_SIMPLE_ROUTES: tuple[CliRoute, ...] = (
    CliRoute(("attach",), "attach", ("thread",)),
    CliRoute(("get",), "show", ("lane",)),
    CliRoute(("tail",), "transcript", ("lane",)),
    CliRoute(("watch",), "watch", ("lane",)),
    CliRoute(("sync",), "sync", ("lane",)),
    CliRoute(("rename",), "lane-rename", ("old", "new")),
    CliRoute(("archive",), "archive", ("target",)),
    CliRoute(("restore",), "restore", ("target",)),
    CliRoute(("models",), "models"),
    CliRoute(("permissions",), "permissions"),
    CliRoute(("usage",), "usage"),
    CliRoute(("inbox", "read"), "inbox-read", ("id",)),
    CliRoute(("request", "list"), "server-request-list"),
    CliRoute(("subscriptions",), "subscription-list"),
    CliRoute(("unsubscribe",), "unsubscribe", ("id",)),
    CliRoute(("goal", "status"), "goal-get", ("lane",)),
    CliRoute(("goal", "clear"), "goal-clear", ("lane",)),
    CliRoute(("trigger", "add"), "trigger-add"),
    CliRoute(("trigger", "rm"), "trigger-rm", ("id",)),
    CliRoute(("trigger", "pause"), "trigger-pause", ("id",)),
    CliRoute(("trigger", "resume"), "trigger-resume", ("id",)),
    CliRoute(("daemon", "status"), "status"),
    CliRoute(("daemon", "log"), "log"),
)

CLI_PROJECTION_CONTROL_PATHS: tuple[tuple[str, ...], ...] = (("schema",),)
CLI_ADAPTED_INPUT_FIELDS: dict[str, dict[str, frozenset[str]]] = {
    "new": {"content": frozenset({"image", "image_url", "image_detail"})},
    "send": {"content": frozenset({"image", "image_url", "image_detail"})},
}
_COMPOSED_SCHEMA_ROUTES: dict[str, str] = {
    "list --unmanaged": "discover",
    "new --dry-run": "new-plan",
}


def cli_public_routes() -> tuple[CliRoute, ...]:
    """All op-backed CLI routes. Custom route functions are still manifest entries;
    the custom code is an explicit projection override, not an untracked surface."""
    return (*_CUSTOM_ROUTES, *_SIMPLE_ROUTES)


def cli_schema_routes() -> dict[str, str]:
    """Public command spellings accepted by ``dispatch schema``."""
    routes = {" ".join(route.path): route.op_id for route in cli_public_routes()}
    routes.update(_COMPOSED_SCHEMA_ROUTES)
    return routes


def derive_cli(
    registry: OpRegistry, invoke: Invoker, render: Renderer | None = None
) -> typer.Typer:
    app = typer.Typer(
        name="dispatch",
        help="Local control plane for orchestrating Codex agent lanes.",
        no_args_is_help=True,
        add_completion=True,
    )
    renderer = render if render is not None else _default_render
    groups: dict[str, typer.Typer] = {}

    _register_command(
        app,
        ("new",),
        _new_command(registry, invoke, renderer),
        context_settings={"allow_extra_args": True},
    )
    _register_command(app, ("send",), _send_command(registry.get("send"), invoke, renderer))
    _register_command(app, ("stop",), _stop_command(registry.get("stop"), invoke, renderer))
    _register_command(app, ("search",), _search_command(registry.get("search"), invoke, renderer))
    _register_command(app, ("query",), _query_command(registry.get("query"), invoke, renderer))
    _register_command(
        app, ("history",), _history_command(registry.get("history"), invoke, renderer)
    )
    _register_command(app, ("list",), _list_command(registry, invoke, renderer))
    _register_command(
        app,
        ("subscribe",),
        _subscribe_command(registry.get("subscribe"), invoke, renderer),
    )
    _register_command(
        app,
        ("inbox", "list"),
        _inbox_list_command(registry.get("inbox-list"), invoke, renderer),
        groups,
    )
    _register_command(
        app,
        ("inbox", "ack"),
        _inbox_ack_command(registry.get("inbox-ack"), invoke, renderer),
        groups,
    )
    _register_command(
        app,
        ("request", "respond"),
        _server_request_respond_command(registry.get("server-request-respond"), invoke, renderer),
        groups,
    )
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

    for route in _SIMPLE_ROUTES:
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
    context_settings: dict[str, object] | None = None,
) -> None:
    if len(path) == 1:
        app.command(name=path[0], help=callback.__doc__, context_settings=context_settings)(
            callback
        )
        return
    group_name, command_name = path
    if groups is None:
        groups = {}
    group = groups.get(group_name)
    if group is None:
        group = typer.Typer(no_args_is_help=True, add_completion=False)
        groups[group_name] = group
        app.add_typer(group, name=group_name)
    group.command(name=command_name, help=callback.__doc__, context_settings=context_settings)(
        callback
    )


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
        yes = bool(kwargs.pop("yes", False))
        no_interactive = bool(kwargs.pop("no_interactive", False))
        for field in _CALLER_RESOLVED_PATH_FIELDS.get(op.id, frozenset()):
            value = kwargs.get(field)
            if isinstance(value, str):
                kwargs[field] = str(Path(value).expanduser().resolve())
        if op.intent == "destroy":
            if no_interactive and not yes:
                typer.secho(
                    f"dispatch: destroy op {op.id!r} requires --yes with --no-interactive",
                    fg="red",
                    err=True,
                )
                raise typer.Exit(code=2)
            if not yes:
                typer.confirm(f"Run destroy op {op.id!r}?", abort=True, err=True)
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
    if op.intent == "destroy":
        parameters.extend(
            [
                inspect.Parameter(
                    "yes",
                    inspect.Parameter.KEYWORD_ONLY,
                    default=typer.Option(False, "--yes", help="Confirm this destroy operation."),
                    annotation=bool,
                ),
                inspect.Parameter(
                    "no_interactive",
                    inspect.Parameter.KEYWORD_ONLY,
                    default=typer.Option(
                        False,
                        "--no-interactive",
                        help="Fail instead of prompting unless --yes is also provided.",
                    ),
                    annotation=bool,
                ),
            ]
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


_STDIN_FIELDS: dict[str, str] = {"goal_file": "goal", "input_file": "text"}
_PATH_FIELDS: tuple[str, ...] = (
    "packet",
    "input_file",
    "goal_file",
    "output_schema_file",
    "base_file",
    "developer_file",
    "worktree_path",
)
_CALLER_RESOLVED_PATH_FIELDS: dict[str, frozenset[str]] = {
    "permissions": frozenset({"cwd"}),
}


def _new_command(registry: OpRegistry, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    new_op = registry.get("new")
    plan_op = registry.get("new-plan")
    parameters = [param for param in _parameters(new_op) if param.name != "content"]
    parameters = [_new_subscribe_parameter(param) for param in parameters]
    parameters[len(parameters) - 1 : len(parameters) - 1] = _image_parameters()
    parameters.insert(
        len(parameters) - 1,  # before the trailing --json option
        inspect.Parameter(
            "subscribe_spec",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(
                None,
                "--subscribe-spec",
                help="Explicit compact subscription spec for --subscribe.",
            ),
            annotation=str | None,
        ),
    )
    parameters.insert(
        len(parameters) - 1,  # before the trailing --json option
        inspect.Parameter(
            "dry_run",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(
                False, "--dry-run", help="Resolve and print the launch plan without mutating state."
            ),
            annotation=bool,
        ),
    )
    parameters.insert(
        0,
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context),
    )

    def command(ctx: typer.Context, **kwargs: object) -> None:
        json_requested = bool(kwargs.pop("json", False))
        dry_run = bool(kwargs.pop("dry_run", False))
        _resolve_new_subscribe(ctx, kwargs)
        _resolve_new_stdin(kwargs)
        _absolutize_new_paths(kwargs)
        _resolve_image_options(kwargs)
        if kwargs.get("subscribe") is not None:
            kwargs["caller_thread_id"] = os.environ.get("CODEX_THREAD_ID")
        op = plan_op if dry_run else new_op
        result = invoke(op.id, kwargs)
        render(op, result)
        _ignore_json(json_requested)

    command.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    command.__name__ = "new"
    command.__doc__ = new_op.summary
    return command


def _new_subscribe_parameter(param: inspect.Parameter) -> inspect.Parameter:
    if param.name != "subscribe":
        return param
    return inspect.Parameter(
        "subscribe",
        inspect.Parameter.KEYWORD_ONLY,
        default=typer.Option(
            False,
            "--subscribe",
            help="Create a default subscription to the new lane; optionally followed by a spec.",
        ),
        annotation=bool,
    )


def _resolve_new_subscribe(ctx: typer.Context, kwargs: dict[str, object]) -> None:
    subscribe = bool(kwargs.pop("subscribe", False))
    subscribe_spec = kwargs.pop("subscribe_spec", None)
    extras = list(ctx.args)
    if extras:
        if not subscribe:
            typer.secho(
                f"dispatch: unexpected argument(s): {' '.join(extras)}",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=2)
        if len(extras) > 1:
            typer.secho("dispatch: provide at most one --subscribe spec", fg="red", err=True)
            raise typer.Exit(code=2)
        if subscribe_spec is not None:
            typer.secho(
                "dispatch: use either --subscribe <spec> or --subscribe-spec, not both",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=2)
        subscribe_spec = extras[0]
    kwargs["subscribe"] = (subscribe_spec or "default") if subscribe or subscribe_spec else None


def _resolve_new_stdin(kwargs: dict[str, object]) -> None:
    """Read one launch input from stdin (``--goal-file -`` / ``--input-file -``).

    The daemon has no terminal stdin, so the CLI inlines it here. At most one
    consumer; the inline twin (``--goal``/``--text``) must not also be set."""
    consumers = [field for field in _STDIN_FIELDS if kwargs.get(field) == "-"]
    if len(consumers) > 1:
        typer.secho("dispatch: read at most one launch input from stdin (-)", fg="red", err=True)
        raise typer.Exit(code=2)
    for field in consumers:
        inline = _STDIN_FIELDS[field]
        if kwargs.get(inline) is not None:
            flag = field.replace("_", "-")
            typer.secho(
                f"dispatch: --{flag} - conflicts with an inline value for --{inline}",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=2)
        kwargs[inline] = sys.stdin.read()
        kwargs[field] = None


def _absolutize_new_paths(kwargs: dict[str, object]) -> None:
    """Resolve packet/file paths against the caller's cwd before the daemon reads them."""
    for field in _PATH_FIELDS:
        value = kwargs.get(field)
        if isinstance(value, str) and value not in ("", "-"):
            kwargs[field] = str(Path(value).expanduser().resolve())


def _send_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        lane: Annotated[str, typer.Argument(help="Thread selector.")],
        text: Annotated[str | None, typer.Argument(help="Message text.")] = None,
        input_file: Annotated[
            str | None, typer.Option("--input-file", help="Message text file (use - for stdin).")
        ] = None,
        image: Annotated[
            list[str] | None, typer.Option("--image", help="Local image path (repeatable).")
        ] = None,
        image_url: Annotated[
            list[str] | None, typer.Option("--image-url", help="HTTPS image URL (repeatable).")
        ] = None,
        image_detail: Annotated[
            _ImageDetail | None, typer.Option("--image-detail", help="Image detail hint.")
        ] = None,
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
            typer.Option(
                "--intro",
                help="Append dispatch attribution and reply hint from CODEX_THREAD_ID.",
            ),
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
        params: dict[str, object] = {
            "lane": lane,
            "text": _read_send_text(text, input_file),
            "mode": chosen[0] if chosen else mode,
            "intro": intro,
            "image": image or [],
            "image_url": image_url or [],
            "image_detail": image_detail,
        }
        _resolve_image_options(params)
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


def _image_parameters() -> list[inspect.Parameter]:
    return [
        inspect.Parameter(
            "image",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option([], "--image", help="Local image path (repeatable)."),
            annotation=list[str],
        ),
        inspect.Parameter(
            "image_url",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option([], "--image-url", help="HTTPS image URL (repeatable)."),
            annotation=list[str],
        ),
        inspect.Parameter(
            "image_detail",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(None, "--image-detail", help="Image detail hint."),
            annotation=_ImageDetail | None,
        ),
    ]


def _resolve_image_options(kwargs: dict[str, object]) -> None:
    images = [
        str(Path(path).expanduser().resolve()) for path in cast(list[str], kwargs.pop("image", []))
    ]
    urls = [str(url) for url in cast(list[str], kwargs.pop("image_url", []))]
    detail = kwargs.pop("image_detail", None)
    if detail is not None and not images and not urls:
        typer.secho("dispatch: --image-detail requires --image or --image-url", fg="red", err=True)
        raise typer.Exit(code=2)
    content: list[dict[str, object]] = []
    content.extend({"type": "local_image", "path": path, "detail": detail} for path in images)
    content.extend({"type": "image", "url": url, "detail": detail} for url in urls)
    if content:
        kwargs["content"] = content


def _read_send_text(text: str | None, input_file: str | None) -> str | None:
    if text is not None and input_file is not None:
        typer.secho("dispatch: TEXT and --input-file are mutually exclusive", fg="red", err=True)
        raise typer.Exit(code=2)
    if input_file is None:
        return text
    if input_file == "-":
        return sys.stdin.read()
    try:
        return Path(input_file).expanduser().read_text()
    except OSError as exc:
        typer.secho(f"dispatch: cannot read --input-file: {exc}", fg="red", err=True)
        raise typer.Exit(code=2) from exc


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


def _query_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        query: Annotated[
            str | None,
            typer.Argument(help="Optional local indexed text query."),
        ] = None,
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
        source: Annotated[
            str | None, typer.Option("--source", help="Only include threads by source.")
        ] = None,
        status: Annotated[
            str | None, typer.Option("--status", help="Only include threads by status.")
        ] = None,
        archived: Annotated[
            bool, typer.Option("--archived", help="Query archived managed threads.")
        ] = False,
        since: Annotated[
            str | None, typer.Option("--since", help="Inclusive ISO date/time lower bound.")
        ] = None,
        until: Annotated[
            str | None, typer.Option("--until", help="Inclusive ISO date/time upper bound.")
        ] = None,
        date_field: Annotated[
            _QueryDateField, typer.Option("--date-field", help="Lane timestamp for date filters.")
        ] = "updated_at",
        item_type: Annotated[
            str | None, typer.Option("--type", help="Only include matching item types.")
        ] = None,
        role: Annotated[
            str | None, typer.Option("--role", help="Only include matching item roles.")
        ] = None,
        tool: Annotated[
            str | None, typer.Option("--tool", help="Only include matching tool names.")
        ] = None,
        tool_server: Annotated[
            str | None, typer.Option("--tool-server", help="Only include matching tool servers.")
        ] = None,
        tool_status: Annotated[
            str | None, typer.Option("--tool-status", help="Only include matching tool status.")
        ] = None,
        errored: Annotated[
            bool | None,
            typer.Option("--errored/--not-errored", help="Only include errored or clean items."),
        ] = None,
        file: Annotated[
            str | None, typer.Option("--file", help="Only include matching file refs.")
        ] = None,
        file_under: Annotated[
            str | None, typer.Option("--file-under", help="Only include refs under a path.")
        ] = None,
        ext: Annotated[
            str | None, typer.Option("--ext", help="Only include file refs with extension.")
        ] = None,
        mentions_thread: Annotated[
            str | None, typer.Option("--mentions-thread", help="Only include thread refs.")
        ] = None,
        turn: Annotated[str | None, typer.Option("--turn", help="Only include one turn id.")] = (
            None
        ),
        item_id: Annotated[
            str | None, typer.Option("--item-id", help="Only include one item id.")
        ] = None,
        arg_key: Annotated[
            str | None, typer.Option("--arg-key", help="Only include tool calls with arg key.")
        ] = None,
        raw_retained: Annotated[
            bool | None,
            typer.Option(
                "--raw-retained/--no-raw-retained",
                help="Only include items with or without raw payloads.",
            ),
        ] = None,
        limit: Annotated[int, typer.Option(help="Max matches to return.")] = 20,
        max_scan: Annotated[int, typer.Option("--max-scan", help="Max indexed items to scan.")] = (
            200
        ),
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        result = invoke(
            op.id,
            {
                "query": query,
                "lane": lane,
                "directory": directory,
                "repo": repo,
                "source": source,
                "status": status,
                "archived": archived,
                "since": since,
                "until": until,
                "date_field": date_field,
                "item_type": item_type,
                "role": role,
                "tool": tool,
                "tool_server": tool_server,
                "tool_status": tool_status,
                "errored": errored,
                "file": file,
                "file_under": file_under,
                "ext": ext,
                "mentions_thread": mentions_thread,
                "turn": turn,
                "item_id": item_id,
                "arg_key": arg_key,
                "raw_retained": raw_retained,
                "limit": limit,
                "max_scan": max_scan,
            },
        )
        render(op, result)
        _ignore_json(json)

    command.__doc__ = op.summary
    return command


def _history_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        lane: Annotated[
            str | None,
            typer.Argument(help="Optional thread selector. Omit for overview."),
        ] = None,
        view: Annotated[_HistoryView, typer.Option("--view", help="History view.")] = "auto",
        item_type: Annotated[
            str | None, typer.Option("--type", help="Only include matching item types.")
        ] = None,
        role: Annotated[
            str | None, typer.Option("--role", help="Only include matching item roles.")
        ] = None,
        phase: Annotated[
            str | None, typer.Option("--phase", help="Only include matching message phases.")
        ] = None,
        tool: Annotated[
            str | None, typer.Option("--tool", help="Only include matching tool names.")
        ] = None,
        tool_server: Annotated[
            str | None, typer.Option("--tool-server", help="Only include matching tool servers.")
        ] = None,
        tool_status: Annotated[
            str | None, typer.Option("--tool-status", help="Only include matching tool status.")
        ] = None,
        errored: Annotated[
            bool | None,
            typer.Option("--errored/--not-errored", help="Only include errored or clean items."),
        ] = None,
        mentions_thread: Annotated[
            str | None, typer.Option("--mentions-thread", help="Only include thread refs.")
        ] = None,
        arg_key: Annotated[
            str | None, typer.Option("--arg-key", help="Only include tool calls with arg key.")
        ] = None,
        grep: Annotated[
            str | None, typer.Option("--grep", help="Only include items containing text.")
        ] = None,
        cwd: Annotated[
            str | None, typer.Option("--cwd", help="Only include threads whose cwd contains text.")
        ] = None,
        source: Annotated[
            str | None, typer.Option("--source", help="Only include threads by source.")
        ] = None,
        status: Annotated[
            str | None, typer.Option("--status", help="Only include threads by status.")
        ] = None,
        has_tool: Annotated[
            str | None, typer.Option("--has-tool", help="Only include summaries using this tool.")
        ] = None,
        changed: Annotated[
            bool | None,
            typer.Option(
                "--changed/--clean",
                help="Only include summaries with changed or clean workspace files.",
            ),
        ] = None,
        min_bytes: Annotated[
            int | None,
            typer.Option("--min-bytes", help="Only include transcripts at least this large."),
        ] = None,
        raw: Annotated[bool, typer.Option("--raw", help="Include raw item payloads.")] = False,
        limit: Annotated[int, typer.Option("--limit", help="Max rows/items to return.")] = 50,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        result = invoke(
            op.id,
            {
                "lane": lane,
                "view": view,
                "item_type": item_type,
                "role": role,
                "phase": phase,
                "tool": tool,
                "tool_server": tool_server,
                "tool_status": tool_status,
                "errored": errored,
                "mentions_thread": mentions_thread,
                "arg_key": arg_key,
                "grep": grep,
                "cwd": cwd,
                "source": source,
                "status": status,
                "has_tool": has_tool,
                "changed": changed,
                "min_bytes": min_bytes,
                "raw": raw,
                "limit": limit,
            },
        )
        render(op, result)
        _ignore_json(json)

    command.__doc__ = op.summary
    return command


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
        archived: Annotated[
            bool, typer.Option("--archived", help="List archived unmanaged sessions.")
        ] = False,
        parent: Annotated[
            str | None, typer.Option("--parent", help="Only direct children of this thread.")
        ] = None,
        ancestor: Annotated[
            str | None, typer.Option("--ancestor", help="Only descendants of this thread.")
        ] = None,
        root: Annotated[
            str | None, typer.Option("--root", help="Only threads in this rooted tree.")
        ] = None,
        limit: Annotated[int, typer.Option(help="Max unmanaged sessions to list.")] = 50,
        topology_limit: Annotated[
            int, typer.Option("--topology-limit", help="Max topology nodes to return.")
        ] = 50,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        op = discover if unmanaged else roster
        params: dict[str, object] = (
            {
                "limit": limit,
                "archived": archived,
                "parent": parent,
                "ancestor": ancestor,
                "root": root,
                "topology_limit": topology_limit,
            }
            if unmanaged
            else {
                "include_archived": include_archived,
                "parent": parent,
                "ancestor": ancestor,
                "root": root,
                "topology_limit": topology_limit,
            }
        )
        render(op, invoke(op.id, params))
        _ignore_json(json)

    command.__doc__ = "List managed threads, or unmanaged discoverable sessions."
    return command


def _subscribe_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        target: Annotated[str, typer.Argument(help="Target thread selector.")],
        spec: Annotated[
            str | None,
            typer.Argument(help="Compact spec: when:done,to:self,delivery:turn."),
        ] = None,
        when: Annotated[str | None, typer.Option("--when", help="Subscription condition.")] = None,
        to: Annotated[
            str | None, typer.Option("--to", help="Subscriber thread selector or self.")
        ] = None,
        delivery: Annotated[
            str | None, typer.Option("--delivery", help="Delivery mode: turn or inbox.")
        ] = None,
        deliver: Annotated[
            str | None, typer.Option("--deliver", help="Delivery policy: idle or now.")
        ] = None,
        tail: Annotated[int | None, typer.Option("--tail", help="Latest messages to include.")] = (
            None
        ),
        once: Annotated[
            bool | None, typer.Option("--once/--repeat", help="Complete after first match.")
        ] = None,
        ack: Annotated[
            str | None, typer.Option("--ack", help="Acknowledgement policy: auto or manual.")
        ] = None,
        attribution: Annotated[
            bool | None,
            typer.Option(
                "--attribution/--no-attribution",
                help="Append dispatch attribution to turn-delivered subscription updates.",
            ),
        ] = None,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        params: dict[str, object] = {
            "target": target,
            "spec": spec,
            "when": when,
            "to": to,
            "delivery": delivery,
            "deliver": deliver,
            "tail": tail,
            "once": once,
            "ack": ack,
            "attribution": attribution,
            "caller_thread_id": os.environ.get("CODEX_THREAD_ID"),
        }
        result = invoke(op.id, params)
        render(op, result)
        _ignore_json(json)

    command.__doc__ = op.summary
    return command


def _inbox_list_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        lane: Annotated[
            str | None,
            typer.Option("--lane", "--thread", help="Recipient thread selector."),
        ] = None,
        state: Annotated[str | None, typer.Option("--state", help="Inbox state filter.")] = (
            "pending"
        ),
        kind: Annotated[str | None, typer.Option("--kind", help="Inbox message kind.")] = None,
        limit: Annotated[int, typer.Option("--limit", help="Max messages to return.")] = 50,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        result = invoke(
            op.id,
            {
                "lane": lane,
                "state": state,
                "kind": kind,
                "limit": limit,
                "caller_thread_id": os.environ.get("CODEX_THREAD_ID"),
            },
        )
        render(op, result)
        _ignore_json(json)

    command.__doc__ = op.summary
    return command


def _inbox_ack_command(op: Op, invoke: Invoker, render: Renderer) -> Callable[..., None]:
    def command(
        id: Annotated[int | None, typer.Argument(help="Inbox message id.")] = None,
        all: Annotated[bool, typer.Option("--all", help="Ack all pending messages.")] = False,
        lane: Annotated[
            str | None,
            typer.Option("--lane", "--thread", help="Recipient thread selector for --all."),
        ] = None,
        json: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        result = invoke(
            op.id,
            {
                "id": id,
                "all": all,
                "lane": lane,
                "caller_thread_id": os.environ.get("CODEX_THREAD_ID"),
            },
        )
        render(op, result)
        _ignore_json(json)

    command.__doc__ = op.summary
    return command


def _server_request_respond_command(
    op: Op, invoke: Invoker, render: Renderer
) -> Callable[..., None]:
    def command(
        id: Annotated[int, typer.Argument(help="Dispatch-local interactive request id.")],
        response: Annotated[str, typer.Argument(help="JSON protocol result object.")],
        json_output: Annotated[
            bool, typer.Option("--json", help="Render machine-readable JSON output.")
        ] = False,
    ) -> None:
        try:
            parsed = json.loads(response)
        except ValueError as exc:
            raise typer.BadParameter("response must be valid JSON", param_hint="response") from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter("response must be a JSON object", param_hint="response")
        result = invoke(op.id, {"id": id, "response": parsed})
        render(op, result)
        _ignore_json(json_output)

    command.__doc__ = op.summary
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
                "input": _cli_input_schema(op),
                "output": op.output.model_json_schema(),
            }
        )
        _ignore_json(json)

    command.__doc__ = "Print the derived JSON schemas for a command."
    return command


def _cli_input_schema(op: Op) -> dict[str, object]:
    schema = public_schema(deepcopy(op.input.model_json_schema()))
    adapter_id = "new" if op.id == "new-plan" else op.id
    if adapter_id not in CLI_ADAPTED_INPUT_FIELDS:
        return schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    for authored_field in CLI_ADAPTED_INPUT_FIELDS[adapter_id]:
        properties.pop(authored_field, None)
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        for name in ("MessageContent", "TextContent", "ImageUrlContent", "LocalImageContent"):
            definitions.pop(name, None)
        if not definitions:
            schema.pop("$defs", None)
    properties.update(
        {
            "image": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local image paths supplied with repeatable --image flags.",
            },
            "image_url": {
                "type": "array",
                "items": {"type": "string", "format": "uri"},
                "description": "HTTPS image URLs supplied with repeatable --image-url flags.",
            },
            "image_detail": {
                "anyOf": [
                    {"type": "string", "enum": ["auto", "low", "high", "original"]},
                    {"type": "null"},
                ],
                "default": None,
                "description": "Image detail hint applied to this invocation.",
            },
        }
    )
    if op.id == "send":
        properties["input_file"] = {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
            "description": "Message text file; '-' reads stdin.",
        }
    return schema


def _schema_op_id(command: str) -> str:
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        parts = command.strip().split()
    flags = {part for part in parts if part.startswith("--")}
    words = [part for part in parts if not part.startswith("--")]
    normalized = "-".join(words) if words else command.strip().replace(" ", "-")
    if normalized == "tail" and "--follow" in flags:
        return "__unknown_tail_follow__"
    schema_routes = cli_schema_routes()
    aliases = {command.replace(" ", "-"): op_id for command, op_id in schema_routes.items()}
    flagged = " ".join((*words, *sorted(flags)))
    return schema_routes.get(flagged) or aliases.get(normalized, normalized)


def _default_render(op: Op, result: dict[str, object]) -> None:
    _print_json(data=result)


def _ignore_json(_requested: bool) -> None:
    """Successful CLI output is already JSON; keep --json as an explicit stable affordance."""


def _print_json(*, data: dict[str, object]) -> None:
    typer.echo(json.dumps(data, indent=2))

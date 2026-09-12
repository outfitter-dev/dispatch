"""Normalize provider-owned thread relationships for core projections."""

from __future__ import annotations

from outfitter.dispatch.client.models import ThreadInfo
from outfitter.dispatch.core.models import ThreadTopologyNode, ThreadTopologyView
from outfitter.dispatch.registry.models import (
    Lane,
    ProviderThreadLifecycleState,
    ProviderThreadNode,
    ProviderThreadObservation,
)
from outfitter.dispatch.registry.store import DEFAULT_CODEX_BINDING_ID, Registry


def observation_from_thread(
    thread: ThreadInfo,
    *,
    lifecycle_state: ProviderThreadLifecycleState | None = None,
    relationship_source: str,
    observed_at: str | None = None,
) -> ProviderThreadObservation:
    spawned = thread.spawned_source or {}
    spawned_parent = spawned.get("parent_thread_id")
    spawned_depth = spawned.get("depth")
    spawned_nickname = spawned.get("agent_nickname")
    spawned_role = spawned.get("agent_role")
    return ProviderThreadObservation(
        binding_id=DEFAULT_CODEX_BINDING_ID,
        provider_thread_id=thread.id,
        session_id=thread.session_id,
        parent_thread_id=thread.parent_thread_id
        or (spawned_parent if isinstance(spawned_parent, str) else None),
        forked_from_id=thread.forked_from_id,
        source_kind=thread.source_kind,
        thread_source=thread.thread_source,
        agent_nickname=thread.agent_nickname
        or (spawned_nickname if isinstance(spawned_nickname, str) else None),
        agent_role=thread.agent_role or (spawned_role if isinstance(spawned_role, str) else None),
        agent_depth=spawned_depth
        if isinstance(spawned_depth, int) and not isinstance(spawned_depth, bool)
        else None,
        lifecycle_state=lifecycle_state,
        relationship_source=relationship_source,
        confidence=1.0,
        observed_at=observed_at,
    )


async def observe_thread(
    registry: Registry,
    thread: ThreadInfo,
    *,
    lifecycle_state: ProviderThreadLifecycleState | None = None,
    relationship_source: str,
) -> None:
    await registry.upsert_provider_thread(
        observation_from_thread(
            thread,
            lifecycle_state=lifecycle_state,
            relationship_source=relationship_source,
            observed_at=registry.now_iso(),
        )
    )


async def observe_threads(
    registry: Registry,
    threads: list[ThreadInfo],
    *,
    lifecycle_state: ProviderThreadLifecycleState | None = None,
    relationship_source: str,
) -> None:
    observed_at = registry.now_iso()
    await registry.upsert_provider_threads(
        [
            observation_from_thread(
                thread,
                lifecycle_state=lifecycle_state,
                relationship_source=relationship_source,
                observed_at=observed_at,
            )
            for thread in threads
        ]
    )


def _node(node: ProviderThreadNode | None, relation: str) -> ThreadTopologyNode | None:
    if node is None:
        return None
    thread = node.thread
    return ThreadTopologyNode(
        id=thread.provider_thread_id,
        provider=thread.provider,
        binding_id=thread.binding_id,
        managed=node.managed,
        ref=node.ref,
        handle=node.handle,
        lifecycle_state=thread.lifecycle_state,
        relation=relation,
        source_kind=thread.source_kind,
        thread_source=thread.thread_source,
        agent_nickname=thread.agent_nickname,
        agent_role=thread.agent_role,
        agent_depth=thread.agent_depth,
    )


def _nodes(nodes: list[ProviderThreadNode], relation: str) -> list[ThreadTopologyNode]:
    return [projected for item in nodes if (projected := _node(item, relation)) is not None]


async def topology_views(
    registry: Registry,
    thread_ids: list[str],
    *,
    max_nodes: int,
    provider: str = "codex",
    binding_id: str = DEFAULT_CODEX_BINDING_ID,
) -> dict[str, ThreadTopologyView]:
    if not thread_ids:
        return {}
    topology = await registry.get_provider_thread_topology(
        provider,
        thread_ids,
        binding_id=binding_id,
        max_nodes=max_nodes,
        max_depth=16,
    )
    indexed = {node.thread.provider_thread_id: node for node in topology.nodes}
    views: dict[str, ThreadTopologyView] = {}
    for thread_id in thread_ids:
        current = indexed.get(thread_id)
        ancestry = topology.parent_ancestry.get(thread_id, [])
        views[thread_id] = ThreadTopologyView(
            observed=current is not None,
            parent=_node(ancestry[0] if ancestry else None, "parent"),
            root=_node(topology.roots.get(thread_id), "root"),
            forked_from=_node(topology.fork_origins.get(thread_id), "fork_origin"),
            children=_nodes(topology.children.get(thread_id, []), "child"),
            descendants=_nodes(topology.descendants.get(thread_id, []), "descendant"),
            forks=_nodes(topology.forks.get(thread_id, []), "fork"),
            complete=topology.complete,
            cycle_detected=topology.cycle_detected,
            truncated=topology.truncated,
            observed_at=current.thread.last_seen_at if current is not None else None,
        )
    return views


async def lane_topology_views(
    registry: Registry, lanes: list[Lane], *, max_nodes: int
) -> dict[str, ThreadTopologyView]:
    """Project topology by stable lane id without confusing it with native identity."""

    views: dict[str, ThreadTopologyView] = {}
    groups: dict[tuple[str, str], list[Lane]] = {}
    for lane in lanes:
        if lane.provider_session_id is None:
            views[lane.id] = ThreadTopologyView()
            continue
        groups.setdefault((lane.provider, lane.binding_id), []).append(lane)
    for (provider, binding_id), group in groups.items():
        native_views = await topology_views(
            registry,
            [lane.provider_session_id for lane in group if lane.provider_session_id is not None],
            max_nodes=max_nodes,
            provider=provider,
            binding_id=binding_id,
        )
        for lane in group:
            assert lane.provider_session_id is not None
            views[lane.id] = native_views[lane.provider_session_id]
    return views

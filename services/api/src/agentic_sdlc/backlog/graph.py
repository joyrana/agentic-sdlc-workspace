"""Pure dependency-graph algorithms used by the planner, the backlog service and validation."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Hashable, Iterable
from typing import TypeVar

N = TypeVar("N", bound=Hashable)


def find_cycle(nodes: Iterable[N], edges: Iterable[tuple[N, N]]) -> list[N] | None:
    """Return one cycle as a node list (first == last) or None. Edge (a, b) means a depends on b."""
    graph: dict[N, list[N]] = defaultdict(list)
    for a, b in edges:
        graph[a].append(b)
    white, grey, black = 0, 1, 2
    color: dict[N, int] = defaultdict(int)
    stack_path: list[N] = []

    def visit(n: N) -> list[N] | None:
        color[n] = grey
        stack_path.append(n)
        for m in graph.get(n, []):
            if color[m] == grey:
                i = stack_path.index(m)
                return [*stack_path[i:], m]
            if color[m] == white:
                found = visit(m)
                if found:
                    return found
        stack_path.pop()
        color[n] = black
        return None

    for n in list(nodes) + list(graph):
        if color[n] == white:
            found = visit(n)
            if found:
                return found
    return None


def would_create_cycle(edges: Iterable[tuple[N, N]], new_edge: tuple[N, N]) -> list[N] | None:
    all_edges = [*edges, new_edge]
    nodes = {x for e in all_edges for x in e}
    return find_cycle(nodes, all_edges)


def topological_layers(nodes: Iterable[N], edges: Iterable[tuple[N, N]]) -> list[list[N]]:
    """Kahn's algorithm grouped into layers: layer k can start once layers < k are done.

    Raises ValueError on a cycle. Nodes within a layer keep input order (deterministic output).
    """
    order = list(dict.fromkeys(nodes))
    deps: dict[N, set[N]] = {n: set() for n in order}
    dependents: dict[N, list[N]] = defaultdict(list)
    for a, b in edges:
        if a not in deps or b not in deps:
            raise ValueError(f"edge references unknown node: {a!r} -> {b!r}")
        if b not in deps[a]:
            deps[a].add(b)
            dependents[b].append(a)
    remaining = {n: len(d) for n, d in deps.items()}
    ready = deque(n for n in order if remaining[n] == 0)
    layers: list[list[N]] = []
    seen = 0
    while ready:
        layer = list(ready)
        ready.clear()
        layers.append(layer)
        seen += len(layer)
        nxt: list[N] = []
        for n in layer:
            for d in dependents[n]:
                remaining[d] -= 1
                if remaining[d] == 0:
                    nxt.append(d)
        ready.extend(sorted(nxt, key=order.index))
    if seen != len(order):
        raise ValueError("dependency graph contains a cycle")
    return layers


def critical_path(nodes: Iterable[N], edges: Iterable[tuple[N, N]], weight: dict[N, float]) -> list[N]:
    """Longest weighted chain through the DAG (what bounds delivery time)."""
    edge_list = list(edges)
    layers = topological_layers(nodes, edge_list)
    deps: dict[N, list[N]] = defaultdict(list)
    for a, b in edge_list:
        deps[a].append(b)
    best: dict[N, tuple[float, list[N]]] = {}
    for layer in layers:
        for n in layer:
            prev = max((best[d] for d in deps[n]), key=lambda x: x[0], default=(0.0, []))
            best[n] = (prev[0] + weight.get(n, 0.0), [*prev[1], n])
    return max(best.values(), key=lambda x: x[0], default=(0.0, []))[1]

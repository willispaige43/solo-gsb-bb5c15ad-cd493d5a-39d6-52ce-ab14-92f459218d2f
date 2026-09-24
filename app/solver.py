"""差分约束求解：Bellman-Ford 负环检测与坐标紧界推导。

建模
----
每条原始约束 ``c: coord(end) - coord(start) ∈ [lower, upper]``
（单位：整数微米）转成两条差分不等式：

* ``coord(end)   - coord(start) ≤ upper``  → 边 start→end，权 upper（上限边）
* ``coord(start) - coord(end)   ≤ -lower`` → 边 end→start，权 -lower（下限反向边）

基准点坐标恒为 0。取第一个基准点 ``anchor`` 作参考点（距离初始化为 0），
其余基准点与 anchor 之间各加两条权为 0 的 ``datum_pin`` 边，表示
``coord(di) = coord(anchor) = 0``。

在差分图上跑 Bellman-Ford：
不存在负环 ⇔ 所有不等式可同时满足 ⇔ 公差可行；
存在负环时，环上边权之和严格为负，沿环累加得到 0 < 负数的矛盾，
环中除 ``datum_pin`` 外的边所对应的原始约束编号即构成矛盾闭环。

可行时的逐点紧界（``solve_ranges``）
------------------------------------
在 ``x_v - x_u ≤ w(u→v)`` 的差分图中，沿任意 anchor→v 路径累加都有
``x_v ≤ 路径权和``，故 ``max x_v`` 等于 **原图 anchor→v 的最短距离**；
取各点最短距离赋值本身满足全部不等式，因此该上界可由一组全局可行坐标达到。

同理 ``min x_v`` 等于 **转置图（全部边反向、权不变）anchor→v 最短距离
的相反数**（即原图 v→anchor 的最短距离取负），其极值赋值同样可行。
跨测点约束、多个基准点互钉为 0 的 pin 边、负权边均已在图中，故这是
纳入全部固定关系后的全局紧界；各测点的端点不必由同一组坐标同时达到。
整数权 ⇒ 最短路距离为整数，紧界本身即整数微米坐标。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .schemas import ChainRequest, ConstraintIn

INF: int = 10**30


class ValidationError(Exception):
    """请求在求解前的静态校验错误（未知点 / 反向区间 / 未连通等）。"""

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} 个校验错误")


@dataclass(frozen=True)
class Edge:
    fr: str  # 差分图边起点
    to: str  # 差分图边终点
    weight: int
    cid: str | None  # 原始约束编号；基准点固定边为 None
    bound: str  # "upper" | "lower" | "datum_pin"
    start: str | None  # 原始约束起点（pin 边为 None）
    end: str | None  # 原始约束终点（pin 边为 None）


@dataclass
class SolveResult:
    feasible: bool
    coordinates: dict[str, int]
    # 不可行时：按有向环顺序排列的边（含 datum_pin 边以便审计）
    cycle_edges: list[Edge]
    cycle_weight: int
    # solve_ranges 可行时：测点编号 -> (最小坐标, 最大坐标)
    ranges: dict[str, tuple[int, int]] | None = None


def _err(code: str, message: str, location: list, constraint_id: str | None = None) -> dict:
    item: dict = {
        "code": code,
        "message": message,
        "location": location,
    }
    if constraint_id is not None:
        item["constraint_id"] = constraint_id
    return item


def validate_request(req: ChainRequest) -> None:
    """执行与求解无关的静态校验，收集全部错误后一次性抛出。"""
    errors: list[dict] = []

    datum = req.datum_points
    measure = req.measure_points

    seen_d: dict[str, int] = {}
    for i, p in enumerate(datum):
        if p in seen_d:
            errors.append(
                _err(
                    "duplicate_point",
                    f"基准点 {p!r} 重复声明（首次出现于 datum_points[{seen_d[p]}]）",
                    ["datum_points", i],
                )
            )
        else:
            seen_d[p] = i

    seen_m: dict[str, int] = {}
    for i, p in enumerate(measure):
        if p in seen_m:
            errors.append(
                _err(
                    "duplicate_point",
                    f"测点 {p!r} 重复声明（首次出现于 measure_points[{seen_m[p]}]）",
                    ["measure_points", i],
                )
            )
        else:
            seen_m[p] = i
        if p in seen_d:
            errors.append(
                _err(
                    "point_role_conflict",
                    f"点 {p!r} 不能同时作为基准点和测点",
                    ["measure_points", i],
                )
            )

    declared = set(seen_d) | set(seen_m)

    seen_c: dict[str, int] = {}
    for i, c in enumerate(req.constraints):
        if c.id in seen_c:
            errors.append(
                _err(
                    "duplicate_constraint",
                    f"约束编号 {c.id!r} 重复（首次出现于 constraints[{seen_c[c.id]}]）",
                    ["constraints", i],
                    c.id,
                )
            )
        else:
            seen_c[c.id] = i

        if c.start not in declared:
            errors.append(
                _err(
                    "unknown_point",
                    f"约束 {c.id!r} 的起点 {c.start!r} 未在基准点或测点中声明",
                    ["constraints", i, "start"],
                    c.id,
                )
            )
        if c.end not in declared:
            errors.append(
                _err(
                    "unknown_point",
                    f"约束 {c.id!r} 的终点 {c.end!r} 未在基准点或测点中声明",
                    ["constraints", i, "end"],
                    c.id,
                )
            )
        if c.lower > c.upper:
            errors.append(
                _err(
                    "reversed_interval",
                    f"约束 {c.id!r} 的公差区间反向：lower={c.lower} > upper={c.upper}",
                    ["constraints", i, "lower"],
                    c.id,
                )
            )

    # 无向连通性：每个测点必须经约束链连到至少一个基准点。
    # 存在未知点时连通性结果不可靠，跳过（未知点错误已上报）。
    if not any(e["code"] == "unknown_point" for e in errors):
        adjacency: dict[str, set[str]] = {p: set() for p in declared}
        for c in req.constraints:
            adjacency[c.start].add(c.end)
            adjacency[c.end].add(c.start)

        reached: set[str] = set()
        queue: deque[str] = deque(p for p in seen_d)
        reached.update(seen_d)
        while queue:
            cur = queue.popleft()
            for nxt in adjacency[cur]:
                if nxt not in reached:
                    reached.add(nxt)
                    queue.append(nxt)

        for i, p in enumerate(measure):
            if p not in reached:
                errors.append(
                    _err(
                        "point_not_connected_to_datum",
                        f"测点 {p!r} 未通过任何约束链连接到基准点，无法确定其坐标",
                        ["measure_points", i],
                    )
                )

    if errors:
        raise ValidationError(errors)


def _build_edges(req: ChainRequest, anchor: str) -> list[Edge]:
    """构造差分图的全部边（顺序固定，保证结果确定性）。"""
    edges: list[Edge] = []
    # 其余基准点与 anchor 互相钉在同一坐标（同为 0）
    for d in req.datum_points:
        if d == anchor:
            continue
        edges.append(Edge(anchor, d, 0, None, "datum_pin", None, None))
        edges.append(Edge(d, anchor, 0, None, "datum_pin", None, None))
    for c in req.constraints:
        edges.extend(_constraint_edges(c))
    return edges


def _constraint_edges(c: ConstraintIn) -> tuple[Edge, Edge]:
    return (
        Edge(c.start, c.end, c.upper, c.id, "upper", c.start, c.end),
        Edge(c.end, c.start, -c.lower, c.id, "lower", c.start, c.end),
    )


def _bellman_ford(
    nodes: list[str], edges: list[Edge], source: str
) -> tuple[dict[str, int], dict[str, Edge], Edge | None]:
    """从 source 跑 Bellman-Ford。

    返回 (最短距离, 前驱边, 负环线索边)：第 |V| 轮仍可松弛时线索边非空，
    配合前驱链可还原一个负环。
    """
    n = len(nodes)
    dist: dict[str, int] = {p: INF for p in nodes}
    dist[source] = 0
    pred: dict[str, Edge] = {}

    bad_edge: Edge | None = None
    for iteration in range(n):
        changed = False
        for e in edges:
            du = dist[e.fr]
            if du == INF:
                continue
            nd = du + e.weight
            if nd < dist[e.to]:
                dist[e.to] = nd
                pred[e.to] = e
                changed = True
                if iteration == n - 1:
                    bad_edge = e
        if not changed:
            break
    return dist, pred, bad_edge


def _extract_cycle(pred: dict[str, Edge], bad_edge: Edge, n: int) -> list[Edge]:
    """从第 n 轮仍被松弛的边沿前驱回退，还原一个有向负环。"""
    x = bad_edge.to
    for _ in range(n):
        x = pred[x].fr

    cycle: list[Edge] = []
    cur = x
    while True:
        e = pred[cur]
        cycle.append(e)
        cur = e.fr
        if cur == x:
            break
    cycle.reverse()  # 还原为沿有向环正向排列
    return cycle


def solve(req: ChainRequest) -> SolveResult:
    """静态校验通过后做可行性判定。调用方应先调用 validate_request。"""
    validate_request(req)

    anchor = req.datum_points[0]
    nodes = list(dict.fromkeys([*req.datum_points, *req.measure_points]))
    edges = _build_edges(req, anchor)

    dist, pred, bad_edge = _bellman_ford(nodes, edges, anchor)

    if bad_edge is None:
        # 可行：最短距离给出一组满足全部上界的坐标，基准点为 0
        coordinates = {p: int(dist[p]) for p in nodes}
        return SolveResult(True, coordinates, [], 0)

    cycle = _extract_cycle(pred, bad_edge, len(nodes))
    return SolveResult(False, {}, cycle, sum(e.weight for e in cycle))


def solve_ranges(req: ChainRequest) -> SolveResult:
    """可行性校核 + 可行时返回各测点的全局紧界（最小/最大整数微米坐标）。

    最大坐标取原图自 anchor 的最短距离；最小坐标取转置图自 anchor 的
    最短距离再取负。两组极值坐标各自全局可行，故界是紧的，但一般不是
    同一组坐标同时达到。
    """
    validate_request(req)

    anchor = req.datum_points[0]
    nodes = list(dict.fromkeys([*req.datum_points, *req.measure_points]))
    edges = _build_edges(req, anchor)

    upper_dist, pred, bad_edge = _bellman_ford(nodes, edges, anchor)
    if bad_edge is not None:
        cycle = _extract_cycle(pred, bad_edge, len(nodes))
        return SolveResult(False, {}, cycle, sum(e.weight for e in cycle))

    # 转置图：边全部反向、权不变。静态校验保证无向连通，转置图中 anchor
    # 同样可达所有点，距离均为有限值；原图无负环 ⇔ 转置图无负环。
    rev_edges = [
        Edge(e.to, e.fr, e.weight, e.cid, e.bound, e.start, e.end) for e in edges
    ]
    lower_dist, _, rev_bad = _bellman_ford(nodes, rev_edges, anchor)
    if rev_bad is not None:  # pragma: no cover - 理论上不可达：负环与其转置同时存在
        raise RuntimeError("转置图检测到负环，与原图可行性结论矛盾")

    coordinates = {p: int(upper_dist[p]) for p in nodes}
    ranges: dict[str, tuple[int, int]] = {}
    for p in req.measure_points:
        # -lower_dist[p] ≤ x_p ≤ upper_dist[p]
        ranges[p] = (-int(lower_dist[p]), int(upper_dist[p]))
    return SolveResult(True, coordinates, [], 0, ranges)

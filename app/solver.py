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

读数校正（``solve_reconcile``）
-------------------------------
每个测点 p 另给整数微米读数 r_p，要求校正坐标 x_p 满足
``|x_p − r_p| ≤ e``（e 为非负整数）。改写为两条差分不等式：

* ``x_p ≤ r_p + e`` → anchor→p 边，权 ``r_p + e``
* ``-x_p ≤ -r_p + e`` → p→anchor 边，权 ``-r_p + e``

即把每个测点“钉”到以读数为中心、半宽 e 的整数区间，与原尺寸约束合并为
同一张差分图，仍以无负环作为可行性判据。可行性对 e **单调**：e 可行则
更大的 e 必可行（可行域只扩不缩），且原约束系统可行时存在有限上界（原图
anchor→p 的最短距离给出一组可行坐标，取到读数的最大偏差即上界），故在
``[0, hi]`` 上二分即可找到全局最小非负整数 e。

读数边均以 anchor 为端点，并入后的任何负环都可归约为三类含读数边的简单环
（路径中段不经过 anchor；经过 anchor 的走法拆成这些简单环），其权只通过
``f(v)``（原图 anchor→v 最短路）、``g(v)``（原图 v→anchor 最短路）与
``h(v)``（去掉 anchor 的子图中 min_p(r_p + δ(p→v))）三个与 e 无关的量
依赖 e。三次 Bellman–Ford 预计算后，二分中每个 e 的可行性判定只需
O(|V|) 次比较；e* 下的最短距离
``d*(v) = min(f(v), h(v) + e*)`` 即达到 e* 的同一组整数可行校正坐标
（anchor 及与它 0 权互钉的基准点为 0）。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .schemas import ChainRequest, ConstraintIn, ReconcileRequest

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
    # solve_reconcile 可行时：最小非负整数 e（逐点校正量绝对值的最大值）
    max_abs_correction: int | None = None


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


def validate_readings(req: ReconcileRequest) -> None:
    """校验 readings 恰好覆盖全部测点：不得缺漏，也不得含基准点/额外测点。

    调用前 validate_request 已通过（故测点编号无重复、点角色不冲突）。
    """
    errors: list[dict] = []
    reading_ids = set(req.readings)
    datum = set(req.datum_points)
    measure_set = set(req.measure_points)

    for p in req.measure_points:
        if p not in reading_ids:
            errors.append(
                _err(
                    "reading_missing",
                    f"测点 {p!r} 缺少 readings 读数（readings 必须恰好覆盖全部测点）",
                    ["readings", p],
                )
            )

    for p in req.readings:  # dict 顺序即请求顺序
        if p in datum:
            errors.append(
                _err(
                    "reading_extra",
                    f"读数键 {p!r} 是基准点，readings 只能包含 measure_points 中声明的测点",
                    ["readings", p],
                )
            )
        elif p not in measure_set:
            errors.append(
                _err(
                    "reading_extra",
                    f"读数键 {p!r} 未在 measure_points 中声明（额外测点）",
                    ["readings", p],
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


def solve_reconcile(req: ReconcileRequest) -> SolveResult:
    """读数校正：求使全部测点同时满足 |校正坐标 − 读数| ≤ e 的最小非负整数 e。

    先按原尺寸约束判可行：无解则返回与 solve 相同的矛盾闭环。有解时把读数
    区间并入差分图，利用可行性对 e 的单调性二分求全局最小 e*，并返回 e* 图上
    达到该值的同一组可行校正坐标（基准点为 0）。

    读数边均以 anchor 为端点，故并入后的负环只有三类（路径中间不经过 anchor，
    经过 anchor 的走法可拆成前两类简单环）：

    * anchor →(原图最短路 f(v))→ v →(读数边 e−r_v)→ anchor；
    * anchor →(读数边 r_v+e)→ v →(原图最短路 g(v)=v→anchor)→ anchor；
    * anchor →(r_p+e)→ p →(去掉 anchor 的子图最短路 δ(p→q))→ q
      →(e−r_q)→ anchor，其中 min_p(r_p+δ(p→q)) 记为 h(q)。

    f/g/h 都与 e 无关，各跑一次 Bellman–Ford 预计算后，每个 e 的可行性
    判定只需 O(|V|) 次比较，二分成本可忽略。最终坐标取并入图的最短路距离
    d*(v) = min(f(v), h(v)+e*)（h(v) 经读数边给出 anchor 出发的新路径）。
    """
    validate_request(req)
    validate_readings(req)

    anchor = req.datum_points[0]
    nodes = list(dict.fromkeys([*req.datum_points, *req.measure_points]))
    measure = req.measure_points
    base_edges = _build_edges(req, anchor)

    # 阶段一：原尺寸约束本身必须有解；无解时沿用现有矛盾闭环。
    # f(v)：原图 anchor→v 最短距离（兼作可行性判定）。
    f_dist, pred, bad_edge = _bellman_ford(nodes, base_edges, anchor)
    if bad_edge is not None:
        cycle = _extract_cycle(pred, bad_edge, len(nodes))
        return SolveResult(False, {}, cycle, sum(e.weight for e in cycle))

    # g(v)：原图 v→anchor 最短距离 = 转置图 anchor→v 最短距离。
    rev_edges = [
        Edge(e.to, e.fr, e.weight, e.cid, e.bound, e.start, e.end) for e in base_edges
    ]
    rev_dist, _, rev_bad = _bellman_ford(nodes, rev_edges, anchor)
    if rev_bad is not None:  # pragma: no cover - 原图无负环 ⇔ 转置图无负环
        raise RuntimeError("转置图检测到负环，与原图可行性结论矛盾")

    # h(v)：去掉 anchor 的子图中 min_p(r_p + δ(p→v))。以超源点初始化
    # （测点初值 r_p、其余 INF）跑 Bellman–Ford；子图无负环（原图已可行）。
    inner_nodes = [p for p in nodes if p != anchor]
    inner_edges = [
        e for e in base_edges if e.fr != anchor and e.to != anchor
    ]
    readings: dict[str, int] = {p: req.readings[p] for p in measure}
    h_dist = {p: readings.get(p, INF) for p in inner_nodes}
    h_dist = _bellman_ford_distances(inner_nodes, inner_edges, h_dist)

    def feasible_at(e: int) -> bool:
        """并入读数区间（半宽 e）后的差分图是否存在负环。"""
        for p in measure:
            # 单侧读数环：f(p)+e-r_p<0；r_p+e+g(p)<0
            if int(f_dist[p]) + e - readings[p] < 0:
                return False
            if readings[p] + e + int(rev_dist[p]) < 0:
                return False
            # 双侧读数环：2e + h(p) − r_p < 0
            if 2 * e + int(h_dist[p]) - readings[p] < 0:
                return False
        return True

    # 可行性对 e 单调（区间只扩不缩）。f 本身是一组可行坐标，
    # 其到读数的最大偏差给出保证可行的有限上界。
    hi = max(abs(int(f_dist[p]) - readings[p]) for p in measure)
    lo = -1  # 约定 lo 侧“不可行”；e 从 0 起
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if feasible_at(mid):
            hi = mid
        else:
            lo = mid
    e_star = hi

    # e* 并入图的最短距离：原图路径 f(v) 或“读数边 + 子图最短路” h(v)+e*
    coordinates: dict[str, int] = {anchor: 0}
    for v in inner_nodes:
        via_reading = h_dist[v] + e_star if h_dist[v] != INF else INF
        coordinates[v] = int(min(f_dist[v], via_reading))
    return SolveResult(True, coordinates, [], 0, None, e_star)


def _bellman_ford_distances(
    nodes: list[str], edges: list[Edge], initial: dict[str, int]
) -> dict[str, int]:
    """从给定初始距离（等价于超源点边）跑 Bellman–Ford，只返回最短距离。

    用于 reconcile 的子图最短路：原图已被证明无负环，子图不可能出现负环。
    """
    dist = dict(initial)
    for _ in range(len(nodes)):
        changed = False
        for e in edges:
            du = dist[e.fr]
            if du == INF:
                continue
            nd = du + e.weight
            if nd < dist[e.to]:
                dist[e.to] = nd
                changed = True
        if not changed:
            break
    return dist

"""solve_reconcile 的随机差分测试：与暴力枚举的极小化最大值交叉验证。

对每个随机实例另给各测点随机整数读数，枚举所有满足全部约束（基准点为 0）
的整数坐标，直接计算 e* = min 赋值 max_p |x_p − r_p|，与 Bellman-Ford +
单调性二分求得的 max_abs_correction 比对，并核对返回坐标确实达到 e*。
"""

from __future__ import annotations

import random

from app.schemas import ConstraintIn, ReconcileRequest
from app.solver import solve_reconcile
from tests.test_random import brute_force_solutions, random_instance


def _multi_datum_instance(rng: random.Random):
    """2 个基准点 + 2 个测点的小规模随机实例（含基准互钉结构）。"""
    datums = ["D1", "D2"]
    measure = ["M0", "M1"]
    nodes = datums + measure
    # 先保证无向连通（每条边落在随机两点间）
    connected = [nodes[0]]
    cons = []
    cid = 0
    for child in nodes[1:]:
        parent = rng.choice(connected)
        lo, hi = sorted((rng.randint(-5, 5), rng.randint(-5, 5)))
        cons.append(ConstraintIn(id=f"c{cid}", start=parent, end=child, lower=lo, upper=hi))
        connected.append(child)
        cid += 1
    for _ in range(rng.randint(0, 2)):
        a, b = rng.sample(nodes, 2)
        lo, hi = sorted((rng.randint(-5, 5), rng.randint(-5, 5)))
        cons.append(ConstraintIn(id=f"c{cid}", start=a, end=b, lower=lo, upper=hi))
        cid += 1
    return datums, measure, cons


def test_random_reconcile_matches_brute_force_minimax():
    rng = random.Random(20260925)
    checked = 0
    zero_cases = 0
    for _ in range(200):
        datum, measure, constraints = random_instance(rng)
        readings = {p: rng.randint(-8, 12) for p in measure}
        req_obj = ReconcileRequest(
            datum_points=datum,
            measure_points=measure,
            constraints=constraints,
            readings=readings,
        )
        res = solve_reconcile(req_obj)
        solutions = list(brute_force_solutions(datum, measure, constraints))

        if not solutions:
            assert not res.feasible
            assert res.cycle_weight < 0
            continue

        # 暴力：在全部可行赋值上极小化“最大逐点偏差”
        e_star = min(
            max(abs(s[p] - readings[p]) for p in measure) for s in solutions
        )
        assert res.feasible
        assert res.max_abs_correction == e_star, (
            f"e mismatch: got {res.max_abs_correction}, bf {e_star}, "
            f"constraints={[(c.start, c.end, c.lower, c.upper) for c in constraints]}, "
            f"readings={readings}"
        )
        coords = res.coordinates
        # 返回的同一组坐标必须满足基准固定、全部约束、达到 e*
        assert all(coords[d] == 0 for d in datum)
        assert all(
            c.lower <= coords[c.end] - coords[c.start] <= c.upper
            for c in constraints
        )
        deviations = [abs(coords[p] - readings[p]) for p in measure]
        assert max(deviations) == e_star
        assert all(isinstance(coords[p], int) for p in measure)
        if e_star == 0:
            zero_cases += 1
        checked += 1

    assert checked > 120  # 大多数随机实例可行
    assert zero_cases > 0  # 应覆盖到无需校正（e=0）的实例


def test_random_reconcile_multiple_datums_match_brute_force():
    rng = random.Random(9981)
    feasible_checked = 0
    for _ in range(200):
        datum, measure, constraints = _multi_datum_instance(rng)
        readings = {p: rng.randint(-8, 8) for p in measure}
        req_obj = ReconcileRequest(
            datum_points=datum,
            measure_points=measure,
            constraints=constraints,
            readings=readings,
        )
        res = solve_reconcile(req_obj)
        solutions = list(brute_force_solutions(datum, measure, constraints))
        if not solutions:
            assert not res.feasible
            continue

        e_star = min(
            max(abs(s[p] - readings[p]) for p in measure) for s in solutions
        )
        assert res.feasible
        assert res.max_abs_correction == e_star, (
            f"got {res.max_abs_correction}, bf {e_star}, "
            f"cons={[(c.start, c.end, c.lower, c.upper) for c in constraints]}, "
            f"readings={readings}"
        )
        coords = res.coordinates
        assert all(coords[d] == 0 for d in datum)
        assert all(
            c.lower <= coords[c.end] - coords[c.start] <= c.upper
            for c in constraints
        )
        assert max(abs(coords[p] - readings[p]) for p in measure) == e_star
        feasible_checked += 1

    assert feasible_checked >= 60  # 充分覆盖多基准点可行实例（其余为原约束不可行实例）

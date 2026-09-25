"""随机差分测试：用独立的暴力可行性判定交叉验证 Bellman-Ford 结果。

暴力方法：对小规模问题枚举所有点坐标（受限范围内的整数微米），
检查是否存在一组赋值满足全部约束。坐标范围取各边权之和的上界，
保证若有解必有界内解。ranges 接口的紧界用同一枚举取真实最小/最大值比对。
"""

from __future__ import annotations

import itertools
import random

from app.schemas import ChainRequest, ConstraintIn, ReconcileRequest
from app.solver import ValidationError, solve, solve_ranges, solve_reconcile


def brute_force_solutions(datum, measure, constraints):
    """枚举全部可行整数赋值，yield 每个测点 -> 坐标的 dict（基准点固定 0）。"""
    nodes = list(dict.fromkeys([*datum, *measure]))
    datum_set = set(datum)
    # 界：所有 |边权| 之和足以覆盖任何简单路径约束（含紧界端点）
    bound = sum(max(abs(c.lower), abs(c.upper)) + 1 for c in constraints)
    ranges = [range(0, 1) if p in datum_set else range(-bound, bound + 1) for p in nodes]
    idx = {p: i for i, p in enumerate(nodes)}
    for vals in itertools.product(*ranges):
        if all(
            c.lower <= vals[idx[c.end]] - vals[idx[c.start]] <= c.upper
            for c in constraints
        ):
            yield {p: vals[idx[p]] for p in measure}


def brute_force_feasible(datum, measure, constraints) -> bool:
    return next(brute_force_solutions(datum, measure, constraints), None) is not None


def returned_coords_valid(datum, measure, constraints, coords) -> bool:
    if any(coords[d] != 0 for d in datum):
        return False
    return all(
        c.lower <= coords[c.end] - coords[c.start] <= c.upper for c in constraints
    )


def random_instance(rng: random.Random):
    """生成一个无向连通的随机尺寸链（与 80 例可行性测试同一分布）。"""
    nodes = [f"p{i}" for i in range(rng.randint(2, 3))]
    datum = [nodes[0]]
    measure = nodes[1:]
    shuffled = nodes[1:]
    rng.shuffle(shuffled)
    connected = [nodes[0]]
    constraints = []
    cid = 0
    for child in shuffled:
        parent = rng.choice(connected)
        lo, hi = sorted((rng.randint(-4, 4), rng.randint(-4, 4)))
        if rng.random() < 0.5:
            constraints.append(ConstraintIn(id=f"c{cid}", start=parent, end=child, lower=lo, upper=hi))
        else:  # 反向放置
            constraints.append(ConstraintIn(id=f"c{cid}", start=child, end=parent, lower=-hi, upper=-lo))
        connected.append(child)
        cid += 1
    for _ in range(rng.randint(0, 3)):
        a, b = rng.sample(nodes, 2)
        lo, hi = sorted((rng.randint(-4, 4), rng.randint(-4, 4)))
        constraints.append(ConstraintIn(id=f"c{cid}", start=a, end=b, lower=lo, upper=hi))
        cid += 1
    return datum, measure, constraints


def test_random_against_brute_force():
    rng = random.Random(20260924)
    checked = 0
    for _ in range(80):
        datum, measure, constraints = random_instance(rng)

        req_obj = ChainRequest(datum_points=datum, measure_points=measure, constraints=constraints)
        try:
            res = solve(req_obj)
        except ValidationError:  # 随机数据不应触发静态错误
            raise AssertionError(
                f"unexpected validation error for "
                f"{[(c.start, c.end, c.lower, c.upper) for c in constraints]}"
            )
        expected = brute_force_feasible(datum, measure, constraints)
        assert res.feasible == expected, (
            f"mismatch: bf={expected}, bf_cycle={res.cycle_edges}, "
            f"constraints={[(c.start, c.end, c.lower, c.upper) for c in constraints]}"
        )
        if res.feasible:
            assert returned_coords_valid(datum, measure, constraints, res.coordinates)
        else:
            assert res.cycle_weight < 0
            # 矛盾闭环里的编号必须来自真实约束
            assert all(e.cid in {c.id for c in constraints}
                       for e in res.cycle_edges if e.cid is not None)
        checked += 1
    assert checked == 80


def test_random_ranges_match_brute_force_extrema():
    rng = random.Random(540219)
    checked = 0
    for _ in range(150):
        datum, measure, constraints = random_instance(rng)
        req_obj = ChainRequest(datum_points=datum, measure_points=measure, constraints=constraints)
        res = solve_ranges(req_obj)

        solutions = list(brute_force_solutions(datum, measure, constraints))
        if not solutions:
            assert not res.feasible
            continue

        assert res.feasible
        assert list(res.ranges) == list(measure)  # 顺序与请求一致
        for p in measure:
            lo, hi = res.ranges[p]
            # 紧界：与暴力枚举的真实最小/最大值完全一致
            assert lo == min(s[p] for s in solutions), (
                f"{p} lower bound wrong for "
                f"{[(c.start, c.end, c.lower, c.upper) for c in constraints]}"
            )
            assert hi == max(s[p] for s in solutions), (
                f"{p} upper bound wrong for "
                f"{[(c.start, c.end, c.lower, c.upper) for c in constraints]}"
            )
            # 端点必须分别被某组全局可行赋值达到
            assert any(s[p] == lo for s in solutions)
            assert any(s[p] == hi for s in solutions)
            assert isinstance(lo, int) and isinstance(hi, int)
        checked += 1
    assert checked > 100  # 大多数随机实例可行，保证确实比对了紧界


def test_random_reconcile_matches_brute_force():
    """reconcile 的最小 e 与暴力枚举的真实最优值一致。

    暴力枚举的坐标界覆盖全部可行赋值（任何可行坐标的绝对值都不超过
    所有边权绝对值之和），故枚举上的最小最大偏差即全局最优 e。
    """
    rng = random.Random(770031)
    checked = 0
    for _ in range(80):
        datum, measure, constraints = random_instance(rng)
        readings = {p: rng.randint(-6, 6) for p in measure}
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

        assert res.feasible
        # 暴力最优 e：所有可行赋值中“最大单点偏差”的最小值
        expected_e = min(
            max(abs(s[p] - readings[p]) for p in measure) for s in solutions
        )
        assert res.e == expected_e, (
            f"e mismatch: got {res.e}, want {expected_e}, readings={readings}, "
            f"constraints={[(c.start, c.end, c.lower, c.upper) for c in constraints]}"
        )
        # 返回坐标全局可行（含基准点为 0），且自身就达到 e
        assert returned_coords_valid(datum, measure, constraints, res.coordinates)
        for p in measure:
            assert res.corrections[p] == res.coordinates[p] - readings[p]
            assert abs(res.corrections[p]) <= res.e
        assert max(abs(res.corrections[p]) for p in measure) == res.e
        checked += 1
    assert checked > 50  # 大多数随机实例可行，保证确实比对了最优 e

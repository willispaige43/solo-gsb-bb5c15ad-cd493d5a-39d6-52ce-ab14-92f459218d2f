"""核心求解器单元测试。"""

from __future__ import annotations

import pytest

from app.schemas import ChainRequest, ConstraintIn
from app.solver import ValidationError, solve, solve_ranges, validate_request


def C(cid: str, start: str, end: str, lower: int, upper: int) -> ConstraintIn:
    return ConstraintIn(id=cid, start=start, end=end, lower=lower, upper=upper)


def req(datum, measure, constraints) -> ChainRequest:
    return ChainRequest(
        datum_points=list(datum),
        measure_points=list(measure),
        constraints=list(constraints),
    )


# ---------- 可行 ----------

def test_simple_chain_feasible():
    r = req(["A"], ["B", "C"], [C("c1", "A", "B", 10, 12), C("c2", "B", "C", 5, 8)])
    res = solve(r)
    assert res.feasible
    coords = res.coordinates
    assert coords["A"] == 0
    assert coords["B"] == 12 and coords["C"] == 20  # 取最紧上界
    # 返回坐标必须满足所有约束
    for c in r.constraints:
        d = coords[c.end] - coords[c.start]
        assert c.lower <= d <= c.upper


def test_closed_loop_feasible_at_boundary():
    # A=0, A→B [9,10], B→C [9,10], A→C [18,20]：闭环恰好可行
    r = req(["A"], ["B", "C"], [C("c1", "A", "B", 9, 10), C("c2", "B", "C", 9, 10), C("c3", "A", "C", 18, 20)])
    res = solve(r)
    assert res.feasible
    assert res.coordinates == {"A": 0, "B": 10, "C": 20}


def test_negative_coordinates_allowed():
    # B→A 区间 [-12,-10]：0 - B ∈ [-12,-10] → B ∈ [10,12]（反向约束、正向坐标）
    r = req(["A"], ["B"], [C("c1", "B", "A", -12, -10)])
    res = solve(r)
    assert res.feasible
    assert 10 <= res.coordinates["B"] <= 12
    assert res.coordinates["B"] == 12  # 最大可行解


def test_negative_coordinate_values_allowed():
    # A→B 区间 [-12,-10]：B 落在负半轴
    r = req(["A"], ["B"], [C("c1", "A", "B", -12, -10)])
    res = solve(r)
    assert res.feasible and res.coordinates["B"] == -10


def test_exact_constraints_single_solution():
    r = req(["A"], ["B"], [C("c1", "A", "B", 5, 5)])
    res = solve(r)
    assert res.feasible and res.coordinates["B"] == 5


def test_multiple_datums_pinned_to_zero():
    r = req(
        ["D1", "D2"],
        ["M"],
        [C("c1", "D1", "M", 3, 7), C("c2", "M", "D2", -7, -3)],
    )
    res = solve(r)
    assert res.feasible
    assert res.coordinates["D1"] == 0 and res.coordinates["D2"] == 0
    assert res.coordinates["M"] == 7  # M ∈ [3,7]，取最大可行解


# ---------- 坐标紧界 solve_ranges ----------

def test_ranges_simple_chain():
    r = req(["A"], ["B", "C"], [C("c1", "A", "B", 10, 12), C("c2", "B", "C", 5, 8)])
    res = solve_ranges(r)
    assert res.feasible
    assert list(res.ranges) == ["B", "C"]  # 顺序沿用请求测点顺序
    assert res.ranges["B"] == (10, 12)
    assert res.ranges["C"] == (15, 20)


def test_ranges_negative_coordinate_values():
    r = req(["A"], ["B"], [C("c1", "A", "B", -12, -10)])
    res = solve_ranges(r)
    assert res.feasible
    assert res.ranges["B"] == (-12, -10)


def test_ranges_exact_constraint_collapses():
    r = req(["A"], ["B"], [C("c1", "A", "B", 5, 5)])
    res = solve_ranges(r)
    assert res.feasible
    assert res.ranges["B"] == (5, 5)


def test_ranges_cross_measure_constraint_tightens():
    # 若无 c3：B∈[0,10]、C∈[0,10]；c3 要求 C-B∈[5,5]，
    # 收紧为 B∈[0,5]、C∈[5,10]（跨测点约束必须纳入界推导）
    r = req(
        ["A"],
        ["B", "C"],
        [
            C("c1", "A", "B", 0, 10),
            C("c2", "A", "C", 0, 10),
            C("c3", "B", "C", 5, 5),
        ],
    )
    res = solve_ranges(r)
    assert res.feasible
    assert res.ranges["B"] == (0, 5)
    assert res.ranges["C"] == (5, 10)


def test_ranges_endpoints_individually_attainable():
    # c3 要求 C-B∈[5,5]：B 的最大端点与 C 的最小端点不同时达到，
    # 但各自存在全局可行赋值（B=5⇒C=10；C=5⇒B=0）
    r = req(
        ["A"],
        ["B", "C"],
        [
            C("c1", "A", "B", 0, 10),
            C("c2", "A", "C", 0, 10),
            C("c3", "B", "C", 5, 5),
        ],
    )
    res = solve_ranges(r)
    assert res.feasible
    blo, bhi = res.ranges["B"]
    clo, chi = res.ranges["C"]

    def feasible_at(coords: dict[str, int]) -> bool:
        coords = {**{d: 0 for d in r.datum_points}, **coords}
        return all(
            c.lower <= coords[c.end] - coords[c.start] <= c.upper
            for c in r.constraints
        )

    assert feasible_at({"B": blo, "C": clo})  # 最小赋值（lower 极值解）
    assert feasible_at({"B": bhi, "C": chi})  # 最大赋值（upper 极值解）
    # 不要求同一组坐标同时占住“B 最大且 C 最小”——本例下不可能
    assert not feasible_at({"B": bhi, "C": clo})


def test_ranges_multiple_datums_pin_relations():
    # 两个基准点互钉为 0：M 相对 D1 ∈ [3,7]，相对 D2 ∈ [-7,-3]，
    # 两侧约束同时成立 ⇒ M ∈ [3,7]
    r = req(
        ["D1", "D2"],
        ["M"],
        [C("c1", "D1", "M", 3, 7), C("c2", "M", "D2", -7, -3)],
    )
    res = solve_ranges(r)
    assert res.feasible
    assert res.ranges["M"] == (3, 7)


def test_ranges_negative_weight_semantics_preserved():
    # B→A ∈ [-12,-10]（负权边语义）：B ∈ [10,12]
    r = req(["A"], ["B"], [C("c1", "B", "A", -12, -10)])
    res = solve_ranges(r)
    assert res.feasible
    assert res.ranges["B"] == (10, 12)


def test_ranges_infeasible_same_report_as_solve():
    r = req(
        ["A"],
        ["B", "C"],
        [C("c1", "A", "B", 9, 10), C("c2", "B", "C", 9, 10), C("c3", "C", "A", -1, 0)],
    )
    res = solve_ranges(r)
    assert not res.feasible
    assert res.ranges is None
    assert res.cycle_weight < 0
    cids = {e.cid for e in res.cycle_edges if e.cid is not None}
    assert cids == {"c1", "c2", "c3"}


def test_ranges_datum_conflict_via_pin_edges():
    r = req(
        ["D1", "D2"],
        ["M"],
        [C("c1", "D1", "D2", 1, 2), C("c2", "D1", "M", 0, 5)],
    )
    res = solve_ranges(r)
    assert not res.feasible
    cids = [e.cid for e in res.cycle_edges if e.cid is not None]
    assert cids == ["c1"]
    assert any(e.bound == "datum_pin" for e in res.cycle_edges)


# ---------- 不可行 ----------

def test_closed_loop_infeasible_reports_ids():
    r = req(
        ["A"],
        ["B", "C"],
        [C("c1", "A", "B", 9, 10), C("c2", "B", "C", 9, 10), C("c3", "C", "A", -1, 0)],
    )
    res = solve(r)
    assert not res.feasible
    assert res.cycle_weight < 0
    cids = [e.cid for e in res.cycle_edges if e.cid is not None]
    assert set(cids) == {"c1", "c2", "c3"}
    # 闭环逐边首尾相接
    for i, e in enumerate(res.cycle_edges):
        assert e.to == res.cycle_edges[(i + 1) % len(res.cycle_edges)].fr


def test_point_constraint_contradicts_datum():
    # 基准点之间被强制产生非零关系
    r = req(["D1", "D2"], ["M"], [C("c1", "D1", "M", 3, 5), C("c2", "M", "D2", 3, 5)])
    res = solve(r)
    assert not res.feasible
    cids = [e.cid for e in res.cycle_edges if e.cid is not None]
    assert set(cids) == {"c1", "c2"}
    assert res.cycle_weight < 0


def test_zero_width_self_loop_reversed_only_when_lower_gt_upper():
    r = req(["A"], ["B"], [C("c1", "A", "B", 5, 5)])
    assert solve(r).feasible


def test_self_loop_contradiction_is_infeasible():
    # coord(A) - coord(A) ∈ [1,2]：直接矛盾的自环
    r = req(
        ["A"],
        ["B"],
        [C("s", "A", "A", 1, 2), C("c1", "A", "B", 1, 2)],
    )
    res = solve(r)
    assert not res.feasible
    cids = [e.cid for e in res.cycle_edges if e.cid is not None]
    assert cids == ["s"]


def test_datum_conflict_via_pin_edges():
    # 两个基准点必须同为 0，但 c1 强制 D2 - D1 ∈ [1,2]
    r = req(
        ["D1", "D2"],
        ["M"],
        [C("c1", "D1", "D2", 1, 2), C("c2", "D1", "M", 0, 5)],
    )
    res = solve(r)
    assert not res.feasible
    cids = [e.cid for e in res.cycle_edges if e.cid is not None]
    assert cids == ["c1"]
    assert any(e.bound == "datum_pin" for e in res.cycle_edges)
    assert res.cycle_weight == -1


# ---------- 静态校验 ----------

def codes(req_obj):
    with pytest.raises(ValidationError) as ei:
        validate_request(req_obj)
    return {e["code"] for e in ei.value.errors}


def test_unknown_point_start_and_end():
    r = req(["A"], ["B"], [C("c1", "A", "X", 1, 2), C("c2", "Y", "A", 1, 2)])
    errs = codes(r)
    assert errs == {"unknown_point"}


def test_reversed_interval():
    r = req(["A"], ["B"], [C("c1", "A", "B", 10, 9)])
    assert codes(r) == {"reversed_interval"}


def test_measure_not_connected_to_datum():
    # B-C 相连但整体未连到基准点 A
    r = req(["A"], ["B", "C"], [C("c1", "B", "C", 1, 2)])
    assert codes(r) == {"point_not_connected_to_datum"}


def test_connected_undirected_is_enough():
    # 约束方向背离 A，但无向意义下连通，仍可校核
    r = req(["A"], ["B"], [C("c1", "B", "A", -10, -8)])
    assert solve(r).feasible


def test_duplicates_and_role_conflict():
    r = ChainRequest(
        datum_points=["A", "A"],
        measure_points=["A", "B", "B"],
        constraints=[
            C("c1", "A", "B", 1, 2),
            C("c1", "A", "B", 1, 2),
        ],
    )
    assert codes(r) == {"duplicate_point", "point_role_conflict", "duplicate_constraint"}


def test_strips_whitespace():
    r = req([" A "], [" B "], [C(" c1 ", "A", "B", 1, 2)])
    res = solve(r)
    assert res.feasible and res.coordinates["A"] == 0 and res.coordinates["B"] == 2

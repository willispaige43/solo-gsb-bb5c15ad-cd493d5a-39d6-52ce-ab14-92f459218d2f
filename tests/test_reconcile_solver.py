"""读数校正（solve_reconcile）求解器单元测试。"""

from __future__ import annotations

import pydantic
import pytest

from app.schemas import ReconcileRequest
from app.solver import ValidationError, solve_reconcile
from tests.test_solver import C


def rreq(datum, measure, constraints, readings) -> ReconcileRequest:
    return ReconcileRequest(
        datum_points=list(datum),
        measure_points=list(measure),
        constraints=list(constraints),
        readings=dict(readings),
    )


def check_solution(r: ReconcileRequest, res, e: int) -> None:
    """断言返回坐标满足：基准固定、全部约束、|校正坐标−读数|≤e。"""
    assert res.feasible
    assert res.max_abs_correction == e
    coords = res.coordinates
    for d in r.datum_points:
        assert coords[d] == 0
    for c in r.constraints:
        assert c.lower <= coords[c.end] - coords[c.start] <= c.upper
    for p in r.measure_points:
        assert abs(coords[p] - r.readings[p]) <= e
    # e 最小：至少一个测点的偏差恰好顶到 e（否则 e-1 即可行）
    assert any(abs(coords[p] - r.readings[p]) == e for p in r.measure_points)


# ---------- 基本最优性 ----------

def test_zero_correction_when_readings_match_feasible_coordinates():
    r = rreq(
        ["A"], ["B", "C"],
        [C("c1", "A", "B", 10, 12), C("c2", "B", "C", 5, 8)],
        {"B": 12, "C": 20},
    )
    res = solve_reconcile(r)
    check_solution(r, res, 0)
    assert res.coordinates == {"A": 0, "B": 12, "C": 20}


def test_min_e_balances_two_readings():
    # B∈[10,12]，C=B+[5,8]；读数 B=12、C=15。
    # e=0 要求 B=12 ⇒ C∈[17,20]，不可能等于 15；
    # e=1：B=11 ⇒ C=16，|11-12|=|16-15|=1
    r = rreq(
        ["A"], ["B", "C"],
        [C("c1", "A", "B", 10, 12), C("c2", "B", "C", 5, 8)],
        {"B": 12, "C": 15},
    )
    res = solve_reconcile(r)
    check_solution(r, res, 1)


def test_single_point_interval_minimax():
    # B∈[10,12]，读数 25：最近可行坐标 12，e=13
    r = rreq(["A"], ["B"], [C("c1", "A", "B", 10, 12)], {"B": 25})
    res = solve_reconcile(r)
    check_solution(r, res, 13)
    assert res.coordinates["B"] == 12


def test_negative_readings_and_coordinates():
    # B∈[-12,-10]，读数 0：最近 -10，e=10
    r = rreq(["A"], ["B"], [C("c1", "A", "B", -12, -10)], {"B": 0})
    res = solve_reconcile(r)
    check_solution(r, res, 10)
    assert res.coordinates["B"] == -10


def test_cross_measure_constraint_forces_joint_compromise():
    # B∈[0,10]、C∈[0,10]、C-B=5；读数 B=10、C=5 恰好对立，
    # 单点可行域不能兼顾：B=5,C=10 ⇒ e=5（B=0,C=5 同样 e=5）
    r = rreq(
        ["A"], ["B", "C"],
        [
            C("c1", "A", "B", 0, 10),
            C("c2", "A", "C", 0, 10),
            C("c3", "B", "C", 5, 5),
        ],
        {"B": 10, "C": 5},
    )
    res = solve_reconcile(r)
    check_solution(r, res, 5)


def test_multiple_datums_pinned():
    r = rreq(
        ["D1", "D2"], ["M"],
        [C("c1", "D1", "M", 3, 7), C("c2", "M", "D2", -7, -3)],
        {"M": 0},
    )
    res = solve_reconcile(r)
    check_solution(r, res, 3)  # M∈[3,7]，读数 0，最近 3
    assert res.coordinates["D1"] == 0 and res.coordinates["D2"] == 0


# ---------- 原约束不可行：沿用矛盾闭环 ----------

def test_original_infeasibility_returns_cycle():
    r = rreq(
        ["A"], ["B", "C"],
        [
            C("c1", "A", "B", 9, 10),
            C("c2", "B", "C", 9, 10),
            C("c3", "C", "A", -1, 0),
        ],
        {"B": 9, "C": 18},
    )
    res = solve_reconcile(r)
    assert not res.feasible
    assert res.max_abs_correction is None
    assert res.cycle_weight < 0
    cids = {e.cid for e in res.cycle_edges if e.cid is not None}
    assert cids == {"c1", "c2", "c3"}


def test_datum_conflict_reports_cycle_without_reading_edges():
    # 基准点矛盾与读数无关：读数边不应出现在闭环报告里
    r = rreq(
        ["D1", "D2"], ["M"],
        [C("c1", "D1", "D2", 1, 2), C("c2", "D1", "M", 0, 5)],
        {"M": 3},
    )
    res = solve_reconcile(r)
    assert not res.feasible
    cids = [e.cid for e in res.cycle_edges if e.cid is not None]
    assert cids == ["c1"]
    assert any(e.bound == "datum_pin" for e in res.cycle_edges)


# ---------- readings 静态校验 ----------

def reading_codes(r: ReconcileRequest):
    with pytest.raises(ValidationError) as ei:
        solve_reconcile(r)
    return {e["code"] for e in ei.value.errors}


def test_reading_missing():
    r = rreq(
        ["A"], ["B", "C"],
        [
            C("c1", "A", "B", 1, 2),
            C("c2", "B", "C", 1, 2),
        ],
        {"B": 1},
    )
    assert reading_codes(r) == {"reading_missing"}


def test_reading_extra_unknown_and_datum():
    r = rreq(
        ["A"], ["B"],
        [C("c1", "A", "B", 1, 2)],
        {"B": 1, "A": 0, "GHOST": 3},
    )
    errs = solve_errs(r)
    # 基准点和额外测点分别上报
    assert [e["code"] for e in errs] == ["reading_extra", "reading_extra"]
    assert {tuple(e["location"]) for e in errs} == {
        ("readings", "A"),
        ("readings", "GHOST"),
    }
    # 缺漏与额外同时存在时各自上报
    r2 = rreq(
        ["A"], ["B", "C"],
        [C("c1", "A", "B", 1, 2), C("c2", "B", "C", 1, 2)],
        {"B": 1, "A": 0},
    )
    codes = reading_codes(r2)
    assert codes == {"reading_missing", "reading_extra"}


def solve_errs(r):
    with pytest.raises(ValidationError) as ei:
        solve_reconcile(r)
    return ei.value.errors


def test_base_validation_runs_first():
    # 原尺寸链校验错误（未连通）同样拦截，不被 readings 校验掩盖
    r = rreq(
        ["A"], ["B", "C"],
        [C("c1", "A", "B", 1, 2)],
        {"B": 1, "C": 2},
    )
    assert reading_codes(r) == {"point_not_connected_to_datum"}


def test_reading_keys_are_stripped():
    r = rreq(
        ["A"], ["B", "C"],
        [C("c1", "A", "B", 10, 12), C("c2", "B", "C", 5, 8)],
        {" B ": 12, " C ": 20},
    )
    res = solve_reconcile(r)
    assert res.feasible and res.max_abs_correction == 0


def test_reading_keys_collide_after_strip_422():
    # 由 pydantic 在入模阶段拦截（与点编号去空白策略一致）
    with pytest.raises(pydantic.ValidationError):
        rreq(
            ["A"], ["B"],
            [C("c1", "A", "B", 1, 2)],
            {"B": 1, " B ": 2},
        )

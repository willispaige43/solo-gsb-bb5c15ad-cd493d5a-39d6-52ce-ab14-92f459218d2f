"""FastAPI 入口：装配尺寸链可行性校核。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .schemas import (
    ChainRequest,
    CycleEdge,
    FeasibleResponse,
    InfeasibleResponse,
    PointCorrection,
    PointRange,
    RangesResponse,
    ReconcileRequest,
    ReconcileResponse,
    ValidationErrorResponse,
)
from .solver import (
    Edge,
    SolveResult,
    ValidationError,
    solve,
    solve_ranges,
    solve_reconcile,
)

logger = logging.getLogger("dimchain")

app = FastAPI(
    title="装配尺寸链可行性校核 API",
    description=(
        "客户端提交基准点、测点及有向尺寸约束（终点减起点的整数微米差上下限），"
        "服务端将界限转为差分约束并判定公差能否同时成立；可行时返回以基准点为零"
        "的各点坐标，不可行时返回构成矛盾闭环的原始约束编号。"
    ),
    version=__version__,
)


@app.exception_handler(ValidationError)
async def handle_domain_validation(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors},
    )


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    logger.exception("求解过程中发生未预期错误: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": [
                {
                    "code": "internal_error",
                    "message": "服务端内部错误，请检查请求或联系管理员",
                    "location": [],
                }
            ]
        },
    )


def _to_cycle_edge(e: Edge) -> CycleEdge:
    return CycleEdge(
        constraint_id=e.cid,
        start=e.start,
        end=e.end,
        bound=e.bound,  # type: ignore[arg-type]
        weight=e.weight,
        edge_from=e.fr,
        edge_to=e.to,
    )


def _build_response(result: SolveResult):
    if result.feasible:
        return FeasibleResponse(coordinates=result.coordinates)
    return _build_infeasible(result)


def _build_infeasible(result: SolveResult) -> InfeasibleResponse:
    cycle_edges = [_to_cycle_edge(e) for e in result.cycle_edges]
    # 构成矛盾闭环的原始约束编号：按环上首次出现顺序去重（pin 边无原始约束）
    cids: list[str] = []
    for e in result.cycle_edges:
        if e.cid is not None and e.cid not in cids:
            cids.append(e.cid)

    return InfeasibleResponse(
        conflicting_constraint_ids=cids,
        cycle_edges=cycle_edges,
        cycle_weight=result.cycle_weight,
    )


@app.post(
    "/api/v1/chains/check",
    response_model=FeasibleResponse | InfeasibleResponse,
    summary="校核尺寸链可行性",
    responses={422: {"model": ValidationErrorResponse}},
    tags=["chains"],
)
def check_chain(req: ChainRequest):
    result = solve(req)
    return _build_response(result)


@app.post(
    "/api/v1/chains/ranges",
    response_model=RangesResponse | InfeasibleResponse,
    summary="校核可行性并返回各测点坐标紧界",
    responses={422: {"model": ValidationErrorResponse}},
    tags=["chains"],
)
def ranges_chain(req: ChainRequest):
    result = solve_ranges(req)
    if not result.feasible:
        return _build_infeasible(result)

    # 按请求测点顺序列出编号与上下界
    items = [
        PointRange(id=p, minimum=lo, maximum=hi)
        for p, (lo, hi) in result.ranges.items()
    ]
    return RangesResponse(ranges=items)


@app.post(
    "/api/v1/chains/reconcile",
    response_model=ReconcileResponse | InfeasibleResponse,
    summary="依据实测读数求最小统一校正量",
    responses={422: {"model": ValidationErrorResponse}},
    tags=["chains"],
)
def reconcile_chain(req: ReconcileRequest):
    result = solve_reconcile(req)
    if not result.feasible:
        return _build_infeasible(result)

    # 同一组可行校正坐标 + 逐点校正量，按请求测点顺序列出
    corrections = [
        PointCorrection(
            id=p,
            reading=req.readings[p],
            corrected_coordinate=result.coordinates[p],
            correction=result.coordinates[p] - req.readings[p],
        )
        for p in req.measure_points
    ]
    return ReconcileResponse(
        max_abs_correction=result.max_abs_correction,  # type: ignore[arg-type]
        coordinates=result.coordinates,
        corrections=corrections,
    )


@app.get("/healthz", tags=["meta"], summary="健康检查")
def healthz() -> dict:
    return {"status": "ok", "version": __version__}

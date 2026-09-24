"""请求 / 响应数据模型。

坐标与公差的单位均为整数微米（µm）。每条约束表示
``coord(end) - coord(start) ∈ [lower, upper]``。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

# 允许的点编号：非空字符串（允许前后空白，入库时自动去除）
PointId = str


def _strip(name: str, value: object) -> str:
    if not isinstance(value, str):  # pragma: no cover - pydantic 已保证类型
        raise ValueError(f"{name} 必须是字符串")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} 不能为空字符串")
    return text


class ConstraintIn(BaseModel):
    """一条有向尺寸约束：coord(end) - coord(start) ∈ [lower, upper]。"""

    model_config = ConfigDict(extra="forbid")

    id: PointId = Field(..., description="约束的原始编号，用于矛盾闭环溯源")
    start: PointId = Field(..., description="起点编号")
    end: PointId = Field(..., description="终点编号")
    lower: StrictInt = Field(..., description="终点减起点公差下限（整数微米，含端点）")
    upper: StrictInt = Field(..., description="终点减起点公差上限（整数微米，含端点）")

    @field_validator("id", "start", "end")
    @classmethod
    def _strip_fields(cls, v: str) -> str:
        return _strip("点编号 / 约束编号", v)


class ChainRequest(BaseModel):
    """校核请求。

    基准点坐标固定为 0；测点必须（在忽略约束方向的无向意义下）
    经约束链连接到至少一个基准点。
    """

    model_config = ConfigDict(extra="forbid")

    datum_points: list[PointId] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="基准点编号列表，坐标恒为 0",
    )
    measure_points: list[PointId] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="测点编号列表",
    )
    constraints: list[ConstraintIn] = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="有向尺寸约束列表（至少一条）",
    )

    @field_validator("datum_points", "measure_points")
    @classmethod
    def _strip_lists(cls, values: list[str]) -> list[str]:
        return [_strip("点编号", v) for v in values]


class CycleEdge(BaseModel):
    """矛盾闭环中一条边的溯源信息。

    ``bound="datum_pin"`` 表示基准点同为零点的固定边，此时
    ``constraint_id/start/end`` 为 null。
    """

    model_config = ConfigDict(extra="forbid")

    constraint_id: PointId | None = Field(
        None, description="原始约束编号；基准点固定边为 null"
    )
    start: PointId | None = Field(None, description="该约束的起点")
    end: PointId | None = Field(None, description="该约束的终点")
    bound: Literal["upper", "lower", "datum_pin"] = Field(
        ..., description="闭环中取到的是上限边(upper)、下限反向边(lower)还是基准固定边(datum_pin)"
    )
    weight: StrictInt = Field(..., description="该边在差分图中的权值（微米）")
    edge_from: PointId = Field(..., description="差分图边的起点")
    edge_to: PointId = Field(..., description="差分图边的终点")


class FeasibleResponse(BaseModel):
    status: Literal["feasible"] = "feasible"
    unit: Literal["um"] = "um"
    coordinates: dict[PointId, StrictInt] = Field(
        ..., description="以基准点为 0 的各点整数坐标（微米）"
    )


class PointRange(BaseModel):
    """单个测点在所有约束同时成立时可取的整数微米坐标闭区间。"""

    model_config = ConfigDict(extra="forbid")

    id: PointId = Field(..., description="测点编号（与请求 measure_points 一致）")
    minimum: StrictInt = Field(
        ..., description="可达到的最小整数坐标（微米，含端点），可为负数"
    )
    maximum: StrictInt = Field(
        ..., description="可达到的最大整数坐标（微米，含端点），可为负数"
    )


class RangesResponse(BaseModel):
    status: Literal["feasible"] = "feasible"
    unit: Literal["um"] = "um"
    ranges: list[PointRange] = Field(
        ...,
        description=(
            "各测点的全局紧界，按请求 measure_points 的顺序列出；"
            "每个端点都由某组全局可行坐标达到，但不同测点的端点不要求由同一组坐标同时达到"
        ),
    )


class InfeasibleResponse(BaseModel):
    status: Literal["infeasible"] = "infeasible"
    unit: Literal["um"] = "um"
    conflicting_constraint_ids: list[PointId] = Field(
        ..., description="构成矛盾闭环的原始约束编号（按闭环顺序，去重）"
    )
    cycle_edges: list[CycleEdge] = Field(
        ..., description="矛盾闭环的逐边明细（按有向闭环顺序）"
    )
    cycle_weight: StrictInt = Field(
        ..., description="闭环总权值，严格为负即表示公差无法同时成立"
    )


class ValidationErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误说明（中文）")
    location: list[str | int] = Field(
        default_factory=list, description="错误在请求中的位置，如 [\"constraints\", 0, \"lower\"]"
    )
    constraint_id: PointId | None = Field(
        None, description="若错误与某条约束相关，给出其原始编号"
    )


class ValidationErrorResponse(BaseModel):
    detail: list[ValidationErrorItem]

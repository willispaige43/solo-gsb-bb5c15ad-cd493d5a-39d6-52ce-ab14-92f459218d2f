"""API 层测试（基于 FastAPI TestClient）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_feasible_endpoint():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 10, "upper": 12},
            {"id": "c2", "start": "B", "end": "C", "lower": 5, "upper": 8},
        ],
    }
    r = client.post("/api/v1/chains/check", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "feasible"
    assert body["unit"] == "um"
    assert body["coordinates"] == {"A": 0, "B": 12, "C": 20}


def test_infeasible_endpoint():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 9, "upper": 10},
            {"id": "c2", "start": "B", "end": "C", "lower": 9, "upper": 10},
            {"id": "c3", "start": "C", "end": "A", "lower": -1, "upper": 0},
        ],
    }
    r = client.post("/api/v1/chains/check", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "infeasible"
    assert set(body["conflicting_constraint_ids"]) == {"c1", "c2", "c3"}
    assert body["cycle_weight"] < 0
    assert body["cycle_edges"], "应给出逐边明细"
    assert all(e["weight"] == int(e["weight"]) for e in body["cycle_edges"])


def test_unknown_point_422():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "X", "lower": 1, "upper": 2}
        ],
    }
    r = client.post("/api/v1/chains/check", json=payload)
    assert r.status_code == 422
    codes = {e["code"] for e in r.json()["detail"]}
    assert codes == {"unknown_point"}


def test_reversed_interval_422():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 9, "upper": 1}
        ],
    }
    r = client.post("/api/v1/chains/check", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "reversed_interval"


def test_disconnected_measure_point_422():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 1, "upper": 2}
        ],
    }
    r = client.post("/api/v1/chains/check", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "point_not_connected_to_datum"


def test_pydantic_type_errors_422():
    # lower 给了浮点数 / 缺少字段：走 pydantic 的标准 422
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 1.5, "upper": 2}
        ],
    }
    r = client.post("/api/v1/chains/check", json=payload)
    assert r.status_code == 422
    assert "detail" in r.json()


def test_openapi_available():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"]


def test_ranges_endpoint_feasible():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 10, "upper": 12},
            {"id": "c2", "start": "B", "end": "C", "lower": 5, "upper": 8},
        ],
    }
    r = client.post("/api/v1/chains/ranges", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "feasible"
    assert body["unit"] == "um"
    # 按请求测点顺序列出
    assert body["ranges"] == [
        {"id": "B", "minimum": 10, "maximum": 12},
        {"id": "C", "minimum": 15, "maximum": 20},
    ]


def test_ranges_endpoint_cross_measure_tightening():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 0, "upper": 10},
            {"id": "c2", "start": "A", "end": "C", "lower": 0, "upper": 10},
            {"id": "c3", "start": "B", "end": "C", "lower": 5, "upper": 5},
        ],
    }
    r = client.post("/api/v1/chains/ranges", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["ranges"] == [
        {"id": "B", "minimum": 0, "maximum": 5},
        {"id": "C", "minimum": 5, "maximum": 10},
    ]


def test_ranges_endpoint_negative_bounds():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": -12, "upper": -10}
        ],
    }
    r = client.post("/api/v1/chains/ranges", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["ranges"] == [{"id": "B", "minimum": -12, "maximum": -10}]


def test_ranges_endpoint_infeasible_uses_same_response():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 9, "upper": 10},
            {"id": "c2", "start": "B", "end": "C", "lower": 9, "upper": 10},
            {"id": "c3", "start": "C", "end": "A", "lower": -1, "upper": 0},
        ],
    }
    r = client.post("/api/v1/chains/ranges", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "infeasible"
    assert set(body["conflicting_constraint_ids"]) == {"c1", "c2", "c3"}
    assert body["cycle_weight"] < 0


def test_ranges_endpoint_static_validation_422():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 1, "upper": 2}
        ],
    }
    r = client.post("/api/v1/chains/ranges", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "point_not_connected_to_datum"


def test_ranges_endpoint_openapi_documented():
    schema = client.get("/openapi.json").json()
    assert "/api/v1/chains/ranges" in schema["paths"]


# ---------- /api/v1/chains/reconcile ----------


def _reconcile_payload(**overrides):
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 10, "upper": 12},
            {"id": "c2", "start": "B", "end": "C", "lower": 5, "upper": 8},
        ],
        "readings": {"B": 10, "C": 19},
    }
    payload.update(overrides)
    return payload


def test_reconcile_endpoint_feasible():
    r = client.post("/api/v1/chains/reconcile", json=_reconcile_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "feasible"
    assert body["unit"] == "um"
    assert body["e"] == 1
    assert body["coordinates"] == {"A": 0, "B": 11, "C": 19}
    assert body["corrections"] == {"B": 1, "C": 0}
    # 校正量 = 校正坐标 − 读数，且最大值恰为 e
    for p, reading in {"B": 10, "C": 19}.items():
        assert body["corrections"][p] == body["coordinates"][p] - reading
    assert max(abs(v) for v in body["corrections"].values()) == body["e"]


def test_reconcile_endpoint_zero_correction():
    r = client.post(
        "/api/v1/chains/reconcile",
        json=_reconcile_payload(readings={"B": 11, "C": 17}),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["e"] == 0
    assert body["corrections"] == {"B": 0, "C": 0}
    assert body["coordinates"] == {"A": 0, "B": 11, "C": 17}


def test_reconcile_endpoint_infeasible_same_cycle():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 9, "upper": 10},
            {"id": "c2", "start": "B", "end": "C", "lower": 9, "upper": 10},
            {"id": "c3", "start": "C", "end": "A", "lower": -1, "upper": 0},
        ],
        "readings": {"B": 9, "C": 18},
    }
    r = client.post("/api/v1/chains/reconcile", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "infeasible"
    assert set(body["conflicting_constraint_ids"]) == {"c1", "c2", "c3"}
    assert body["cycle_weight"] < 0
    assert body["cycle_edges"]


def test_reconcile_missing_reading_422():
    r = client.post(
        "/api/v1/chains/reconcile", json=_reconcile_payload(readings={"B": 10})
    )
    assert r.status_code == 422
    errs = r.json()["detail"]
    assert [e["code"] for e in errs] == ["missing_reading"]
    assert errs[0]["location"] == ["readings", "C"]


def test_reconcile_extra_reading_422():
    r = client.post(
        "/api/v1/chains/reconcile",
        json=_reconcile_payload(readings={"B": 10, "C": 19, "A": 0, "X": 1}),
    )
    assert r.status_code == 422
    errs = r.json()["detail"]
    assert {e["code"] for e in errs} == {"extra_reading"}
    assert [e["location"] for e in errs] == [["readings", "A"], ["readings", "X"]]


def test_reconcile_missing_and_extra_reported_together():
    r = client.post(
        "/api/v1/chains/reconcile", json=_reconcile_payload(readings={"B": 10, "X": 1})
    )
    assert r.status_code == 422
    assert {e["code"] for e in r.json()["detail"]} == {"missing_reading", "extra_reading"}


def test_reconcile_non_integer_reading_422():
    r = client.post(
        "/api/v1/chains/reconcile",
        json=_reconcile_payload(readings={"B": 10.5, "C": 19}),
    )
    assert r.status_code == 422
    assert "detail" in r.json()


def test_reconcile_base_validation_still_applies_422():
    # 尺寸链部分的静态校验与 /check 一致（未知点）
    payload = _reconcile_payload(
        constraints=[{"id": "c1", "start": "A", "end": "GHOST", "lower": 1, "upper": 2}]
    )
    r = client.post("/api/v1/chains/reconcile", json=payload)
    assert r.status_code == 422
    codes = {e["code"] for e in r.json()["detail"]}
    assert "unknown_point" in codes


def test_reconcile_endpoint_openapi_documented():
    schema = client.get("/openapi.json").json()
    assert "/api/v1/chains/reconcile" in schema["paths"]

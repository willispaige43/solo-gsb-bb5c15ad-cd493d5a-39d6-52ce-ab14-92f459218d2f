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

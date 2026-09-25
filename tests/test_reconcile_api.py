"""POST /api/v1/chains/reconcile 的 API 层测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def base_payload(readings):
    return {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 10, "upper": 12},
            {"id": "c2", "start": "B", "end": "C", "lower": 5, "upper": 8},
        ],
        "readings": readings,
    }


def test_reconcile_zero_correction():
    r = client.post("/api/v1/chains/reconcile", json=base_payload({"B": 12, "C": 20}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "feasible"
    assert body["unit"] == "um"
    assert body["max_abs_correction"] == 0
    assert body["coordinates"] == {"A": 0, "B": 12, "C": 20}
    assert body["corrections"] == [
        {"id": "B", "reading": 12, "corrected_coordinate": 12, "correction": 0},
        {"id": "C", "reading": 20, "corrected_coordinate": 20, "correction": 0},
    ]


def test_reconcile_min_e_and_same_coordinate_set():
    r = client.post("/api/v1/chains/reconcile", json=base_payload({"B": 12, "C": 15}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_abs_correction"] == 1
    coords = body["coordinates"]
    # 同一组坐标同时满足约束、基准固定与逐点 |校正坐标−读数| ≤ e
    assert coords["A"] == 0
    assert 10 <= coords["B"] - coords["A"] <= 12
    assert 5 <= coords["C"] - coords["B"] <= 8
    for item in body["corrections"]:
        p = item["id"]
        assert item["correction"] == coords[p] - item["reading"]
        assert abs(item["correction"]) <= body["max_abs_correction"]
    # corrections 按 measure_points 顺序
    assert [i["id"] for i in body["corrections"]] == ["B", "C"]


def test_reconcile_readings_order_does_not_matter():
    p1 = base_payload({"B": 12, "C": 15})
    p2 = base_payload({"C": 15, "B": 12})
    b1 = client.post("/api/v1/chains/reconcile", json=p1).json()
    b2 = client.post("/api/v1/chains/reconcile", json=p2).json()
    assert b1["max_abs_correction"] == b2["max_abs_correction"] == 1
    assert b1["coordinates"] == b2["coordinates"]
    assert [i["id"] for i in b2["corrections"]] == ["B", "C"]


def test_reconcile_missing_reading_422():
    r = client.post("/api/v1/chains/reconcile", json=base_payload({"B": 12}))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail[0]["code"] == "reading_missing"
    assert detail[0]["location"] == ["readings", "C"]


def test_reconcile_extra_reading_422():
    # 额外测点
    r = client.post(
        "/api/v1/chains/reconcile", json=base_payload({"B": 12, "C": 20, "Z": 1})
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "reading_extra"

    # 基准点也不能出现在 readings
    r = client.post(
        "/api/v1/chains/reconcile",
        json=base_payload({"A": 0, "B": 12, "C": 20}),
    )
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "reading_extra"


def test_reconcile_missing_and_extra_reported_together():
    r = client.post(
        "/api/v1/chains/reconcile", json=base_payload({"B": 12, "Z": 1})
    )
    assert r.status_code == 422
    codes = {e["code"] for e in r.json()["detail"]}
    assert codes == {"reading_missing", "reading_extra"}


def test_reconcile_non_integer_reading_422():
    # 浮点数与布尔值按现有严格整数风格走 pydantic 422
    r = client.post(
        "/api/v1/chains/reconcile", json=base_payload({"B": 12.5, "C": 20})
    )
    assert r.status_code == 422
    assert "detail" in r.json()

    r = client.post(
        "/api/v1/chains/reconcile", json=base_payload({"B": True, "C": 20})
    )
    assert r.status_code == 422


def test_reconcile_readings_field_required_422():
    payload = base_payload({"B": 1, "C": 2})
    del payload["readings"]
    r = client.post("/api/v1/chains/reconcile", json=payload)
    assert r.status_code == 422


def test_reconcile_unknown_field_422():
    payload = base_payload({"B": 12, "C": 20})
    payload["bogus"] = 1
    r = client.post("/api/v1/chains/reconcile", json=payload)
    assert r.status_code == 422


def test_reconcile_check_endpoint_rejects_readings():
    # /check 沿用原请求（extra=forbid）：夹带 readings 应 422
    payload = base_payload({"B": 12, "C": 20})
    r = client.post("/api/v1/chains/check", json=payload)
    assert r.status_code == 422


def test_reconcile_infeasible_uses_existing_response():
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


def test_reconcile_static_validation_422():
    payload = {
        "datum_points": ["A"],
        "measure_points": ["B", "C"],
        "constraints": [
            {"id": "c1", "start": "A", "end": "B", "lower": 1, "upper": 2}
        ],
        "readings": {"B": 1, "C": 2},
    }
    r = client.post("/api/v1/chains/reconcile", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "point_not_connected_to_datum"


def test_reconcile_openapi_documented():
    schema = client.get("/openapi.json").json()
    assert "/api/v1/chains/reconcile" in schema["paths"]
    assert "ReconcileResponse" in schema["components"]["schemas"]

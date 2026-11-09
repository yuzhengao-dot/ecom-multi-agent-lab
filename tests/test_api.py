from __future__ import annotations


def test_healthz(api_client):
    response = api_client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["mode"] == "mock"


def test_create_campaign(api_client):
    payload = {"user_id": "U1001", "scene": "618大促", "budget": 500, "channel": "app_push"}
    response = api_client.post("/v1/campaigns", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trace_id"]
    assert body["variant"] in {"A", "B"}
    assert body["plan"]["compliance_status"] == "approved"
    assert body["plan"]["recommended_skus"]
    assert body["agent_results"]
    assert body["trace"]["spans"]


def test_trace_can_be_retrieved(api_client):
    created = api_client.post("/v1/campaigns", json={"user_id": "U1002"}).json()
    response = api_client.get(f"/v1/traces/{created['trace_id']}")
    assert response.status_code == 200
    assert response.json()["trace_id"] == created["trace_id"]


def test_missing_trace_returns_404(api_client):
    assert api_client.get("/v1/traces/does-not-exist").status_code == 404


def test_metrics_endpoint(api_client):
    api_client.post("/v1/campaigns", json={"user_id": "U1001"})
    text = api_client.get("/metrics").text
    assert "ecom_campaign_runs_total" in text
    assert "ecom_agent_runs_total" in text


def test_assignment_and_catalog(api_client):
    assert api_client.get("/v1/experiments/assign", params={"user_id": "U1001"}).json()["variant"] in {"A", "B"}
    users = api_client.get("/v1/catalog/users").json()
    assert {"U1001", "U1002", "U1003", "U1004"} <= {u["user_id"] for u in users}


def test_unknown_user_is_cold_started(api_client):
    response = api_client.post("/v1/campaigns", json={"user_id": "NO-SUCH-USER"})
    assert response.status_code == 200
    assert response.json()["plan"]["segment"] == "新客"


def test_invalid_channel_is_rejected(api_client):
    response = api_client.post("/v1/campaigns", json={"user_id": "U1001", "channel": "carrier_pigeon"})
    assert response.status_code == 422

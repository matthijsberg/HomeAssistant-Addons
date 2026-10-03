"""Integration tests for Laya FastAPI endpoints using TestClient."""

import pytest
from fastapi.testclient import TestClient
from main import app, config, engine

client = TestClient(app)

AUTH_HEADER = {"Authorization": f"Bearer {config.api_key}"}


@pytest.fixture(autouse=True)
def ensure_engine_ready():
    """Ensure engine is initialized before running API tests."""
    if not engine.ready:
        engine.initialize()


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["ready"] is True
    assert "english" in data["loaded"]
    assert "multilingual" in data["loaded"]
    assert data["device"] == "cpu"
    assert "pinned" in data["revisions"]


def test_ingress_gui_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "HA System 1" in response.text
    assert "Playground" in response.text


def test_ingress_auth_bypass():
    # Requests with X-Ingress-Path header do not need Bearer Authorization
    res = client.post(
        "/v1/route",
        json={"prompt": "test prompt without token"},
        headers={"X-Ingress-Path": "/api/hassio_ingress/test-token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "family" in data


def test_auth_rejection():
    # Missing header
    res = client.post("/v1/route", json={"prompt": "test"})
    assert res.status_code == 401

    # Invalid token
    res = client.post(
        "/v1/route",
        json={"prompt": "test"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert res.status_code == 401


def test_route_endpoint_happy_path():
    payload = {
        "prompt": "Schrijf een python script om een CSV bestand in te lezen",
        "recent_turns": [
            {"role": "user", "content": "Wat is Python?"},
            {"role": "assistant", "content": "Python is een programmeertaal."},
        ],
        "question_set": "hermes-v1",
        "session_id": "test_session_123",
    }
    response = client.post("/v1/route", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()

    assert data["family"] in ("code", "general", "quick", "deep")
    assert data["effort"] in ("light", "normal", "deep")
    assert "family" in data["confidence"]
    assert "effort" in data["confidence"]
    assert isinstance(data["confidence"]["family"], float)
    assert isinstance(data["confidence"]["effort"], float)
    assert "checkpoint" in data
    assert data["latency_ms"] >= 0.0
    assert data["question_set"] == "hermes-v1"
    assert "provider" in data
    assert "model" in data
    assert isinstance(data["model"], str)


def test_get_models_endpoint():
    response = client.get("/v1/models", headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert "provider" in data
    assert "models" in data
    assert "quick" in data["models"]
    assert "general" in data["models"]
    assert "code" in data["models"]
    assert "deep" in data["models"]
    assert "supported_providers" in data
    assert "architecture_limitation_note" in data


def test_route_context_truncation():
    # Pass 5 turns to ensure only the last 2 are evaluated in context
    payload = {
        "prompt": "ja, doe maar",
        "recent_turns": [
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "turn 2"},
            {"role": "user", "content": "turn 3"},
            {"role": "assistant", "content": "turn 4"},
            {"role": "user", "content": "turn 5: schrijf de code"},
        ],
    }
    response = client.post("/v1/route", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert "family" in data


def test_route_unknown_question_set():
    payload = {
        "prompt": "Hello",
        "question_set": "non-existent-set",
    }
    response = client.post("/v1/route", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 404


def test_route_empty_prompt():
    payload = {"prompt": ""}
    response = client.post("/v1/route", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 422


def test_get_question_sets():
    response = client.get("/v1/question-sets", headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert "question_sets" in data
    assert "hermes-v1" in data["question_sets"]
    qset = data["question_sets"]["hermes-v1"]
    assert "questions" in qset
    assert "task_family" in qset["questions"]
    assert "effort" in qset["questions"]


def test_systemone_passthrough():
    payload = {
        "state": {"request": "Hello world"},
        "questions": {
            "department": {
                "type": "choice",
                "criteria": {"support": "help", "billing": "money"},
            }
        },
    }
    response = client.post("/v1/systemone", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert "answers" in data
    assert "routing" in data


def test_domotica_route_endpoint():
    payload = {"prompt": "doe de lampen in de serre uit", "execute": False}
    response = client.post("/v1/domotica/route", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert data["is_domotica"] is True
    assert data["domain"] == "light"
    assert data["service"] == "turn_off"
    assert data["fast_path"] is True
    assert "openai_tool_call" in data


def test_domotica_sync_endpoint():
    response = client.post("/v1/domotica/sync", headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert "synced" in data
    assert "entity_count" in data


def test_chat_completions_tool_call():
    payload = {
        "model": "laya-v2",
        "messages": [{"role": "user", "content": "doe de lampen in de woonkamer uit"}],
        "tools": [{"type": "function", "function": {"name": "HassTurnOff"}}],
        "stream": False,
    }
    response = client.post("/v1/chat/completions", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0
    msg = data["choices"][0]["message"]
    assert "tool_calls" in msg
    assert msg["tool_calls"][0]["function"]["name"] in ("HassTurnOff", "HassTurnOn")


def test_chat_completions_streaming():
    payload = {
        "model": "laya-v2",
        "messages": [{"role": "user", "content": "zet het licht aan"}],
        "stream": True,
    }
    response = client.post("/v1/chat/completions", json=payload, headers=AUTH_HEADER)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert "data: " in response.text
    assert "[DONE]" in response.text

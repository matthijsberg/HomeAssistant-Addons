"""Unit tests for Laya decision engine logic, formatting, and question sets."""

from config import AppConfig
from engine import LayaRouterEngine
from question_sets import get_question_set


def test_format_state_empty_context():
    state = LayaRouterEngine.format_state("Test prompt", [])
    assert state == {"request": "Test prompt"}


def test_format_state_with_dict_context():
    turns = [
        {"role": "user", "content": "First turn"},
        {"role": "assistant", "content": "Second turn"},
    ]
    state = LayaRouterEngine.format_state("Third prompt", turns)
    assert state["request"] == "Third prompt"
    assert "user: First turn" in state["context"]
    assert "assistant: Second turn" in state["context"]


def test_format_state_truncates_to_last_two():
    turns = [
        {"role": "user", "content": "Turn 1"},
        {"role": "assistant", "content": "Turn 2"},
        {"role": "user", "content": "Turn 3"},
        {"role": "assistant", "content": "Turn 4"},
    ]
    state = LayaRouterEngine.format_state("Turn 5", turns)
    assert "Turn 1" not in state["context"]
    assert "Turn 2" not in state["context"]
    assert "Turn 3" in state["context"]
    assert "Turn 4" in state["context"]


def test_hermes_v1_wording_rules():
    qset = get_question_set("hermes-v1")
    questions = qset["questions"]

    # PRD rule: choice questions only, no score questions
    for qid, q in questions.items():
        assert q["type"] == "choice", f"Question {qid} must be 'choice', not 'score'"
        criteria = q["criteria"]
        # PRD rule: no yes/no or true/false keys in choice questions
        for key in criteria.keys():
            assert key.lower() not in ("yes", "no", "true", "false")

    # PRD rule: exact option keys
    assert set(questions["task_family"]["criteria"].keys()) == {"quick", "general", "code", "deep"}
    assert set(questions["effort"]["criteria"].keys()) == {"light", "normal", "deep"}


def test_mock_engine_route_execution():
    cfg = AppConfig(mock_mode=True, api_key="secret")
    eng = LayaRouterEngine(cfg)
    eng.initialize()
    assert eng.ready is True

    res = eng.route(prompt="Fix this syntax error in Python", recent_turns=[])
    assert res["family"] == "code"
    assert res["effort"] in ("light", "normal", "deep")
    assert res["confidence"]["family"] > 0.5
    assert res["checkpoint"] in ("english", "multilingual")
    assert res["question_set"] == "hermes-v1"


def test_engine_device_xpu_configuration():
    cfg = AppConfig(device="xpu", mock_mode=True, api_key="secret")
    eng = LayaRouterEngine(cfg)
    eng.initialize()
    assert eng.ready is True
    res = eng.route(prompt="Test prompt", recent_turns=[])
    assert "family" in res

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


def test_format_state_large_prompt_truncation():
    large_text = "HEAD_CONTENT " + ("X" * 10000) + " TAIL_CONTENT"
    state = LayaRouterEngine.format_state(large_text, [])
    assert len(state["request"]) < 7000
    assert "HEAD_CONTENT" in state["request"]
    assert "TAIL_CONTENT" in state["request"]
    assert "[truncated for routing]" in state["request"]


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


def test_engine_provider_and_models_resolution():
    cfg = AppConfig(
        provider="openrouter",
        model_quick="google/gemini-2.5-flash-lite",
        model_code="anthropic/claude-3.5-sonnet",
        mock_mode=True,
        api_key="secret",
    )
    eng = LayaRouterEngine(cfg)
    eng.initialize()
    res = eng.route(prompt="Fix this syntax error in Python", recent_turns=[])
    assert res["family"] == "code"
    assert res["provider"] == "openrouter"
    assert res["model"] == "anthropic/claude-3.5-sonnet"


def test_engine_family_parameters_and_defaults():
    cfg = AppConfig(mock_mode=True, api_key="secret")
    eng = LayaRouterEngine(cfg)
    eng.initialize()

    # Quick prompt: algemene vraag zonder tools
    quick_res = eng.route(prompt="Wat is de hoofdstad van Australië", recent_turns=[])
    assert quick_res["family"] == "quick"
    assert quick_res["model"] == "gemini-3.5-flash-lite"
    assert quick_res["effort"] in ("light", "normal", "deep")
    assert quick_res["reasoning_effort"] == "low"
    assert quick_res["max_tokens"] == 1024
    assert quick_res["temperature"] == 0.2
    assert quick_res["thinking_budget"] == 0
    assert quick_res["needs_memory"] is False
    assert quick_res["allowed_tools"] == []

    # Smarthome prompt: lampen uit
    sh_res = eng.route(prompt="Zet de lampen in de woonkamer uit", recent_turns=[])
    assert sh_res["family"] == "smarthome"
    assert sh_res["model"] == "gemini-3.5-flash-lite"
    assert sh_res["effort"] in ("light", "normal", "deep")
    assert sh_res["reasoning_effort"] == "low"
    assert sh_res["needs_memory"] is False
    assert "ha_call_service" in sh_res["allowed_tools"]

    # Code prompt: python script
    code_res = eng.route(prompt="Schrijf een Python script om data te filteren", recent_turns=[])
    assert code_res["family"] == "code"
    assert code_res["model"] == "gemini-flash-latest"
    assert code_res["effort"] in ("light", "normal", "deep")
    assert code_res["reasoning_effort"] == "high"
    assert code_res["max_tokens"] == 8192
    assert code_res["temperature"] == 0.1
    # PRD & User requirement: no thinking budget for code
    assert "thinking_budget" not in code_res

    # Deep prompt: hypotheek berekenen
    deep_res = eng.route(prompt="Bereken de annuïtaire hypotheek voor 450k", recent_turns=[])
    assert deep_res["family"] == "deep"
    assert deep_res["model"] == "gemini-3.1-pro"
    assert deep_res["effort"] in ("light", "normal", "deep")
    assert deep_res["reasoning_effort"] == "high"
    assert deep_res["max_tokens"] == 8192
    assert deep_res["temperature"] == 0.2
    # PRD & User requirement: no thinking budget for deep
    assert "thinking_budget" not in deep_res

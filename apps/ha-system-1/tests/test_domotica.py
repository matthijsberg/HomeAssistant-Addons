"""Unit tests for Fast-Path Domotica Engine in HA System 1 (PRD v0.4.0 M1)."""

from rootfs.usr.src.app.domotica import DomoticaEngine
from rootfs.usr.src.app.resolver import HAResolver


def test_domotica_parsing_lights():
    resolver = HAResolver()
    engine = DomoticaEngine(resolver)

    # 1. Turn off lights in room
    action = engine.parse("doe de lampen in de serre uit")
    assert action.is_domotica is True
    assert action.domain == "light"
    assert action.service == "turn_off"
    assert "serre" in (action.target_name or "")
    assert action.fast_path is True
    assert action.openai_tool_call is not None
    assert action.openai_tool_call["function"]["name"] == "HassTurnOff"
    assert action.speech is not None
    assert "uitgeschakeld" in action.speech

    # 2. Turn on lights
    action2 = engine.parse("zet het licht in de woonkamer aan")
    assert action2.is_domotica is True
    assert action2.domain == "light"
    assert action2.service == "turn_on"
    assert action2.fast_path is True
    assert action2.openai_tool_call is not None
    assert action2.openai_tool_call["function"]["name"] == "HassTurnOn"
    assert action2.speech is not None and "aangezet" in action2.speech


def test_domotica_parsing_climate():
    resolver = HAResolver()
    engine = DomoticaEngine(resolver)

    action = engine.parse("zet de thermostaat op 20.5 graden")
    assert action.is_domotica is True
    assert action.domain == "climate"
    assert action.service == "set_temperature"
    assert action.service_data.get("temperature") == 20.5
    assert action.fast_path is True


def test_domotica_safety_gates_m1():
    resolver = HAResolver()
    engine = DomoticaEngine(resolver)

    # FP-03: Questions never take fast path
    q1 = engine.parse("staan de lampen in de woonkamer aan?")
    assert q1.fast_path is False
    assert q1.rejected_reason == "question"

    q2 = engine.parse("is de serre lamp aan")
    assert q2.fast_path is False
    assert q2.rejected_reason == "question"

    # FP-04: Multi-intent never takes fast path
    multi = engine.parse("doe de lampen in de serre uit en zet de verwarming op 20")
    assert multi.fast_path is False
    assert multi.rejected_reason == "multi_intent"

    # FP-05: Conditions & negations never take fast path
    neg = engine.parse("doe de lamp in de woonkamer niet uit")
    assert neg.fast_path is False
    assert neg.rejected_reason == "conditional"

    sched = engine.parse("doe de lampen in de tuin over 10 minuten aan")
    assert sched.fast_path is False
    assert sched.rejected_reason == "conditional"

    # FP-11 / D-5: Sensitive devices never take fast path
    lock = engine.parse("doe het deurslot open")
    assert lock.fast_path is False
    assert lock.rejected_reason == "sensitive_target"

    alarm = engine.parse("schakel het alarm uit")
    assert alarm.fast_path is False
    assert alarm.rejected_reason == "sensitive_target"

    # FP-06: Out of range temperature
    crazy_temp = engine.parse("zet de thermostaat op 85 graden")
    assert crazy_temp.fast_path is False
    assert crazy_temp.rejected_reason == "out_of_range"

    # FP-09: Unknown target never invented
    unknown = engine.parse("doe de lampen in narnia uit")
    assert unknown.fast_path is False
    assert unknown.rejected_reason == "unknown_target"


def test_domotica_non_smart_home_prompt():
    resolver = HAResolver()
    engine = DomoticaEngine(resolver)

    action = engine.parse("Wat is de hoofdstad van Australië?")
    assert action.is_domotica is False
    assert action.fast_path is False

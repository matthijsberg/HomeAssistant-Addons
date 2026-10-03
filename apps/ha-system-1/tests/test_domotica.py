"""Unit tests for Fast-Path Domotica Engine in HA System 1 (PRD v0.4.0 M1)."""

from rootfs.usr.src.app.domotica import DomoticaEngine
from rootfs.usr.src.app.resolver import HAResolver


def test_domotica_parsing_lights():
    resolver = HAResolver()
    engine = DomoticaEngine(resolver)

    # 1. Turn off lights in room (Area Precedence - Optie 1 + Optie 3)
    action = engine.parse("doe de lampen in de serre uit")
    assert action.is_domotica is True
    assert action.domain == "light"
    assert action.service == "turn_off"
    assert action.target_type == "area_id"
    assert action.target_id == "serre"
    assert action.fast_path is True
    assert action.openai_tool_call is not None
    assert action.openai_tool_call["function"]["name"] == "HassTurnOff"
    assert action.openai_tool_call["function"]["arguments"]["area"] == "serre"
    assert action.openai_tool_call["function"]["arguments"]["domain"] == "light"
    assert action.speech is not None
    assert "alle lampen in de serre zijn uitgeschakeld" in action.speech

    # 2. Turn on lights
    action2 = engine.parse("zet het licht in de woonkamer aan")
    assert action2.is_domotica is True
    assert action2.domain == "light"
    assert action2.service == "turn_on"
    assert action2.fast_path is True
    assert action2.openai_tool_call is not None
    assert action2.openai_tool_call["function"]["name"] == "HassTurnOn"
    assert action2.speech is not None and "aangezet" in action2.speech

    # 3. Specific subtype in room ("spots in de serre") targets the specific spots, not entire area!
    resolver.load_cached_entities({
        "light.serre_spots_dimmer": {
            "entity_id": "light.serre_spots_dimmer",
            "domain": "light",
            "friendly_name": "Serre Spots Dimmer",
            "state": "on",
            "attributes": {},
        },
        "light.serre_gordijnen_licht": {
            "entity_id": "light.serre_gordijnen_licht",
            "domain": "light",
            "friendly_name": "Serre Gordijnen Licht",
            "state": "on",
            "attributes": {},
        },
    })
    action3 = engine.parse("doe de spots in de serre uit")
    assert action3.is_domotica is True
    assert action3.domain == "light"
    assert action3.service == "turn_off"
    assert action3.target_type == "entity_id"
    assert action3.target_id == "light.serre_spots_dimmer"
    assert action3.openai_tool_call is not None
    assert action3.openai_tool_call["function"]["arguments"]["name"] == "light.serre_spots_dimmer"


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

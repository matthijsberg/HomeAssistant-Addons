"""Unit tests for Fast-Path Domotica Engine in Laya v2.0."""

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

    # 2. Turn on lights
    action2 = engine.parse("zet het licht in de woonkamer aan")
    assert action2.is_domotica is True
    assert action2.domain == "light"
    assert action2.service == "turn_on"
    assert action2.fast_path is True
    assert action2.openai_tool_call is not None
    assert action2.openai_tool_call["function"]["name"] == "HassTurnOn"


def test_domotica_parsing_climate():
    resolver = HAResolver()
    engine = DomoticaEngine(resolver)

    action = engine.parse("zet de thermostaat op 20.5 graden")
    assert action.is_domotica is True
    assert action.domain == "climate"
    assert action.service == "set_temperature"
    assert action.service_data.get("temperature") == 20.5


def test_domotica_non_smart_home_prompt():
    resolver = HAResolver()
    engine = DomoticaEngine(resolver)

    action = engine.parse("Wat is de hoofdstad van Australië?")
    assert action.is_domotica is False
    assert action.fast_path is False

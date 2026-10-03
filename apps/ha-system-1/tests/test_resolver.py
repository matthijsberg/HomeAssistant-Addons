"""Unit tests for Home Assistant Resolver in Laya v2.0."""

from rootfs.usr.src.app.resolver import HAResolver


def test_resolver_initialization():
    resolver = HAResolver(supervisor_token="dummy-token", ha_url="http://mock-ha:8123/api")
    assert resolver.is_configured is True
    assert resolver.ha_url == "http://mock-ha:8123/api"
    assert resolver.supervisor_token == "dummy-token"


def test_resolver_target_resolution_with_mock_data():
    resolver = HAResolver()
    mock_entities = {
        "light.woonkamer_spots_dimmer": {
            "entity_id": "light.woonkamer_spots_dimmer",
            "domain": "light",
            "friendly_name": "Woonkamer Spots Dimmer",
            "state": "off",
            "attributes": {},
        },
        "light.serre_spots_dimmer": {
            "entity_id": "light.serre_spots_dimmer",
            "domain": "light",
            "friendly_name": "Serre Spots Dimmer",
            "state": "off",
            "attributes": {},
        },
        "climate.woonkamer_climate_daikin": {
            "entity_id": "climate.woonkamer_climate_daikin",
            "domain": "climate",
            "friendly_name": "Daikin Woonkamer Thermostaat",
            "state": "heat",
            "attributes": {},
        },
    }
    resolver.load_cached_entities(mock_entities)

    # 1. Exact or high-confidence entity match
    t_type, t_id, conf = resolver.resolve_target(domain="light", target_name="serre spots dimmer")
    assert t_type == "entity_id"
    assert t_id == "light.serre_spots_dimmer"
    assert conf >= 0.90

    # 2. Area fallback resolution
    t_type, t_id, conf = resolver.resolve_target(domain="light", target_name="in de woonkamer")
    assert t_type in ("entity_id", "area_id")
    assert t_id is not None
    if t_type == "entity_id":
        assert "woonkamer" in t_id
    else:
        assert t_id == "woonkamer"
    assert conf >= 0.80

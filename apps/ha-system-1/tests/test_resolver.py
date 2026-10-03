"""Unit tests for Home Assistant Resolver in HA System 1 (PRD v0.4.0 M2)."""

import time

from rootfs.usr.src.app.resolver import HAResolver, fold_diacritics


def test_resolver_initialization():
    resolver = HAResolver(supervisor_token="dummy-token", ha_url="http://mock-ha:8123/api")
    assert resolver.is_configured is True
    assert resolver.ha_url == "http://mock-ha:8123/api"
    assert resolver.supervisor_token == "dummy-token"
    assert resolver.is_stale is False


def test_fold_diacritics():
    assert fold_diacritics("jaloezieën") == "jaloezieen"
    assert fold_diacritics("crème brûlée") == "creme brulee"
    assert fold_diacritics("Woonkamer") == "woonkamer"


def test_resolver_target_resolution_with_mock_data():
    resolver = HAResolver()
    mock_entities = {
        "light.woonkamer_spots_dimmer": {
            "entity_id": "light.woonkamer_spots_dimmer",
            "domain": "light",
            "friendly_name": "Woonkamer Spots Dimmer",
            "state": "off",
            "attributes": {
                "aliases": ["hoofdverlichting woonkamer", "spots beneden"],
            },
        },
        "cover.serre_jaloezieen": {
            "entity_id": "cover.serre_jaloezieen",
            "domain": "cover",
            "friendly_name": "Serre Jaloezieën",
            "state": "open",
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

    # 1. Exact or high-confidence entity match with diacritics folding (RG-04)
    t_type, t_id, conf = resolver.resolve_target(domain="cover", target_name="serre jaloezieën")
    assert t_type == "entity_id"
    assert t_id == "cover.serre_jaloezieen"
    assert conf >= 0.90

    # 2. Match via alias (RG-04)
    t_type_a, t_id_a, conf_a = resolver.resolve_target(domain="light", target_name="spots beneden")
    assert t_type_a == "entity_id"
    assert t_id_a == "light.woonkamer_spots_dimmer"
    assert conf_a >= 0.90

    # 3. Area fallback resolution
    t_type, t_id, conf = resolver.resolve_target(domain="light", target_name="in de woonkamer")
    assert t_type in ("entity_id", "area_id")
    assert t_id is not None
    if t_type == "entity_id":
        assert "woonkamer" in t_id
    else:
        assert t_id == "woonkamer"
    assert conf >= 0.80


def test_resolver_staleness_check():
    resolver = HAResolver(max_staleness_minutes=1.0)
    resolver.load_cached_entities({})
    # Fake last sync time to 2 minutes ago
    resolver._last_sync_time = time.time() - 120.0
    assert resolver.is_stale is True

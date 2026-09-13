import pytest
from datetime import datetime
from models.decision_log import DecisionRecord


def test_decision_record_creation_and_serialization():
    rec = DecisionRecord(
        timestamp_iso="2026-09-13T12:00:00+02:00",
        domain="dhw",
        decision_type="opportunistic_merge",
        chosen_mode="max_on",
        target_temp_c=60.0,
        inputs={
            "tank_temp_c": 52.4,
            "current_power_kw": 2.85,
            "current_price_eur": 0.231,
            "candidate_price_eur": 0.205,
            "price_diff_eur": 0.026,
            "solar_surplus_kw": 0.65,
            "has_solar_surplus": False,
            "is_hard_lockout": False
        },
        reason="Opportunistische Run-Fusie",
        explanation="Middagrun samengevoegd om koude compressor-start te voorkomen.",
        savings_estimate_eur=0.21
    )

    d = rec.to_dict()
    assert d["domain"] == "dhw"
    assert d["chosen_mode"] == "max_on"
    assert d["inputs"]["tank_temp_c"] == 52.4

    line = rec.to_influx_line("hems_decisions")
    assert line.startswith("hems_decisions,domain=dhw,decision_type=opportunistic_merge,chosen_mode=max_on")
    assert 'reason="Opportunistische Run-Fusie"' in line
    assert "tank_temp_c=52.400" in line
    assert "price_diff_eur=0.026" in line
    assert "has_solar_surplus=false" in line
    assert "savings_eur=0.21" in line

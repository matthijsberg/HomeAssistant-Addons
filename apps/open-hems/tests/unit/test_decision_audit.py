import pytest
from pathlib import Path
from layer3_scheduling.decision_audit import DecisionAuditLogger, AUDIT_FILE


def test_decision_audit_logger_roundtrip(tmp_path, monkeypatch):
    test_file = tmp_path / "test_decisions.jsonl"
    monkeypatch.setattr("layer3_scheduling.decision_audit.AUDIT_FILE", test_file)
    monkeypatch.setattr("layer3_scheduling.decision_audit.ENABLE_INFLUX_QUERY", False)

    rec1 = DecisionAuditLogger.log_decision(
        domain="dhw",
        decision_type="opportunistic_merge",
        chosen_mode="max_on",
        target_temp_c=60.0,
        inputs={"tank_temp_c": 52.0, "current_price_eur": 0.22},
        reason="Test Fusie",
        explanation="Test toelichting 1",
        savings_estimate_eur=0.20
    )

    rec2 = DecisionAuditLogger.log_decision(
        domain="space_heating",
        decision_type="preheat_boost",
        chosen_mode="advised_on",
        target_temp_c=21.0,
        inputs={"outdoor_temp_c": 8.0, "current_price_eur": 0.18},
        reason="Nachtdal Vloerbuffer",
        explanation="Test toelichting 2",
        savings_estimate_eur=0.35
    )

    all_recs = DecisionAuditLogger.get_recent_decisions(limit=10)
    assert len(all_recs) == 2
    assert all_recs[0]["domain"] == "space_heating"  # newest first
    assert all_recs[1]["domain"] == "dhw"

    dhw_recs = DecisionAuditLogger.get_recent_decisions(limit=10, domain="dhw")
    assert len(dhw_recs) == 1
    assert dhw_recs[0]["decision_type"] == "opportunistic_merge"

"""
Unit tests for KPI Presenter and API DTO Data Structures (Clean Architecture Layer 5).
"""

from models.api_dto import KpiCardItem, KpiBreakdownItem, DecisionExplanationDTO
from layer5_analytics.kpi_presenter import KpiPresenter
from unittest.mock import MagicMock


def test_api_dto_to_dict():
    item = KpiBreakdownItem(
        label="Test Sluipverbruik",
        icon="🏠",
        kwh=5.0,
        eur=1.25,
        desc="Baseload description"
    )
    d = item.to_dict()
    assert d["label"] == "Test Sluipverbruik"
    assert d["kwh"] == 5.0
    assert d["eur"] == 1.25
    assert d["desc"] == "Baseload description"

    card = KpiCardItem(
        title="Kosten",
        main="€1.25",
        sub="5.0 kWh",
        breakdown=[item]
    )
    card_dict = card.to_dict()
    assert card_dict["title"] == "Kosten"
    assert len(card_dict["breakdown"]) == 1


def test_kpi_presenter_build_forecast_kpis():
    plan = MagicMock()
    plan.slots = []
    plan.dhw_summary = MagicMock()
    plan.dhw_summary.total_stroom_kwh = 2.5
    plan.dhw_summary.arbitrage_saving_eur = 0.65

    unalloc = [0.3] * 96
    boiler = [0.0] * 96
    boiler[8:12] = [2.0, 2.0, 2.0, 2.0]  # 2 kW for 1 hour = 2 kWh
    heating = [0.0] * 96
    heating[20:28] = [1.5] * 8  # 1.5 kW for 2 hours = 3 kWh
    solar = [0.0] * 96
    solar[40:60] = [2.5] * 20  # 5 hours of 2.5 kW
    prices = [0.25] * 96

    kpis = KpiPresenter.build_forecast_kpis(
        unallocated=unalloc,
        boiler=boiler,
        heating=heating,
        solar=solar,
        prices=prices,
        step_h=0.25,
        plan=plan,
        pred_afname_kwh=10.0,
        pred_afname_eur=2.50,
        pred_terug_kwh=5.0,
        pred_terug_eur=0.30,
        pred_selfcons_kwh=5.0,
        pred_selfcons_eur=1.25,
        net_cost_eur=2.20,
        tot_cons_kwh=15.0
    )

    assert "costs" in kpis
    assert "solar" in kpis
    assert "savings" in kpis
    assert "heatpump" in kpis

    # Validate breakdowns exist and contain expected items
    assert len(kpis["costs"]["breakdown"]) >= 5
    assert len(kpis["solar"]["breakdown"]) >= 3
    assert len(kpis["savings"]["breakdown"]) >= 3
    assert len(kpis["heatpump"]["breakdown"]) >= 3

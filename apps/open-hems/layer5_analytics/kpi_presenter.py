"""
Layer 5: KPI Presentation & DTO Builder
========================================
Implements the Presenter Pattern (Clean Architecture).
Extracts UI formatting, currency localization, consumer breakdowns,
and arbitrage calculations out of raw HTTP API handlers into testable pure functions.
"""

from typing import List, Dict, Any
from models.api_dto import KpiCardItem, KpiBreakdownItem


class KpiPresenter:
    """Formats raw dispatch and historical telemetric aggregates into structured KPI DTOs."""

    @classmethod
    def build_forecast_kpis(
        cls,
        unallocated: List[float],
        boiler: List[float],
        heating: List[float],
        solar: List[float],
        prices: List[float],
        step_h: float,
        plan: Any,
        pred_afname_kwh: float,
        pred_afname_eur: float,
        pred_terug_kwh: float,
        pred_terug_eur: float,
        pred_selfcons_kwh: float,
        pred_selfcons_eur: float,
        net_cost_eur: float,
        tot_cons_kwh: float
    ) -> Dict[str, Any]:
        """Builds standardized 24h rolling forecast KPI cards with modal breakdown specifications."""
        net_kwh_balance = round(pred_afname_kwh - pred_terug_kwh, 2)
        p_act = prices[0] if prices else 0.28
        avg_import_p = round(pred_afname_eur / pred_afname_kwh, 2) if pred_afname_kwh >= 0.1 else round(p_act, 2)

        solar_selfcons_kwh = round(pred_selfcons_kwh, 2)
        solar_selfcons_eur = round(pred_selfcons_eur, 2)
        solar_export_kwh = round(pred_terug_kwh, 2)
        solar_export_eur = round(pred_terug_eur, 2)
        tot_solar_kwh = round(sum(s * step_h for s in solar), 1)
        solar_total_value_eur = round(solar_selfcons_eur + solar_export_eur, 2)

        hp_dhw_kwh = sum(b * step_h for b in boiler)
        hp_cv_kwh = sum(h * step_h for h in heating)
        hp_tot_stroom_kwh = round(hp_dhw_kwh + hp_cv_kwh, 1)
        hp_cost_eur = round(sum((b + h) * p * step_h for b, h, p in zip(boiler, heating, prices)), 2)
        hp_tot_th = round(hp_dhw_kwh * 3.1 + hp_cv_kwh * 4.5, 1)
        hp_cop = round(hp_tot_th / hp_tot_stroom_kwh, 1) if hp_tot_stroom_kwh > 0 else 3.5
        dhw_hours = round(sum(step_h for b in boiler if b > 0.1), 1)
        cv_hours = round(sum(step_h for h in heating if h > 0.1), 1)

        # Consumer breakdowns
        unalloc_kwh = round(sum(u * step_h for u in unallocated), 2)
        unalloc_cost = round(sum(u * p * step_h for u, p in zip(unallocated, prices)), 2)
        dhw_kwh = round(hp_dhw_kwh, 2)
        dhw_cost = round(sum(b * p * step_h for b, p in zip(boiler, prices)), 2)
        cv_kwh = round(hp_cv_kwh, 2)
        cv_cost = round(sum(h * p * step_h for h, p in zip(heating, prices)), 2)

        # Savings & Arbitrage calculations (Strictly non-overlapping HEMS dispatch savings)
        slots = plan.slots if (plan and hasattr(plan, "slots") and plan.slots) else []
        peak_prices = [p for p, slot in zip(prices, slots) if getattr(slot, 'is_peak_lockout', False)]
        avg_peak_price = (sum(peak_prices) / len(peak_prices)) if peak_prices else 0.45
        dhw_avg_p = (dhw_cost / dhw_kwh) if dhw_kwh > 0.05 else 0.22
        dhw_arbitrage_saving = round(max(0.0, dhw_kwh * (avg_peak_price - dhw_avg_p)), 2)

        cv_lockout_hours = round(sum(step_h for slot in slots if getattr(slot, 'is_peak_lockout', False)), 1)
        cv_spitsblok_saving = round(cv_lockout_hours * 0.8 * max(0.05, avg_peak_price - 0.25), 2)

        # Zero double-counting: total HEMS arbitrage is purely the sum of DHW peak avoidance and CV lockout savings!
        tot_savings = round(dhw_arbitrage_saving + cv_spitsblok_saving, 2)

        shifted_el_kwh = round(hp_dhw_kwh + hp_cv_kwh, 1)
        shifted_th_kwh = round(hp_tot_th, 1)

        net_sign = "+" if net_kwh_balance > 0 else ""
        costs_card = KpiCardItem(
            title="Kosten",
            main=f"€{net_cost_eur:.2f}",
            sub=f"{net_sign}{net_kwh_balance:.1f} kWh netto",
            headline=f"Verwachte energiekosten: €{net_cost_eur:.2f}",
            explanation="Netto kosten bestaan uit huishoudelijk sluipverbruik, tapwater en CV, minus gratis zonne-energie en feed-in vergoeding.",
            footer=f"Totaal verbruik: {tot_cons_kwh:.1f} kWh el · Netto netafname: {net_kwh_balance:.1f} kWh el",
            breakdown=[
                KpiBreakdownItem("Sluip- & Basisverbruik", "blue", unalloc_kwh, unalloc_cost, "300W continue huishoudlast (koelkast, ventilatie, stand-by)"),
                KpiBreakdownItem("Warm Tapwater (DHW)", "pink", dhw_kwh, dhw_cost, f"Boilerrun(s) gepland op daltarief/zon (€{dhw_avg_p:.2f}/kWh)"),
                KpiBreakdownItem("CV Ruimteverwarming", "indigo", cv_kwh, cv_cost, f"Vloerverwarming & thermische buffer ({cv_hours}u stooktijd)"),
                KpiBreakdownItem("Zon Direct Benut (Aftrek)", "amber", -solar_selfcons_kwh, -solar_selfcons_eur, "Gratis eigen dakopwekking direct in huis verbruikt"),
                KpiBreakdownItem("Teruglevering aan het Net", "cyan", -solar_export_kwh, -solar_export_eur, "Overtollige zonne-energie tegen teruglevertarief")
            ]
        )

        solar_card = KpiCardItem(
            title="Zonnepanelen",
            main=f"€{solar_total_value_eur:.2f}",
            main_extra="",
            sub=f"{tot_solar_kwh:.1f} kWh opwek",
            headline=f"Zonnepanelen Totale Waarde: €{solar_total_value_eur:.2f} ({tot_solar_kwh:.1f} kWh el)",
            explanation="Open HEMS maximaliseert het eigen verbruik door tapwater en vloerverwarming tijdens zonneschijn te sturen.",
            footer=f"Zelfconsumptie: {round((solar_selfcons_kwh / tot_solar_kwh * 100) if tot_solar_kwh > 0 else 0)}% · Vermeden netafname: €{solar_selfcons_eur:.2f}",
            breakdown=[
                KpiBreakdownItem("Direct Eigen Verbruik", "amber", solar_selfcons_kwh, solar_selfcons_eur, f"{round((solar_selfcons_kwh / tot_solar_kwh * 100) if tot_solar_kwh > 0 else 0)}% van de opwek direct in huis/boiler benut"),
                KpiBreakdownItem("Teruglevering aan het Net", "cyan", solar_export_kwh, solar_export_eur, f"{round((solar_export_kwh / tot_solar_kwh * 100) if tot_solar_kwh > 0 else 0)}% geëxporteerd tegen teruglevertarief"),
                KpiBreakdownItem("Totale Zonne-opwekking", "emerald", tot_solar_kwh, solar_total_value_eur, "5.76 kWp ZW-installatie conform 15m weerinterpolatie")
            ]
        )

        savings_card = KpiCardItem(
            title="Besparing",
            main=f"€{tot_savings:.2f}",
            sub=f"{shifted_el_kwh:.1f} kWh gestuurd",
            headline=f"Totale HEMS Besparing: €{tot_savings:.2f}",
            explanation="Berekend t.o.v. een ongeoptimaliseerde thermostaat die zonder rekening te houden met dynamische tarieven of spitsperiodes zou stoken.",
            footer=f"{shifted_el_kwh:.1f} kWh el stroom benut voor {shifted_th_kwh:.1f} kWh th warmte",
            breakdown=[
                KpiBreakdownItem("Tapwater Spitsvermijding", "pink", dhw_kwh, dhw_arbitrage_saving, f"{dhw_kwh:.1f} kWh el ({round(dhw_kwh * 3.1, 1)} kWh th) op dal/zon (€{dhw_avg_p:.2f}/kWh) i.p.v. spits (€{avg_peak_price:.2f}/kWh)"),
                KpiBreakdownItem("CV Spitsblokkades", "indigo", round(cv_lockout_hours * 0.8, 1), cv_spitsblok_saving, f"{cv_lockout_hours}u stookblokkades opgevangen door de 13,2 kWh/K dekvloer")
            ]
        )

        hp_card = KpiCardItem(
            title="Warmtepomp",
            main=f"{hp_tot_stroom_kwh:.1f} kWh el",
            main_extra="",
            sub=f"~€{hp_cost_eur:.2f}",
            headline=f"Warmtepomp Totaal: {hp_tot_stroom_kwh:.1f} kWh el (~€{hp_cost_eur:.2f})",
            explanation="De Daikin Altherma levert zowel tapwater als vloerverwarming via een geoptimaliseerd Smart Grid relaisprofiel.",
            footer=f"Totale stookduur: {round(dhw_hours + cv_hours, 1)} uur · Thermische opbrengst: {hp_tot_th:.1f} kWh th",
            breakdown=[
                KpiBreakdownItem("Tapwater (DHW Boiler)", "pink", dhw_kwh, dhw_cost, f"{dhw_hours}u stooktijd · {round(dhw_kwh * 3.1, 1)} kWh th warmte (gem. COP ~3,1)"),
                KpiBreakdownItem("Ruimteverwarming (CV Vloer)", "indigo", cv_kwh, cv_cost, f"{cv_hours}u stooktijd · {round(cv_kwh * 4.5, 1)} kWh th warmte (gem. COP ~4,5)"),
                KpiBreakdownItem("Totaal Warmteopbrengst", "emerald", hp_tot_th, hp_cost_eur, f"Seizoens-COP {hp_cop:.1f} over alle runs gecombineerd")
            ]
        )

        return {
            "costs": costs_card.to_dict(),
            "solar": solar_card.to_dict(),
            "savings": savings_card.to_dict(),
            "heatpump": hp_card.to_dict()
        }

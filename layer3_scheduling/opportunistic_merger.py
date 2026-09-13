"""
Layer 3: Opportunistic In-Flight DHW Run Merger & 50°C vs 60°C Trade-Off Engine
=============================================================================
Detects when the heat pump starts heating domestic hot water autonomously
(e.g. after a family shower) and decides whether to merge an upcoming
60°C thermal storage / solar boost run into the active run or stop at 50°C.
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from models.canonical import CanonicalDispatchPlan, DispatchPlanSlot


@dataclass
class OpportunisticMergeResult:
    should_merge: bool
    promoted_mode: str
    target_temp_c: float
    reason: str
    decision_explanation: str
    original_slot_idx: Optional[int]
    original_slot_time: Optional[str]
    savings_estimate_eur: float
    cancelled_slots: List[int]
    is_dhw_active: bool = False
    current_tank_temp_c: float = 50.0
    current_power_kw: float = 0.0
    active_target_temp_c: float = 50.0
    active_mode_label: str = "Normaal"


class OpportunisticDHWMerger:
    LOOKAHEAD_SLOTS_MAX = 24  # 6 hours lookahead (covers full daytime solar window)
    PRICE_TOLERANCE_EUR = 0.05  # Up to 5 cents/kWh difference is compensated by ramp-up savings
    RAMP_UP_PENALTY_KWH = 0.25  # Starting compressor cold consumes ~0.25 kWh overhead

    @classmethod
    def evaluate_merge(
        cls,
        plan: CanonicalDispatchPlan,
        is_dhw_actively_heating: bool,
        current_tank_temp_c: float,
        current_solar_kw: float,
        current_price_eur: float,
        is_hard_lockout_now: bool = False,
        current_power_kw: float = 0.0,
        lookahead_slots: Optional[int] = None
    ) -> OpportunisticMergeResult:
        """
        Evaluates whether an active DHW heating run should be promoted to 60°C
        and merged with an upcoming planned run or kept at 50°C standard comfort.
        """
        if not is_dhw_actively_heating:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason="Geen actieve tapwaterverwarming gedetecteerd",
                decision_explanation="Warmtepomp is niet actief voor tapwater (stand-by).",
                original_slot_idx=None,
                original_slot_time=None,
                savings_estimate_eur=0.0,
                cancelled_slots=[],
                is_dhw_active=False,
                current_tank_temp_c=current_tank_temp_c,
                current_power_kw=current_power_kw,
                active_target_temp_c=50.0,
                active_mode_label="Standby"
            )

        if is_hard_lockout_now:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason="Harde spitsblokkade actief: doorverwarmen geblokkeerd",
                decision_explanation=f"Warmtepomp is actief ({current_power_kw:.2f} kW, {current_tank_temp_c:.1f}°C) maar een harde spitsblokkade is actief. Doorverwarmen naar 60°C is niet toegestaan tegen piektarieven.",
                original_slot_idx=None,
                original_slot_time=None,
                savings_estimate_eur=0.0,
                cancelled_slots=[],
                is_dhw_active=True,
                current_tank_temp_c=current_tank_temp_c,
                current_power_kw=current_power_kw,
                active_target_temp_c=50.0,
                active_mode_label="Stoppen bij 50°C (Spitsblokkade)"
            )

        if not plan or not plan.slots:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason="Geen actief dispatch plan beschikbaar",
                decision_explanation="Geen dispatch plan geladen.",
                original_slot_idx=None,
                original_slot_time=None,
                savings_estimate_eur=0.0,
                cancelled_slots=[],
                is_dhw_active=True,
                current_tank_temp_c=current_tank_temp_c,
                current_power_kw=current_power_kw,
                active_target_temp_c=50.0,
                active_mode_label="Standaard 50°C"
            )

        active_lookahead = lookahead_slots if lookahead_slots is not None else cls.LOOKAHEAD_SLOTS_MAX

        # Scan upcoming slots within lookahead window
        candidate_slots_60: List[int] = []
        candidate_slots_50: List[int] = []
        candidate_time_60 = None
        candidate_price_60 = None
        candidate_time_50 = None

        lookahead_limit = min(len(plan.slots), active_lookahead + 1)
        for idx in range(1, lookahead_limit):
            s = plan.slots[idx]
            if s.mode_code in ["max_on", "forced_solar_boost_60"] or (s.dhw_kw > 1.0 and getattr(s, "target_temp_c", 50.0) >= 58.0):
                candidate_slots_60.append(idx)
                if candidate_time_60 is None:
                    candidate_time_60 = s.time_label
                    candidate_price_60 = s.price_eur
            elif s.mode_code in ["forced_on", "forced_night_50", "forced_standard_50"] or s.dhw_kw > 1.0:
                candidate_slots_50.append(idx)
                if candidate_time_50 is None:
                    candidate_time_50 = s.time_label

        has_solar_surplus = (current_solar_kw >= 0.8)

        # CASE A: A 60°C run is planned within the lookahead window
        if candidate_slots_60:
            price_diff = current_price_eur - (candidate_price_60 if candidate_price_60 is not None else current_price_eur)
            is_cost_efficient = has_solar_surplus or (price_diff <= cls.PRICE_TOLERANCE_EUR)

            if is_cost_efficient:
                savings = round(cls.RAMP_UP_PENALTY_KWH * current_price_eur + 0.15, 2)
                reason = (
                    f"Opportunistische Run-Fusie: Actieve tapwaterverwarming gedetecteerd (tank {current_tank_temp_c:.1f}°C). "
                    f"Geplande 60°C zonnebuffer van {candidate_time_60} direct samengevoegd met de lopende run. "
                    f"Voorkomt 2x herstarten van compressor en benut direct de warme cyclus."
                )
                decision_expl = (
                    f"Besluit: Doorwarmen naar 60°C (Zonnebuffer Fusie). De warmtepomp is reeds op bedrijfstemperatuur ({current_power_kw:.2f} kW, {current_tank_temp_c:.1f}°C). "
                    f"Omdat er om {candidate_time_60} een 60°C run gepland stond en het tariefverschil (+€{max(0.0, price_diff):.3f}/kWh) ruimschoots wordt goedgemaakt door het vermeden opstartverlies (~0,25 kWh), "
                    f"wordt de cyclus direct in één keer doorgewarmd tot 60°C. De latere run van {candidate_time_60} is geannuleerd."
                )
                return OpportunisticMergeResult(
                    should_merge=True,
                    promoted_mode="max_on",
                    target_temp_c=60.0,
                    reason=reason,
                    decision_explanation=decision_expl,
                    original_slot_idx=candidate_slots_60[0],
                    original_slot_time=candidate_time_60,
                    savings_estimate_eur=savings,
                    cancelled_slots=candidate_slots_60,
                    is_dhw_active=True,
                    current_tank_temp_c=current_tank_temp_c,
                    current_power_kw=current_power_kw,
                    active_target_temp_c=60.0,
                    active_mode_label="Doorwarmen naar 60°C (Zonnebuffer Fusie)"
                )
            else:
                reason = f"Huidig tarief (€{current_price_eur:.3f}) te hoog t.o.v. geplande run (€{candidate_price_60:.3f})"
                decision_expl = (
                    f"Besluit: Stoppen bij 50°C (Standaard Basislading). De warmtepomp herstelt het basiscomfort ({current_power_kw:.2f} kW, {current_tank_temp_c:.1f}°C). "
                    f"Doorwarmen naar 60°C wordt nu niet gedaan omdat de geplande middagrun om {candidate_time_60} aanzienlijk goedkoper is (€{candidate_price_60:.3f} vs €{current_price_eur:.3f}/kWh)."
                )
                return OpportunisticMergeResult(
                    should_merge=False,
                    promoted_mode="forced_on",
                    target_temp_c=50.0,
                    reason=reason,
                    decision_explanation=decision_expl,
                    original_slot_idx=candidate_slots_60[0],
                    original_slot_time=candidate_time_60,
                    savings_estimate_eur=0.0,
                    cancelled_slots=[],
                    is_dhw_active=True,
                    current_tank_temp_c=current_tank_temp_c,
                    current_power_kw=current_power_kw,
                    active_target_temp_c=50.0,
                    active_mode_label="Stoppen bij 50°C (Basislading)"
                )

        # CASE B: Only a 50°C run is planned (or no run planned)
        if candidate_slots_50:
            # Active run fulfills the 50C planned run!
            decision_expl = (
                f"Besluit: Stoppen bij 50°C (Standaard Basislading). De warmtepomp is aangeslagen en levert {current_power_kw:.2f} kW bij {current_tank_temp_c:.1f}°C. "
                f"Hiermee is het vat reeds op 50°C niveau. De latere run van {candidate_time_50} is vervallen."
            )
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="forced_on",
                target_temp_c=50.0,
                reason="Lopende run vervangt geplande 50°C basisrun",
                decision_explanation=decision_expl,
                original_slot_idx=candidate_slots_50[0],
                original_slot_time=candidate_time_50,
                savings_estimate_eur=0.10,
                cancelled_slots=candidate_slots_50,
                is_dhw_active=True,
                current_tank_temp_c=current_tank_temp_c,
                current_power_kw=current_power_kw,
                active_target_temp_c=50.0,
                active_mode_label="Stoppen bij 50°C (Basislading)"
            )

        # CASE C: No upcoming run planned
        decision_expl = (
            f"Besluit: Stoppen bij 50°C (Standaard Basislading). De warmtepomp herstelt autonoom het basiscomfort tot 50°C ({current_power_kw:.2f} kW, {current_tank_temp_c:.1f}°C). "
            f"Er was geen 60°C zonnebuffer gepland in de komende {active_lookahead // 4} uur."
        )
        return OpportunisticMergeResult(
            should_merge=False,
            promoted_mode="forced_on",
            target_temp_c=50.0,
            reason=f"Geen 60°C zonnebuffer gepland binnen het venster ({active_lookahead // 4} uur)",
            decision_explanation=decision_expl,
            original_slot_idx=None,
            original_slot_time=None,
            savings_estimate_eur=0.0,
            cancelled_slots=[],
            is_dhw_active=True,
            current_tank_temp_c=current_tank_temp_c,
            current_power_kw=current_power_kw,
            active_target_temp_c=50.0,
            active_mode_label="Stoppen bij 50°C (Autonoom)"
        )

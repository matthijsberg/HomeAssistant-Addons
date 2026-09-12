"""
Layer 3: Opportunistic DHW Run Merger
====================================
Detects when the heat pump starts heating DHW autonomously (e.g. due to
a shower draw-off) and checks if a planned 60°C run within the lookahead
window should be opportunistically merged into the active cycle.

Benefits:
- Eliminates 1 compressor start/stop cycle (reducing compressor wear).
- Eliminates thermal ramp-up overhead (~0.25 kWh).
- Warms to 60°C immediately while the water is already circulating,
  covering the rest of the day and morning peak without a second run.
"""

from dataclasses import dataclass
from typing import Optional, List
from models.canonical import CanonicalDispatchPlan


@dataclass
class OpportunisticMergeResult:
    should_merge: bool
    promoted_mode: str
    target_temp_c: float
    reason: str
    original_slot_idx: Optional[int]
    original_slot_time: Optional[str]
    savings_estimate_eur: float
    cancelled_slots: List[int]


class OpportunisticDHWMerger:
    LOOKAHEAD_SLOTS_MAX = 8  # 2 hours (8 x 15-min quarters)
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
        is_hard_lockout_now: bool = False
    ) -> OpportunisticMergeResult:
        """
        Evaluates whether an active DHW heating run should be promoted to 60°C
        and merged with an upcoming planned run.
        """
        if not is_dhw_actively_heating:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason="Geen actieve tapwaterverwarming gedetecteerd",
                original_slot_idx=None,
                original_slot_time=None,
                savings_estimate_eur=0.0,
                cancelled_slots=[]
            )

        if is_hard_lockout_now:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason="Harde spitsblokkade actief: doorverwarmen geblokkeerd",
                original_slot_idx=None,
                original_slot_time=None,
                savings_estimate_eur=0.0,
                cancelled_slots=[]
            )

        if not plan or not plan.slots:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason="Geen actief dispatch plan beschikbaar",
                original_slot_idx=None,
                original_slot_time=None,
                savings_estimate_eur=0.0,
                cancelled_slots=[]
            )

        # Scan upcoming slots within lookahead window (slots 1 to 8)
        candidate_slots: List[int] = []
        candidate_time = None
        candidate_price = None

        for idx in range(1, min(len(plan.slots), cls.LOOKAHEAD_SLOTS_MAX + 1)):
            s = plan.slots[idx]
            if s.mode_code in ["max_on", "forced_solar_boost_60"]:
                candidate_slots.append(idx)
                if candidate_time is None:
                    candidate_time = s.time_label
                    candidate_price = s.price_eur

        if not candidate_slots:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason="Geen 60°C zonnebuffer gepland binnen het 2-uurs venster",
                original_slot_idx=None,
                original_slot_time=None,
                savings_estimate_eur=0.0,
                cancelled_slots=[]
            )

        # Cost efficiency evaluation:
        # Is current price acceptable compared to planned price?
        # Solar surplus (>= 1.0 kW) makes it virtually free right now.
        has_solar_surplus = (current_solar_kw >= 1.0)
        price_diff = current_price_eur - (candidate_price or current_price_eur)

        is_cost_efficient = has_solar_surplus or (price_diff <= cls.PRICE_TOLERANCE_EUR)

        if not is_cost_efficient:
            return OpportunisticMergeResult(
                should_merge=False,
                promoted_mode="normal",
                target_temp_c=50.0,
                reason=f"Huidig tarief (€{current_price_eur:.3f}) te hoog t.o.v. geplande run (€{candidate_price:.3f})",
                original_slot_idx=candidate_slots[0],
                original_slot_time=candidate_time,
                savings_estimate_eur=0.0,
                cancelled_slots=[]
            )

        # Merge approved!
        savings = round(cls.RAMP_UP_PENALTY_KWH * current_price_eur + 0.15, 2)
        reason = (
            f"Opportunistische Run-Fusie: Actieve tapwaterverwarming gedetecteerd (tank {current_tank_temp_c:.1f}°C). "
            f"Geplande 60°C zonnebuffer van {candidate_time} direct samengevoegd met de lopende run. "
            f"Voorkomt 2x herstarten van compressor en benut direct de warme cyclus."
        )

        return OpportunisticMergeResult(
            should_merge=True,
            promoted_mode="max_on",
            target_temp_c=60.0,
            reason=reason,
            original_slot_idx=candidate_slots[0],
            original_slot_time=candidate_time,
            savings_estimate_eur=savings,
            cancelled_slots=candidate_slots
        )

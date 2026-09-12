"""
Layer 3: Plan Store & Recommendation Registry
==============================================
The single authoritative store and publication channel for HEMS plans.
All dashboards, UI tabs, relay actuators, and Home Assistant sensors
read exclusively from this store.
"""

import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import asdict
from models.canonical import CanonicalDispatchPlan, DispatchPlanSlot, DHWPlanSummary


class PlanStore:
    """Thread-safe singleton storing and serving the active canonical dispatch plan."""

    _instance: Optional["PlanStore"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._current_plan: Optional[CanonicalDispatchPlan] = None
        self._last_updated: Optional[datetime] = None
        self._plan_version: int = 0
        self._publication_history: List[Dict[str, Any]] = []

    @classmethod
    def get_instance(cls) -> "PlanStore":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def publish_plan(self, plan: CanonicalDispatchPlan) -> None:
        """Publish a newly computed canonical dispatch plan."""
        with self._lock:
            self._current_plan = plan
            self._last_updated = datetime.now(timezone.utc)
            self._plan_version += 1
            # Keep lightweight publication audit
            self._publication_history.append({
                "version": self._plan_version,
                "timestamp": self._last_updated.isoformat(),
                "slot_count": len(plan.slots),
                "is_fresh": plan.is_fresh,
                "dhw_mode": plan.dhw_summary.planned_mode if plan.dhw_summary else None,
                "dynamic_peaks_count": len(plan.dynamic_peaks)
            })
            if len(self._publication_history) > 50:
                self._publication_history = self._publication_history[-50:]

    def get_plan(self) -> Optional[CanonicalDispatchPlan]:
        """Get the active canonical dispatch plan."""
        with self._lock:
            return self._current_plan

    def get_plan_dict(self) -> Optional[Dict[str, Any]]:
        """Get the active canonical dispatch plan as a JSON-serializable dictionary."""
        with self._lock:
            if self._current_plan is None:
                return None
            return asdict(self._current_plan)

    def get_version(self) -> int:
        with self._lock:
            return self._plan_version

    def get_audit_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._publication_history)


# Convenient module-level access
def get_plan_store() -> PlanStore:
    return PlanStore.get_instance()

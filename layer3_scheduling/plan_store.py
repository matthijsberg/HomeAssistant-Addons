"""
Layer 3: Plan Store & Recommendation Registry
==============================================
The single authoritative store and publication channel for HEMS plans.
Can be instantiated as an injected dependency for tests or accessed via
the singleton convenience method `get_plan_store()`.
Supports atomic local disk snapshot persistence for cold-boot resilience.
"""

import threading
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import asdict
from models.canonical import CanonicalDispatchPlan


class PlanStore:
    """Thread-safe store serving the active canonical dispatch plan."""

    _instance: Optional["PlanStore"] = None
    _lock = threading.Lock()

    def __init__(self, persistence_path: Optional[Path] = None):
        self._current_plan: Optional[CanonicalDispatchPlan] = None
        self._last_updated: Optional[datetime] = None
        self._plan_version: int = 0
        self._publication_history: List[Dict[str, Any]] = []
        self._persistence_path = persistence_path

    @classmethod
    def get_instance(cls, persistence_path: Optional[Path] = None) -> "PlanStore":
        with cls._lock:
            if cls._instance is None:
                default_path = persistence_path or Path("/config/open_hems_plan_snapshot.json")
                cls._instance = cls(persistence_path=default_path)
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

            # Atomic disk snapshot persistence
            if self._persistence_path:
                try:
                    tmp_file = self._persistence_path.with_suffix(".tmp")
                    plan_dict = asdict(plan)
                    with open(tmp_file, "w", encoding="utf-8") as f:
                        json.dump(plan_dict, f, indent=2)
                    tmp_file.replace(self._persistence_path)
                except Exception as e:
                    # Persistence failure must not crash the memory pipeline
                    pass

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
def get_plan_store(persistence_path: Optional[Path] = None) -> PlanStore:
    return PlanStore.get_instance(persistence_path=persistence_path)

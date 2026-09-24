"""
API Data Transfer Objects (DTO)
================================
Typed, immutable dataclasses for presentation endpoints and client contracts.
Adheres to Invariant #1 (Single Source of Truth) and Invariant #2 (Core Entity Isolation).
Zero Home Assistant entity strings and zero brand names.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass(frozen=True)
class KpiBreakdownItem:
    """Represents a single granular consumer or financial component in a KPI breakdown modal."""
    label: str
    icon: str
    kwh: float
    eur: Optional[float]
    desc: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KpiCardItem:
    """Represents a high-level metric card with headline and modal breakdown."""
    title: str
    main: str
    sub: str
    main_extra: str = ""
    headline: str = ""
    explanation: str = ""
    footer: str = ""
    breakdown: List[KpiBreakdownItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionExplanationDTO:
    """Structured human-readable explanation for heating and DHW dispatch decisions."""
    title: str
    status_badge: str
    comfort_text: str
    planned_runs_text: str
    buffer_text: str
    lockout_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

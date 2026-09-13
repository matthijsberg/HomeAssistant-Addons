"""
Models: Decision Record & Audit Logging (OTel-aligned)
=====================================================
Defines structured decision records for Open HEMS optimization choices,
enabling transparent auditing in InfluxDB, Grafana, and the Open HEMS UI.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json


@dataclass
class DecisionRecord:
    timestamp_iso: str
    domain: str                      # "dhw", "space_heating", "peak_lockout", "battery"
    decision_type: str               # "opportunistic_merge", "buffer_60_boost", "preheat_boost", "system_release"
    chosen_mode: str                 # 6-state taxonomy ("forced_off", "normal", "max_on", etc.)
    target_temp_c: Optional[float]
    inputs: Dict[str, Any]           # Exact physical & economic metrics at decision time
    reason: str
    explanation: str
    savings_estimate_eur: float = 0.0

    def to_influx_line(self, measurement: str = "hems_decisions") -> str:
        """Serializes decision record to InfluxDB Line Protocol."""
        now_ns = int(datetime.fromisoformat(self.timestamp_iso).timestamp() * 1e9)
        
        # Tags (low cardinality for fast Grafana filtering)
        tags = f"domain={self.domain},decision_type={self.decision_type},chosen_mode={self.chosen_mode}"
        
        # Fields
        safe_reason = self.reason.replace('"', '\\"').replace('\n', ' ')
        safe_expl = self.explanation.replace('"', '\\"').replace('\n', ' ')
        
        fields = [
            f'reason="{safe_reason}"',
            f'explanation="{safe_expl}"',
            f'savings_eur={float(self.savings_estimate_eur):.2f}'
        ]
        
        if self.target_temp_c is not None:
            fields.append(f'target_temp_c={float(self.target_temp_c):.1f}')
            
        # Extract numerical inputs as dedicated fields for Grafana aggregation
        for k, v in self.inputs.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                fields.append(f'{k}={float(v):.3f}')
            elif isinstance(v, bool):
                fields.append(f'{k}={str(v).lower()}')

        return f"{measurement},{tags} {','.join(fields)} {now_ns}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

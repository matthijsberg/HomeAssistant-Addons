"""Domotica Fast-Path Decision & Execution Engine for Laya v2.0.

Provides sub-50ms local resolution of natural language smart home prompts into
deterministic Home Assistant service calls and OpenAI-compatible tool call payloads.
"""

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field
from resolver import HAResolver

logger = logging.getLogger("laya.domotica")


# Canonical device domain triggers
DOMAIN_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "light": (
        "lamp", "lampen", "licht", "lichten", "verlichting", "spot", "spots",
        "led", "leds", "ledstrip", "strip", "hanglamp", "schemerlamp", "plafondlamp",
    ),
    "climate": (
        "thermostaat", "temperatuur", "graden", "graad", "verwarming", "airco",
        "ketel", "boiler", "cv", "kachel", "warmtepomp", "setpoint",
    ),
    "cover": (
        "gordijn", "gordijnen", "rolluik", "rolluiken", "zonwering", "scherm",
        "jaloezie", "jaloezieën", "shutter", "shutters",
    ),
    "switch": (
        "schakelaar", "stekker", "relais", "plug", "stopcontact",
    ),
    "fan": (
        "ventilator", "afzuiging", "afzuigkap", "ventilatie",
    ),
    "lock": (
        "slot", "deur", "poort", "deurslot", "voordeur", "achterdeur",
    ),
}

# Canonical action verbs mapping to Home Assistant service names
ACTION_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "turn_on": (
        "aan", "aanzetten", "aansteken", "inschakelen", "activeer", "activeren",
        "start", "starten", "turn on", "switch on", "enable",
    ),
    "turn_off": (
        "uit", "uitzetten", "uitschakelen", "doven", "deactiveer", "deactiveren",
        "stop", "stoppen", "turn off", "switch off", "disable",
    ),
    "toggle": (
        "wissel", "toggle", "omschakelen",
    ),
    "open_cover": (
        "open", "openen", "omhoog", "omhoogdoen",
    ),
    "close_cover": (
        "sluit", "sluiten", "dicht", "dichtdoen", "omlaag", "omlaagdoen",
    ),
    "set_temperature": (
        "graden", "graad", "verhoog", "verlaag", "temperatuur naar", "zet op",
    ),
}


class DomoticaAction(BaseModel):
    """Structured representation of a parsed smart home command."""

    is_domotica: bool = Field(description="Whether the prompt represents a home automation action")
    fast_path: bool = Field(default=False, description="Whether the command qualifies for sub-50ms zero-LLM execution")
    domain: Optional[str] = Field(default=None, description="Home Assistant domain (light, switch, climate, cover)")
    service: Optional[str] = Field(default=None, description="Home Assistant service (turn_on, turn_off, etc.)")
    target_type: Optional[str] = Field(default=None, description="'area_id' or 'entity_id'")
    target_id: Optional[str] = Field(default=None, description="Exact target identifier")
    target_name: Optional[str] = Field(default=None, description="Extracted room or device name from prompt")
    service_data: Dict[str, Any] = Field(default_factory=dict, description="Parameters (brightness, temperature, etc.)")
    confidence: float = Field(default=0.0, description="Routing confidence score (0.0 - 1.0)")
    openai_tool_call: Optional[Dict[str, Any]] = Field(default=None, description="Standard OpenAI Tool Call payload for HA Assist")
    executed: bool = Field(default=False, description="Whether the action was directly executed against HA API")
    execution_result: Optional[Any] = Field(default=None, description="Result returned by Home Assistant API if executed")


class DomoticaEngine:
    """Fast-Path rule and semantic engine for resolving domotica intents."""

    def __init__(self, resolver: Optional[HAResolver] = None) -> None:
        self.resolver = resolver or HAResolver()

    def parse(self, prompt: str) -> DomoticaAction:
        """Parse natural language prompt into a structured DomoticaAction."""
        if not prompt or not prompt.strip():
            return DomoticaAction(is_domotica=False, confidence=0.0)

        clean_p = prompt.strip().lower()

        # 1. Detect Domain
        detected_domain = None
        for dom, keywords in DOMAIN_KEYWORDS.items():
            if any(k in clean_p for k in keywords):
                detected_domain = dom
                break

        # 2. Detect Action
        detected_action = None
        for action, keywords in ACTION_KEYWORDS.items():
            if any(re.search(r"\b" + re.escape(k) + r"\b", clean_p) for k in keywords):
                detected_action = action
                break

        # Normalize cover actions to standard cover domain services
        if detected_domain == "cover":
            if detected_action in ("turn_on", "open_cover"):
                detected_action = "open_cover"
            elif detected_action in ("turn_off", "close_cover"):
                detected_action = "close_cover"

        # Not a domotica command if missing either domain or action
        if not detected_domain or not detected_action:
            return DomoticaAction(is_domotica=False, confidence=0.1)

        # 3. Extract Target Name (room or entity words)
        # Remove action keywords, domain keywords, and Dutch stop-words
        target_tokens = []
        words = clean_p.split()
        skip_words = {
            "doe", "zet", "schakel", "maak", "de", "het", "een", "in", "op",
            "bij", "van", "naar", "alsjeblieft", "graag", "even", "alle", "en",
        }
        domain_kw_set = set(DOMAIN_KEYWORDS[detected_domain])
        action_kw_set = {k for keywords in ACTION_KEYWORDS.values() for k in keywords}

        for w in words:
            w_clean = re.sub(r"[^\w]", "", w)
            if not w_clean:
                continue
            if w_clean in skip_words or w_clean in domain_kw_set or w_clean in action_kw_set:
                continue
            target_tokens.append(w_clean)

        extracted_target = " ".join(target_tokens).strip()

        # 4. Extract Parameters (e.g. brightness or temperature)
        service_data: Dict[str, Any] = {}
        if detected_domain == "climate":
            # Extract number for temperature setpoint
            m_temp = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:graden|graad|celsius|°c)?", clean_p)
            if m_temp:
                try:
                    val = float(m_temp.group(1).replace(",", "."))
                    if 10.0 <= val <= 32.0:
                        service_data["temperature"] = val
                        detected_action = "set_temperature"
                except ValueError:
                    pass
        elif detected_domain == "light":
            # Extract brightness percentage
            m_pct = re.search(r"(\d+)\s*(?:%|procent)", clean_p)
            if m_pct:
                try:
                    pct = int(m_pct.group(1))
                    if 1 <= pct <= 100:
                        service_data["brightness_pct"] = pct
                except ValueError:
                    pass

        # 5. Resolve against Home Assistant Entity / Area Registry
        target_type, target_id, res_conf = self.resolver.resolve_target(
            domain=detected_domain,
            target_name=extracted_target,
        )

        overall_conf = 0.95 if target_id else 0.75

        # 6. Format Standard OpenAI Tool Call for Home Assistant Assist
        tool_call = None
        ha_fn_name = "HassTurnOn" if detected_action in ("turn_on", "open_cover") else "HassTurnOff"
        if detected_action == "set_temperature":
            ha_fn_name = "HassSetPosition" if detected_domain == "cover" else "HassClimateSetTemperature"

        tool_args: Dict[str, Any] = dict(service_data)
        if target_type == "area_id" and target_id:
            tool_args["area"] = target_id
            if detected_domain:
                tool_args["domain"] = detected_domain
        elif target_type == "entity_id" and target_id:
            tool_args["name"] = target_id

        tool_call = {
            "id": f"call_laya_{int(time.time()*1000) % 1000000}",
            "type": "function",
            "function": {
                "name": ha_fn_name,
                "arguments": tool_args,
            },
        }

        return DomoticaAction(
            is_domotica=True,
            fast_path=bool(target_id and overall_conf >= 0.80),
            domain=detected_domain,
            service=detected_action,
            target_type=target_type,
            target_id=target_id,
            target_name=extracted_target,
            service_data=service_data,
            confidence=overall_conf,
            openai_tool_call=tool_call,
            executed=False,
        )

    def route_and_execute(self, prompt: str, execute: bool = False) -> DomoticaAction:
        """Route natural language prompt and optionally execute service call against HA."""
        action = self.parse(prompt)
        if execute and action.fast_path and action.domain and action.service and action.target_type and action.target_id:
            success, res = self.resolver.execute_service(
                domain=action.domain,
                service=action.service,
                target_type=action.target_type,
                target_id=action.target_id,
                service_data=action.service_data,
            )
            action.executed = success
            action.execution_result = res
        return action

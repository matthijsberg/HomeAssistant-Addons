"""Domotica Fast-Path Decision & Execution Engine for HA System 1 (PRD v0.4.0 M1).

Provides sub-50ms deterministic local resolution of natural language smart home prompts into
Home Assistant service calls and OpenAI-compatible tool call payloads, enforcing strict
safety gates (FP-01..FP-18):
- Whole-token matching (FP-01)
- Imperatives only (FP-03, questions routed)
- Single-intent only (FP-04, multi-intent routed)
- No conditions or schedules (FP-05, routed)
- Numeric range bounds (FP-06)
- Targets never invented (FP-09)
- Sensitive domains excluded from fast path (FP-11 / D-5)
- Truthful execution status (FP-15) and localized confirmation speech (FP-16)
"""

import logging
import re
import time
from typing import Any, Dict, Optional, Set, Tuple

from pydantic import BaseModel, Field
from resolver import HAResolver

logger = logging.getLogger("ha_system_1.domotica")


# Generic domain nouns representing broad categories (stripped when extracting specific device names)
GENERIC_DOMAIN_NOUNS: Dict[str, Set[str]] = {
    "light": {"lamp", "lampen", "licht", "lichten", "verlichting"},
    "climate": {"thermostaat", "temperatuur", "klimaat"},
    "cover": {"zonwering"},
    "switch": {"schakelaar", "switch"},
    "fan": {"ventilator", "ventilatie"},
    "media_player": {"muziek", "audio", "geluid"},
}


# Canonical device domain triggers (FP-01 whole-token matching)
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
    "media_player": (
        "muziek", "speaker", "tv", "televisie", "radio", "audio", "geluid",
    ),
    "lock": (
        "slot", "deur", "poort", "deurslot", "voordeur", "achterdeur",
    ),
    "alarm_control_panel": (
        "alarm", "beveiliging",
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
    "media_pause": (
        "pauzeer", "pause", "pauze",
    ),
    "media_play": (
        "hervat", "speel af", "play", "unpause",
    ),
    "media_next_track": (
        "volgend", "volgende", "next",
    ),
    "media_previous_track": (
        "vorig", "vorige", "previous",
    ),
}

# FP-11 / D-5: Sensitive domains that MUST NEVER take the fast path
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel", "siren", "valve"}


class DomoticaAction(BaseModel):
    """Structured representation of a parsed smart home command."""

    is_domotica: bool = Field(description="Whether the prompt represents a home automation action")
    fast_path: bool = Field(default=False, description="Whether the command qualifies for sub-50ms zero-LLM execution")
    rejected_reason: Optional[str] = Field(default=None, description="Explicit RFC-compliant rejection reason code (FP-12)")
    domain: Optional[str] = Field(default=None, description="Home Assistant domain (light, switch, climate, cover)")
    service: Optional[str] = Field(default=None, description="Home Assistant service (turn_on, turn_off, etc.)")
    target_type: Optional[str] = Field(default=None, description="'area_id' or 'entity_id'")
    target_id: Optional[str] = Field(default=None, description="Exact target identifier")
    target_name: Optional[str] = Field(default=None, description="Extracted room or device name from prompt")
    service_data: Dict[str, Any] = Field(default_factory=dict, description="Parameters (brightness, temperature, etc.)")
    confidence: float = Field(default=0.0, description="Routing confidence score (0.0 - 1.0)")
    openai_tool_call: Optional[Dict[str, Any]] = Field(default=None, description="Standard OpenAI Tool Call payload for HA Assist")
    speech: Optional[str] = Field(default=None, description="Localized natural spoken confirmation text (FP-16)")
    executed: bool = Field(default=False, description="Whether the action was directly executed against HA API")
    execution_status: str = Field(default="none", description="Truthful execution status: 'none', 'confirmed', 'accepted', 'failed' (FP-15)")
    execution_result: Optional[Any] = Field(default=None, description="Result returned by Home Assistant API if executed")


class DomoticaEngine:
    """Fast-Path rule and semantic engine for resolving domotica intents with strict safety gates."""

    def __init__(
        self,
        resolver: Optional[HAResolver] = None,
        router_engine: Optional[Any] = None,
    ) -> None:
        self.resolver = resolver or HAResolver()
        self.router_engine = router_engine

    def classify_semantic_slots(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Run Laya System 1 single-forward-pass semantic slot extraction on Intel Arc XPU."""
        if not self.router_engine or not getattr(self.router_engine, "ready", False):
            return None
        router = getattr(self.router_engine, "router", None)
        if not router:
            return None
        try:
            from question_sets import get_question_set
            q_spec = get_question_set("domotica-v1")
            questions = q_spec["questions"]
            state = {"request": prompt.strip()}
            res = router.predict(state, questions)
            answers = res.get("answers", {})
            return {
                "scope": answers.get("target_scope", {}).get("choice"),
                "scope_conf": float(answers.get("target_scope", {}).get("answer_confidence", 0.0)),
                "device_type": answers.get("device_type", {}).get("choice"),
                "device_type_conf": float(answers.get("device_type", {}).get("answer_confidence", 0.0)),
                "room": answers.get("room", {}).get("choice"),
                "room_conf": float(answers.get("room", {}).get("answer_confidence", 0.0)),
            }
        except Exception as exc:
            logger.debug("Laya semantic slot extraction skipped: %s", exc)
            return None

    def parse(self, prompt: str) -> DomoticaAction:
        """Parse natural language prompt into a structured DomoticaAction enforcing FP-01..FP-18."""
        if not prompt or not prompt.strip():
            return DomoticaAction(is_domotica=False, confidence=0.0, rejected_reason="no_action")

        clean_p = prompt.strip().lower()

        # 1. Detect Domain (FP-01 whole-token matching)
        detected_domain = None
        for dom, keywords in DOMAIN_KEYWORDS.items():
            if any(re.search(r"\b" + re.escape(k) + r"\b", clean_p) for k in keywords):
                detected_domain = dom
                break

        # 2. Detect Action (FP-01 whole-token matching)
        detected_action = None
        for action, keywords in ACTION_KEYWORDS.items():
            if any(re.search(r"\b" + re.escape(k) + r"\b", clean_p) for k in keywords):
                detected_action = action
                break

        # If neither domain nor action is detected, this is NOT a domotica command
        if not detected_domain and not detected_action:
            return DomoticaAction(is_domotica=False, confidence=0.0, rejected_reason="no_action")

        # FP-03: Imperatives only. Questions never take the fast path
        if "?" in prompt:
            return DomoticaAction(is_domotica=True, fast_path=False, domain=detected_domain, rejected_reason="question")

        leading_question_words = (
            "staat", "staan", "is", "zijn", "wat", "hoe", "welke", "wanneer", "waarom",
            "are", "what", "how", "when", "why", "who", "kan", "kun",
        )
        tokens = clean_p.split()
        if tokens and tokens[0] in leading_question_words:
            return DomoticaAction(is_domotica=True, fast_path=False, domain=detected_domain, rejected_reason="question")

        # FP-04: Single intent only (reject multi-intent compound sentences)
        multi_intent_patterns = (r"\ben\b", r"\band\b", r"\bdaarna\b", r"\bthen\b", r"\bvervolgens\b")
        if any(re.search(pat, clean_p) for pat in multi_intent_patterns):
            return DomoticaAction(is_domotica=True, fast_path=False, domain=detected_domain, rejected_reason="multi_intent")

        # FP-05: Negations, conditions, schedules never take fast path
        condition_patterns = (
            r"\bniet\b", r"\bnot\b", r"\bals\b", r"\bif\b", r"\btenzij\b", r"\bunless\b",
            r"\bover\s+\d+\s+minut", r"\bom\s+\d{1,2}:\d{2}\b",
        )
        if any(re.search(pat, clean_p) for pat in condition_patterns):
            return DomoticaAction(is_domotica=True, fast_path=False, domain=detected_domain, rejected_reason="conditional")

        # FP-11 / D-5: Sensitive devices never take the fast path
        if detected_domain in SENSITIVE_DOMAINS:
            return DomoticaAction(
                is_domotica=True,
                fast_path=False,
                domain=detected_domain,
                rejected_reason="sensitive_target",
            )

        # RG-03: If mirror is older than max_staleness_minutes, disable fast path
        if self.resolver.is_stale:
            return DomoticaAction(
                is_domotica=True,
                fast_path=False,
                domain=detected_domain,
                service=detected_action,
                rejected_reason="mirror_stale",
            )

        # Normalize cover actions to standard cover domain services
        if detected_domain == "cover":
            if detected_action in ("turn_on", "open_cover"):
                detected_action = "open_cover"
            elif detected_action in ("turn_off", "close_cover"):
                detected_action = "close_cover"

        # Not a domotica command if missing either domain or action
        if not detected_domain or not detected_action:
            return DomoticaAction(is_domotica=False, confidence=0.1, rejected_reason="no_action")

        # 3. Extract Target Name (room or entity words)
        target_tokens = []
        skip_words = {
            "doe", "zet", "schakel", "maak", "de", "het", "een", "in", "op",
            "bij", "van", "naar", "alsjeblieft", "graag", "even", "alle",
            "allemaal", "al", "mijn", "onze",
        }
        generic_nouns = GENERIC_DOMAIN_NOUNS.get(detected_domain, set())
        action_kw_set = {k for keywords in ACTION_KEYWORDS.values() for k in keywords}

        for w in tokens:
            w_clean = re.sub(r"[^\w]", "", w)
            if not w_clean or any(c.isdigit() for c in w_clean):
                continue
            if w_clean in skip_words or w_clean in generic_nouns or w_clean in action_kw_set:
                continue
            target_tokens.append(w_clean)

        extracted_target = " ".join(target_tokens).strip()

        # 4. Extract Parameters & Validate Bounds (FP-06)
        service_data: Dict[str, Any] = {}
        if detected_domain == "climate":
            m_temp = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:graden|graad|celsius|°c)?", clean_p)
            if m_temp:
                try:
                    val = float(m_temp.group(1).replace(",", "."))
                    if 10.0 <= val <= 32.0:
                        service_data["temperature"] = val
                        detected_action = "set_temperature"
                    else:
                        return DomoticaAction(
                            is_domotica=True,
                            fast_path=False,
                            domain="climate",
                            rejected_reason="out_of_range",
                        )
                except ValueError:
                    pass
        elif detected_domain == "light":
            m_pct = re.search(r"(\d+)\s*(?:%|procent)", clean_p)
            if m_pct:
                try:
                    pct = int(m_pct.group(1))
                    if 1 <= pct <= 100:
                        service_data["brightness_pct"] = pct
                    else:
                        return DomoticaAction(
                            is_domotica=True,
                            fast_path=False,
                            domain="light",
                            rejected_reason="out_of_range",
                        )
                except ValueError:
                    pass

        # 5. Semantic Slot Disambiguation via Laya (Intel Arc XPU)
        slots = self.classify_semantic_slots(prompt)
        target_type = None
        target_id = None
        overall_conf = 0.95

        if slots and slots.get("room") and slots["room"] != "unspecified":
            s_room = slots["room"]
            s_scope = slots.get("scope")
            s_dev = slots.get("device_type")

            if s_scope == "entire_area":
                target_type = "area_id"
                target_id = self.resolver._area_name_map.get(s_room, s_room)
                overall_conf = max(overall_conf, slots.get("scope_conf", 0.95))
            elif s_scope == "specific_device" and s_dev and s_dev != "general_light":
                dev_kw = {
                    "led_strip": ("gordijnen", "led", "strip", "ledstrip"),
                    "spots": ("spots", "spot", "dimmer"),
                    "cover": ("jaloezie", "gordijn", "rolluik", "shutter"),
                }.get(s_dev, (s_dev,))

                candidates = self.resolver.list_entities_for_domain(detected_domain)
                for cand in candidates:
                    eid = cand["entity_id"].lower()
                    fn = str(cand.get("friendly_name") or "").lower()
                    if s_room in eid or s_room in fn:
                        if any(kw in eid or kw in fn for kw in dev_kw):
                            target_type = "entity_id"
                            target_id = cand["entity_id"]
                            extracted_target = f"{s_dev} {s_room}"
                            overall_conf = max(overall_conf, slots.get("device_type_conf", 0.95))
                            break

        # Fallback to rule-based entity resolver if slots didn't pinpoint entity
        if not target_id:
            target_type, target_id, res_conf = self.resolver.resolve_target(
                domain=detected_domain,
                target_name=extracted_target,
            )

        if not target_id:
            # FP-09: Targets are NEVER invented. Route to LLM if target is unknown.
            return DomoticaAction(
                is_domotica=True,
                fast_path=False,
                domain=detected_domain,
                service=detected_action,
                target_name=extracted_target,
                service_data=service_data,
                rejected_reason="unknown_target",
            )

        # 6. Format Standard OpenAI Tool Call for Home Assistant Assist (FP-13)
        ha_fn_name = "HassTurnOn" if detected_action in ("turn_on", "open_cover") else "HassTurnOff"
        if detected_action == "set_temperature":
            ha_fn_name = "HassClimateSetTemperature"
        elif detected_domain == "media_player":
            ha_fn_name = "HassMediaPause" if detected_action == "media_pause" else "HassMediaUnpause"

        tool_args: Dict[str, Any] = dict(service_data)
        if target_type == "area_id":
            tool_args["area"] = target_id
            if detected_domain:
                tool_args["domain"] = detected_domain
        elif target_type == "entity_id":
            tool_args["name"] = target_id

        tool_call = {
            "id": f"call_hasys1_{int(time.time()*1000) % 1000000}",
            "type": "function",
            "function": {
                "name": ha_fn_name,
                "arguments": tool_args,
            },
        }

        # 7. Localized Natural Speech Confirmation Template (FP-16)
        if target_type == "area_id":
            display_target = f"alle lampen in de {target_id}" if detected_domain == "light" else f"{detected_domain} in {target_id}"
            verb_state = "zijn" if detected_domain == "light" else "is"
        else:
            display_target = extracted_target or detected_domain
            verb_state = "is"

        if detected_action == "turn_off":
            speech = f"Oké, {display_target} {verb_state} uitgeschakeld."
        elif detected_action == "turn_on":
            speech = f"Oké, {display_target} {verb_state} aangezet."
        elif detected_action == "set_temperature":
            speech = f"Oké, de temperatuur is ingesteld op {service_data.get('temperature', '')} graden."
        elif detected_action in ("open_cover", "close_cover"):
            speech = f"Oké, {display_target} is {'geopend' if detected_action == 'open_cover' else 'gesloten'}."
        else:
            speech = f"Oké, {detected_action} uitgevoerd voor {display_target}."

        return DomoticaAction(
            is_domotica=True,
            fast_path=True,
            domain=detected_domain,
            service=detected_action,
            target_type=target_type,
            target_id=target_id,
            target_name=extracted_target,
            service_data=service_data,
            confidence=overall_conf,
            openai_tool_call=tool_call,
            speech=speech,
            executed=False,
            execution_status="none",
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
            action.execution_status = "confirmed" if success else "failed"
        return action

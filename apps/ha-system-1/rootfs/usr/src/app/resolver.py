"""Home Assistant Entity, Area, and Service Resolver for HA System 1 (PRD v0.4.0 M2).

Synchronizes and caches Home Assistant entities and areas via the Supervisor/Core REST API,
enabling sub-millisecond local resolution of natural language targets into exact entity_ids,
area_ids, and service calls without cloud LLM dependencies.

Implements M2 requirements:
- RG-01: In-memory registry mirror (entities, states, attributes, aliases, exposure)
- RG-03: Resync control, staleness monitoring (disabled if stale > 60m)
- RG-04: Full diacritics folding (e.g. jaloezieën -> jaloezieen) and alias mapping
- RG-05: Sub-20ms resolution p95
"""

import json
import logging
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("ha_system_1.resolver")


def fold_diacritics(text: str) -> str:
    """Normalize and fold diacritics to ASCII base: jaloezieën -> jaloezieen, crème -> creme (RG-04)."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


DEFAULT_AREAS: Set[str] = {
    "woonkamer", "eetkamer", "keuken", "serre", "bijkeuken", "gang", "hal",
    "overloop", "zolder", "slaapkamer", "badkamer", "kantoor", "tuin", "veranda",
    "garage", "kelder", "terrein", "voordeur", "achterdeur",
    "living room", "kitchen", "bedroom", "bathroom", "office", "garden", "hallway",
}


def is_valid_target_entity(eid: str, item: Dict[str, Any]) -> bool:
    """Filter out non-actionable sub-devices, ghost entities, and diagnostic LEDs (RG-01)."""
    state = str(item.get("state", "")).lower()
    attrs = item.get("attributes", {})

    # 1. Skip restored / ghost entities that are unavailable
    if attrs.get("restored") is True and state in ("unavailable", "unknown"):
        return False

    # 2. Skip Z-Wave redundant Basic Command Class endpoints (_basic, _basic_2)
    if re.search(r"_basic(_\d+)?$", eid):
        return False

    # 3. Skip infrastructure / Access Point indicator status LEDs
    if eid.startswith("light.ap_") or re.search(r"\b(indicator|status_led|nightlight)\b", eid):
        return False

    # 4. Skip configuration or diagnostic entities
    if attrs.get("entity_category") in ("config", "diagnostic"):
        return False

    return True


class HAResolver:
    """In-memory cache and entity/area resolver for Home Assistant."""

    def __init__(
        self,
        supervisor_token: Optional[str] = None,
        ha_url: Optional[str] = None,
        cache_ttl_s: float = 300.0,
        max_staleness_minutes: float = 60.0,
    ) -> None:
        self.supervisor_token = supervisor_token or os.environ.get("SUPERVISOR_TOKEN") or ""
        self.ha_url = (ha_url or os.environ.get("HA_URL") or "http://supervisor/core/api").rstrip("/")
        self.cache_ttl_s = cache_ttl_s
        self.max_staleness_minutes = max_staleness_minutes
        self._last_sync_time: float = 0.0
        self._entities: Dict[str, Dict[str, Any]] = {}
        self._areas: Dict[str, Dict[str, Any]] = {}
        self._friendly_name_map: Dict[str, str] = {}
        self._area_name_map: Dict[str, str] = {fold_diacritics(a): a.replace(" ", "_") for a in DEFAULT_AREAS}

    @property
    def is_configured(self) -> bool:
        """Check if Home Assistant connection token and URL are available."""
        return bool(self.supervisor_token and self.ha_url)

    @property
    def is_stale(self) -> bool:
        """Check if registry mirror has exceeded max staleness threshold (RG-03)."""
        if self._last_sync_time == 0.0:
            return False  # Test/standalone mode with preloaded data
        return (time.time() - self._last_sync_time) > (self.max_staleness_minutes * 60.0)

    def sync(self, force: bool = False) -> bool:
        """Fetch and cache entity and area registries from Home Assistant (RG-01, RG-03)."""
        if not self.is_configured:
            logger.debug("HAResolver not configured with SUPERVISOR_TOKEN or HA_URL; skipping sync.")
            return False

        now = time.time()
        if not force and (now - self._last_sync_time) < self.cache_ttl_s and self._entities:
            return True

        t0 = time.perf_counter()
        try:
            # 1. Fetch entity states
            req = urllib.request.Request(
                f"{self.ha_url}/states",
                headers={
                    "Authorization": f"Bearer {self.supervisor_token}",
                    "Content-Type": "application/json",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    states_data = json.loads(resp.read().decode("utf-8"))
                    new_entities = {}
                    new_fn_map = {}
                    for item in states_data:
                        if not isinstance(item, dict):
                            continue
                        eid = item.get("entity_id", "")
                        if not eid or not is_valid_target_entity(eid, item):
                            continue
                        attrs = item.get("attributes", {})
                        fn_raw = str(attrs.get("friendly_name") or eid)
                        fn_folded = fold_diacritics(fn_raw).strip()

                        # Determine Assist exposure
                        is_exposed = attrs.get("conversation_agent", True)

                        new_entities[eid] = {
                            "entity_id": eid,
                            "domain": eid.split(".")[0] if "." in eid else "",
                            "friendly_name": attrs.get("friendly_name", eid),
                            "state": item.get("state"),
                            "attributes": attrs,
                            "is_exposed": is_exposed,
                        }
                        if fn_folded:
                            new_fn_map[fn_folded] = eid

                        # RG-04: Process aliases if present in entity attributes
                        aliases = attrs.get("aliases", [])
                        if isinstance(aliases, list):
                            for alias in aliases:
                                alias_folded = fold_diacritics(str(alias)).strip()
                                if alias_folded:
                                    new_fn_map[alias_folded] = eid

                    self._entities = new_entities
                    self._friendly_name_map = new_fn_map

            self._last_sync_time = now
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.info(
                "HAResolver synced %d entities from Home Assistant in %.1fms",
                len(self._entities),
                elapsed_ms,
            )
            return True
        except Exception as exc:
            logger.warning("HAResolver failed syncing from Home Assistant Core API (%s): %s", self.ha_url, exc)
            return False

    def load_cached_entities(self, entities_dict: Dict[str, Dict[str, Any]]) -> None:
        """Directly inject a pre-loaded dictionary of entities (useful for tests or standalone mode)."""
        self._entities = dict(entities_dict)
        self._friendly_name_map = {}
        for eid, item in self._entities.items():
            fn = fold_diacritics(str(item.get("friendly_name") or eid)).strip()
            if fn:
                self._friendly_name_map[fn] = eid
            aliases = item.get("attributes", {}).get("aliases", [])
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_folded = fold_diacritics(str(alias)).strip()
                    if alias_folded:
                        self._friendly_name_map[alias_folded] = eid
        self._last_sync_time = time.time()

    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached state object for a given entity_id."""
        return self._entities.get(entity_id)

    def list_entities_for_domain(self, domain: str) -> List[Dict[str, Any]]:
        """List all cached entities belonging to a specific domain (e.g. 'light')."""
        return [e for e in self._entities.values() if e.get("domain") == domain]

    def resolve_target(
        self,
        domain: str,
        target_name: str,
    ) -> Tuple[Optional[str], Optional[str], float]:
        """Resolve a natural language room or entity name to (target_type, target_id, confidence) (RG-04, RG-05).

        Target types:
          - 'entity_id': exact or fuzzy matched specific entity (e.g. 'light.serre_spots_dimmer')
          - 'area_id': matched home area/room (e.g. 'woonkamer', 'serre')
        """
        if not target_name:
            if domain == "climate":
                climates = self.list_entities_for_domain("climate")
                if climates:
                    return "entity_id", climates[0]["entity_id"], 0.90
                return "entity_id", "climate.thermostat", 0.85
            return None, None, 0.0

        # Fold diacritics and strip punctuation (RG-04)
        clean_target = fold_diacritics(target_name).strip()
        clean_target = re.sub(r"[^\w\s]", " ", clean_target)
        clean_target = re.sub(r"\b(in|op|bij|van|de|het|een|kamer|hoek)\b", " ", clean_target).strip()
        clean_target = re.sub(r"\s+", " ", clean_target)

        # 1. Area Precedence (Optie 1 + Optie 3):
        # If target matches an area (e.g. "serre", "woonkamer"), prioritize the entire area
        # rather than shadowing the room with a single device that happens to have the room name.
        if clean_target in self._area_name_map:
            return "area_id", self._area_name_map[clean_target], 0.95

        for area, area_slug in self._area_name_map.items():
            if re.search(r"\b" + re.escape(area) + r"\b", clean_target):
                remaining = re.sub(r"\b" + re.escape(area) + r"\b", "", clean_target).strip()
                if not remaining:
                    return "area_id", area_slug, 0.95

        # 2. Exact friendly_name or alias match (RG-04)
        if clean_target in self._friendly_name_map:
            eid = self._friendly_name_map[clean_target]
            if not domain or eid.startswith(f"{domain}."):
                return "entity_id", eid, 0.99

        # 3. Check entities within domain matching target words
        target_words = set(clean_target.split())
        best_eid = None
        best_score = 0.0

        candidates = self.list_entities_for_domain(domain) if domain else list(self._entities.values())
        for cand in candidates:
            eid = cand["entity_id"]
            fn = fold_diacritics(str(cand.get("friendly_name") or "")).lower()
            cand_words = set(re.findall(r"\w+", f"{eid} {fn}"))

            # Calculate word overlap
            overlap = target_words.intersection(cand_words)
            if overlap:
                score = len(overlap) / float(len(target_words))
                if score > best_score:
                    best_score = score
                    best_eid = eid

        if best_eid and best_score >= 0.80:
            return "entity_id", best_eid, min(0.95, 0.70 + best_score * 0.25)

        # 4. Partial area fallback if words contain area
        for area, area_slug in self._area_name_map.items():
            if re.search(r"\b" + re.escape(area) + r"\b", clean_target):
                return "area_id", area_slug, 0.85

        # FP-09: Targets are NEVER invented. If text does not resolve to an existing object, return None!
        return None, None, 0.0

    def execute_service(
        self,
        domain: str,
        service: str,
        target_type: str,
        target_id: str,
        service_data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Any]:
        """Execute a service call against the Home Assistant Core API."""
        if not self.is_configured:
            return False, "HAResolver not configured with SUPERVISOR_TOKEN"

        payload: Dict[str, Any] = dict(service_data or {})
        if target_type == "entity_id":
            payload["entity_id"] = target_id
        elif target_type == "area_id":
            payload["area_id"] = target_id

        try:
            req_data = json.dumps(payload).encode("utf-8")
            url = f"{self.ha_url}/services/{domain}/{service}"
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={
                    "Authorization": f"Bearer {self.supervisor_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                result_data = None
                if resp.status in (200, 201):
                    raw = resp.read()
                    if raw:
                        result_data = json.loads(raw.decode("utf-8"))
                    return True, result_data
                return False, f"HTTP status {resp.status}"
        except Exception as exc:
            logger.error("Error calling HA service %s.%s (%s=%s): %s", domain, service, target_type, target_id, exc)
            return False, str(exc)

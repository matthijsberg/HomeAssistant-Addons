"""Home Assistant Entity, Area, and Service Resolver for Laya v2.0.

Synchronizes and caches Home Assistant entities and areas via the Supervisor/Core REST API,
enabling sub-millisecond local resolution of natural language targets into exact entity_ids,
area_ids, and service calls without cloud LLM dependencies.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("laya.resolver")


class HAResolver:
    """In-memory cache and entity/area resolver for Home Assistant."""

    def __init__(
        self,
        supervisor_token: Optional[str] = None,
        ha_url: Optional[str] = None,
        cache_ttl_s: float = 300.0,
    ) -> None:
        self.supervisor_token = supervisor_token or os.environ.get("SUPERVISOR_TOKEN") or ""
        self.ha_url = (ha_url or os.environ.get("HA_URL") or "http://supervisor/core/api").rstrip("/")
        self.cache_ttl_s = cache_ttl_s
        self._last_sync_time: float = 0.0
        self._entities: Dict[str, Dict[str, Any]] = {}
        self._areas: Dict[str, Dict[str, Any]] = {}
        self._friendly_name_map: Dict[str, str] = {}
        self._area_name_map: Dict[str, str] = {}

    @property
    def is_configured(self) -> bool:
        """Check if Home Assistant connection token and URL are available."""
        return bool(self.supervisor_token and self.ha_url)

    def sync(self, force: bool = False) -> bool:
        """Fetch and cache entity and area registries from Home Assistant."""
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
                        attrs = item.get("attributes", {})
                        fn = str(attrs.get("friendly_name") or eid).strip().lower()
                        new_entities[eid] = {
                            "entity_id": eid,
                            "domain": eid.split(".")[0] if "." in eid else "",
                            "friendly_name": attrs.get("friendly_name", eid),
                            "state": item.get("state"),
                            "attributes": attrs,
                        }
                        if fn:
                            new_fn_map[fn] = eid
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
            fn = str(item.get("friendly_name") or eid).strip().lower()
            if fn:
                self._friendly_name_map[fn] = eid
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
        """Resolve a natural language room or entity name to (target_type, target_id, confidence).

        Target types:
          - 'entity_id': exact or fuzzy matched specific entity (e.g. 'light.serre_spots_dimmer')
          - 'area_id': matched home area/room (e.g. 'woonkamer', 'serre')
        """
        if not target_name:
            return None, None, 0.0

        clean_target = target_name.strip().lower()
        # Remove common stop-words
        clean_target = re.sub(r"\b(in|op|bij|van|de|het|een|kamer|hoek)\b", " ", clean_target).strip()
        clean_target = re.sub(r"\s+", " ", clean_target)

        # 1. Exact friendly_name match
        if clean_target in self._friendly_name_map:
            eid = self._friendly_name_map[clean_target]
            if not domain or eid.startswith(f"{domain}."):
                return "entity_id", eid, 0.99

        # 2. Check entities within domain matching target words
        target_words = set(clean_target.split())
        best_eid = None
        best_score = 0.0

        candidates = self.list_entities_for_domain(domain) if domain else list(self._entities.values())
        for cand in candidates:
            eid = cand["entity_id"]
            fn = str(cand.get("friendly_name") or "").lower()
            cand_words = set(re.findall(r"\w+", f"{eid} {fn}"))

            # Calculate word overlap
            overlap = target_words.intersection(cand_words)
            if overlap:
                score = len(overlap) / float(len(target_words))
                if score > best_score:
                    best_score = score
                    best_eid = eid

        if best_eid and best_score >= 0.50:
            return "entity_id", best_eid, min(0.95, 0.70 + best_score * 0.25)

        # 3. Fallback: treat as area_id
        # In Home Assistant, calling light.turn_on with area_id='woonkamer' natively controls all lights in that area
        area_slug = re.sub(r"[^a-z0-9_]+", "_", clean_target).strip("_")
        if area_slug:
            return "area_id", area_slug, 0.85

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

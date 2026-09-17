"""
Models: Mode Catalog Loader
===========================
Decouples UI presentation (labels, colors, tailwind classes, CSS patterns)
from the mathematical planning engine and canonical contracts.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional

_DEFAULT_CATALOG_PATH = Path(__file__).parent.parent / "config" / "mode_catalog.json"
_CACHED_CATALOG: Optional[Dict[str, Any]] = None


def load_mode_catalog(catalog_path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads and caches the mode catalog."""
    global _CACHED_CATALOG
    if _CACHED_CATALOG is not None and catalog_path is None:
        return _CACHED_CATALOG

    path = catalog_path or _DEFAULT_CATALOG_PATH
    if not path.exists():
        # Fallback for when running from addons directory
        alt_path = Path("/config/addons/open-hems/config/mode_catalog.json")
        if alt_path.exists():
            path = alt_path

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            _CACHED_CATALOG = json.load(f)
            return _CACHED_CATALOG or {}

    return {"archetypes": {}}


def get_mode_meta(mode_code: str, archetype: str = "thermal_buffer") -> Dict[str, Any]:
    """
    Returns presentation metadata for a given mode code.
    Guarantees a safe fallback dictionary if mode_code is not found.
    """
    catalog = load_mode_catalog()
    arch = catalog.get("archetypes", {}).get(archetype, {})
    if mode_code in arch:
        return arch[mode_code]

    # Search all archetypes
    for a_name, a_dict in catalog.get("archetypes", {}).items():
        if mode_code in a_dict:
            return a_dict[mode_code]

    fallback_color = catalog.get("archetypes", {}).get("thermal_buffer", {}).get("normal", {}).get("color_hex", "")
    return {
        "code": mode_code,
        "label": mode_code.replace("_", " ").title(),
        "color_hex": fallback_color,
        "tailwind_text": "text-slate-400",
        "css_pattern": "none",
        "description": f"Mode: {mode_code}"
    }

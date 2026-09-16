"""
Layer 1 / API: Lightweight Internationalization (i18n) Engine
==============================================================
Loads hierarchical JSON locale files (locales/{lang}.json) and provides
string translation with named variable interpolation and graceful fallback.

Usage:
    from api.i18n import t, get_supported_languages

    text = t("dhw.decision_title", lang="en")
    msg = t("dhw.night_plan_desc", lang="nl", temp=50.0, start="04:45", end="05:45")
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

_CACHE: Dict[str, Dict[str, Any]] = {}
DEFAULT_LANG = "nl"
FALLBACK_LANG = "nl"
SUPPORTED_LANGUAGES = ["nl", "en"]


def load_locale(lang: str) -> Dict[str, Any]:
    """Loads and caches the JSON dictionary for a language."""
    global _CACHE
    if lang in _CACHE:
        return _CACHE[lang]

    locale_path = LOCALES_DIR / f"{lang}.json"
    if not locale_path.exists():
        if lang != FALLBACK_LANG:
            return load_locale(FALLBACK_LANG)
        return {}

    try:
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        _CACHE[lang] = data
        return data
    except Exception as e:
        print(f"[i18n] Error loading {locale_path}: {e}")
        return {}


def get_supported_languages() -> list:
    """Returns list of available language codes found in locales/ directory."""
    if not LOCALES_DIR.exists():
        return [DEFAULT_LANG]
    langs = []
    for f in LOCALES_DIR.glob("*.json"):
        langs.append(f.stem)
    return sorted(langs) if langs else [DEFAULT_LANG]


def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """
    Translates a dot-notated key with string interpolation.
    Example:
        t("dhw.box_title", lang="en")
        t("dhw.night_run_desc", lang="en", start="04:45", end="05:45", temp=50.0)
    """
    active_lang = lang or DEFAULT_LANG
    catalog = load_locale(active_lang)

    # 1. Resolve dot notation (e.g. "dhw.box_title")
    parts = key.split(".")
    val = catalog
    for p in parts:
        if isinstance(val, dict) and p in val:
            val = val[p]
        else:
            val = None
            break

    # 2. Fallback to default language if missing in requested language
    if val is None and active_lang != FALLBACK_LANG:
        fallback_catalog = load_locale(FALLBACK_LANG)
        val = fallback_catalog
        for p in parts:
            if isinstance(val, dict) and p in val:
                val = val[p]
            else:
                val = None
                break

    # 3. If still missing, return the key itself as safe fallback
    if val is None:
        return key

    # 4. Interpolate variables (e.g. {temp}, {start}, {end})
    if isinstance(val, str) and kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, ValueError):
            return val

    return str(val)


def localize_dhw_decision(decision: Dict[str, Any], lang: str = "nl") -> Dict[str, Any]:
    """
    Dynamically renders localized texts and badge HTML for DHW decision card.
    Supports any language loaded from locales/*.json.
    """
    if not decision or "template_params" not in decision:
        return decision

    params = decision["template_params"]
    d_type = params.get("decision_type", "standby")
    active_lang = lang if lang in get_supported_languages() else DEFAULT_LANG

    localized = dict(decision)
    localized["box_title"] = t("dhw.box_title", lang=active_lang)
    localized["comfort_card_title"] = t("dhw.reasoning_title", lang=active_lang)

    if d_type == "night_run":
        localized["comfort_text"] = t("dhw.night_reasoning_text", lang=active_lang, **params)
        localized["bullet_1"] = t("dhw.bullet_vat_spits_night", lang=active_lang, **params)
        localized["bullet_2"] = t("dhw.bullet_action_night", lang=active_lang, **params)
        badge_text = t("dhw.badge_night_planned", lang=active_lang, **params)
        localized["badge_html"] = (
            f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold '
            f'bg-amber-950/80 text-amber-300 border border-amber-800/80">'
            f'<span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span> {badge_text}</span>'
        )
    elif d_type == "day_run":
        localized["comfort_text"] = t("dhw.day_reasoning_text", lang=active_lang, **params)
        localized["bullet_1"] = t("dhw.bullet_vat_spits_risk", lang=active_lang, **params)
        localized["bullet_2"] = t("dhw.bullet_action_day", lang=active_lang, **params)
        badge_text = t("dhw.badge_optimal_planned", lang=active_lang, temp=params.get("target_temp"))
        localized["badge_html"] = (
            f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold '
            f'bg-emerald-950/80 text-emerald-300 border border-emerald-800/80">'
            f'<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> {badge_text}</span>'
        )
    else:
        localized["comfort_text"] = t("dhw.standby_reasoning_text", lang=active_lang, **params)
        localized["bullet_1"] = t("dhw.bullet_vat_safe", lang=active_lang, **params)
        localized["bullet_2"] = t("dhw.bullet_action_standby", lang=active_lang)
        badge_text = t("dhw.badge_standby", lang=active_lang)
        localized["badge_html"] = (
            f'<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold '
            f'bg-slate-900 text-slate-300 border border-slate-700">'
            f'<span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span> {badge_text}</span>'
        )

    return localized


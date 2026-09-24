"""
Dynamic Price Peak Detection & Chart Range Extraction
=====================================================
Layer 3 Business & Scheduling Logic:
  - Dynamic price peak detection on 15-minute resolution with winter comfort safeguards.
  - Spitsblok (forced_off) range extraction for chart overlays.
  - Active heating (dhw / space_heating) range extraction for chart overlays.
"""

import math
from datetime import timedelta
from typing import List, Dict, Any, Tuple, Optional


def calc_percentile(data: List[float], p: float) -> float:
    """Calculates percentile from list of floats (pure python, deterministic)."""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    return float(s[int(f)] * (c - k) + s[int(c)] * (k - f))


def detect_dynamic_price_peaks(
    timeline_items: List[Dict[str, Any]],
    step_mins: int = 15,
    max_lockout_mins: int = 120,
    past_continuous_lockout_mins: int = 0,
    mins_since_last_lockout: int = 999
) -> Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """
    Verfijnde Dynamische Spitsdetector op kwartierbasis met Winter Comfort Safeguard:
      1. Micro-piek filter: Negeert rimpels korter dan 30 min (filtert pendelstops weg).
      2. Piek-Kam Prominentie: Een continue harde blokkade wordt gecapt op maximaal 150 min (2,5 uur)
         op de absolute top van de prijsgolf om afkoeling van de vloer/woning in de winter te voorkomen.
      3. Flank-degradatie: Schouder-uren buiten de 2,5u top-kam worden 'Economisch Blokadvies' (⚠️),
         waarin de warmtepomp op minimale modulatie (950W) mag doorpruttelen indien nodig.
      4. Strikte Spitsuren Afbakening: Alleen echte Ochtendspits (06:00-10:00) en Avondspits (17:00-22:00)
         mogen een harde blokkade vormen. Nachtelijke uren (22:00-06:00) worden nooit hard geblokkeerd.
      5. Historie- en Dwell-bewust: Houdt rekening met reeds verstreken blokkadeduren en dwingt minimaal
         120 min hersteltijd af tussen twee opeenvolgende blokkades.
    """
    if not timeline_items:
        return [], {}

    prices = [float(it.get("price", 0.0)) for it in timeline_items]
    p_med = calc_percentile(prices, 50)
    p75 = calc_percentile(prices, 75)
    p85 = calc_percentile(prices, 85)

    # 1. Kandidaat slots: moet significant boven mediaan liggen en in hoogste kwartiel
    is_cand = []
    for it in timeline_items:
        p = float(it.get("price", 0.0))
        cand = (p >= p75) and ((p - p_med) >= 0.035)
        is_cand.append(cand)

    # 2. Overbrug 1-slot dipjes binnen een bredere piek
    n = len(is_cand)
    bridged = list(is_cand)
    p70 = calc_percentile(prices, 70)
    for i in range(1, n - 1):
        if not bridged[i] and bridged[i-1] and bridged[i+1]:
            if float(timeline_items[i].get("price", 0.0)) >= p70:
                bridged[i] = True

    # 3. Cluster aaneengesloten pieken
    raw_events = []
    in_event = False
    start_idx = 0
    for i in range(n):
        if bridged[i] and not in_event:
            in_event = True
            start_idx = i
        elif not bridged[i] and in_event:
            in_event = False
            raw_events.append((start_idx, i - 1))
    if in_event:
        raw_events.append((start_idx, n - 1))

    # 3b. Macro-clustering: overbrug korte rimpels/gaten (gap <= 4 slots / 60 min) binnen hetzelfde spitsvenster
    # Voorkomt gefragmenteerde "uit / aan / uit" cycli en garandeert één coherente spitsblokkade per dagdeel.
    events = []
    for ev in raw_events:
        if not events:
            events.append(ev)
        else:
            prev_start, prev_end = events[-1]
            cur_start, cur_end = ev
            gap = cur_start - prev_end - 1
            h_prev = timeline_items[prev_start]["dt"].hour
            h_cur = timeline_items[cur_start]["dt"].hour
            same_window = (
                ((6 <= h_prev < 11) and (6 <= h_cur < 11)) or
                ((16 <= h_prev < 22) and (16 <= h_cur < 22))
            )
            if gap <= 4 and same_window:
                events[-1] = (prev_start, cur_end)
            else:
                events.append(ev)

    # 4. Formuleer Peak Events met Duur-Cap en Crest Focus
    peak_objects = []
    slot_lockout_map = {}
    last_hard_end_slot = -999
    if past_continuous_lockout_mins > 0:
        last_hard_end_slot = -1
    elif mins_since_last_lockout < 120:
        last_hard_end_slot = -int(mins_since_last_lockout / step_mins)

    for s_idx, e_idx in events:
        cluster_len = e_idx - s_idx + 1
        dur_mins = cluster_len * step_mins

        # Micro-peak filter: negeer pieken korter dan 30 min (2 kwartieren)
        if dur_mins < 30:
            continue

        cluster_prices = [float(timeline_items[k].get("price", 0.0)) for k in range(s_idx, e_idx + 1)]
        max_p = max(cluster_prices)
        avg_p = sum(cluster_prices) / cluster_len
        dt_start = timeline_items[s_idx]["dt"]
        dt_end = timeline_items[e_idx]["dt"] + timedelta(minutes=step_mins)

        h = dt_start.hour
        # Strikte spitsuren: Ochtendspits (06:00-10:00) en Avondspits (17:00-22:00).
        # Buiten deze vensters (22:00-06:00 en 10:00-17:00) NOOIT harde blokkade!
        is_spits_window = (6 <= h < 10) or (17 <= h < 22)
        name = "Ochtendspits" if 6 <= h < 11 else ("Middagpiek" if 11 <= h < 17 else ("Avondspits" if 17 <= h < 22 else "Nachttarief"))

        # Bepaal of deze piek een harde blokkade rechtvaardigt
        is_hard_cluster = (max_p >= p85) and ((max_p - p_med) >= 0.050) and is_spits_window

        # Dwell time safeguard: als de vorige blokkade korter dan 120 minuten geleden eindigde,
        # mag er niet direct opnieuw een harde blokkade starten (afkoelbeveiliging)
        if s_idx == 0 and mins_since_last_lockout < 120 and past_continuous_lockout_mins == 0:
            is_hard_cluster = False

        # Cumulatieve duur-cap met verleden: als dit cluster aansluit op een al lopende blokkade
        effective_max_mins = max_lockout_mins
        if s_idx == 0 and past_continuous_lockout_mins > 0:
            effective_max_mins = max(0, max_lockout_mins - past_continuous_lockout_mins)
            if effective_max_mins < 30:  # minder dan 30 min resterend = direct opheffen
                is_hard_cluster = False

        max_slots_cap = max(0, effective_max_mins // step_mins)

        # Vind de top-kam binnen het cluster
        if is_hard_cluster and max_slots_cap > 0 and cluster_len > max_slots_cap:
            best_sub_start = s_idx
            best_sub_avg = -1.0
            for w_start in range(s_idx, e_idx - max_slots_cap + 2):
                w_end = w_start + max_slots_cap
                sub_avg = sum(float(timeline_items[k].get("price", 0.0)) for k in range(w_start, w_end)) / max_slots_cap
                if sub_avg > best_sub_avg:
                    best_sub_avg = sub_avg
                    best_sub_start = w_start
            hard_start_idx = best_sub_start
            hard_end_idx = best_sub_start + max_slots_cap - 1
        else:
            hard_start_idx = s_idx if (is_hard_cluster and max_slots_cap > 0) else -1
            hard_end_idx = e_idx if (is_hard_cluster and max_slots_cap > 0) else -1

        # Inter-event dwell time safeguard: dwing minimaal 120 min hersteltijd af tussen harde blokkades
        if is_hard_cluster and hard_start_idx >= 0:
            if (hard_start_idx - last_hard_end_slot) * step_mins < 120:
                is_hard_cluster = False
                hard_start_idx = -1
                hard_end_idx = -1
            else:
                last_hard_end_slot = hard_end_idx

        h_start_lbl = timeline_items[hard_start_idx]["dt"].strftime("%H:%M") if hard_start_idx >= 0 else None
        h_end_lbl = (timeline_items[hard_end_idx]["dt"] + timedelta(minutes=step_mins)).strftime("%H:%M") if hard_end_idx >= 0 else None
        h_dur_mins = (hard_end_idx - hard_start_idx + 1) * step_mins if hard_start_idx >= 0 else 0

        peak_obj = {
            "start_idx": s_idx,
            "end_idx": e_idx,
            "start_time": dt_start.strftime("%H:%M"),
            "end_time": dt_end.strftime("%H:%M"),
            "duration_mins": dur_mins,
            "slots_count": cluster_len,
            "name": name,
            "severity": "HARD_LOCKOUT" if (is_hard_cluster and hard_start_idx >= 0) else "SOFT_ADVICE",
            "severity_label": "Harde Spitsblokkade 🔒" if (is_hard_cluster and hard_start_idx >= 0) else "Economisch Blokadvies ⚠️",
            "is_hard_lockout": (is_hard_cluster and hard_start_idx >= 0),
            "hard_start_time": h_start_lbl,
            "hard_end_time": h_end_lbl,
            "hard_duration_mins": h_dur_mins,
            "max_price": round(max_p, 4),
            "avg_price": round(avg_p, 4),
            "delta_median": round(max_p - p_med, 4)
        }
        peak_objects.append(peak_obj)

        for k in range(s_idx, e_idx + 1):
            is_hard = (hard_start_idx <= k <= hard_end_idx and hard_start_idx >= 0)
            slot_entry = dict(peak_obj)
            slot_entry["is_hard_lockout"] = is_hard
            slot_entry["severity"] = "HARD_LOCKOUT" if is_hard else "SOFT_ADVICE"
            slot_entry["severity_label"] = "Harde Spitsblokkade 🔒" if is_hard else "Economisch Blokadvies ⚠️"
            slot_entry["peak_obj"] = peak_obj
            slot_lockout_map[k] = slot_entry

    return peak_objects, slot_lockout_map


def extract_plan_spitsblok_ranges(slots: List[Any], history_count: int = 0, is_15m: bool = True) -> List[Dict[str, Any]]:
    """
    Unified Single Source of Truth builder for chart spitsblok overlays.
    Extracts contiguous hard lockout windows (mode_code == 'forced_off') directly from canonical plan slots.
    Guarantees 100% mathematical and visual alignment across ALL charts.
    """
    ranges = []
    in_block = False
    start_idx = 0
    n = len(slots) if is_15m else len(slots) // 4
    for i in range(n):
        if is_15m:
            is_locked = (slots[i].mode_code == "forced_off") or getattr(slots[i], "is_lockout", False)
        else:
            is_locked = any(
                (slots[i * 4 + k].mode_code == "forced_off" or getattr(slots[i * 4 + k], "is_lockout", False))
                for k in range(4) if (i * 4 + k) < len(slots)
            )
        if is_locked and not in_block:
            in_block = True
            start_idx = i
        elif not is_locked and in_block:
            in_block = False
            s_lbl = slots[start_idx * 4 if not is_15m else start_idx].time_label
            e_lbl = slots[(i - 1) * 4 if not is_15m else (i - 1)].time_label
            ranges.append({
                "start_idx": history_count + start_idx,
                "end_idx": history_count + i - 1,
                "start_label": s_lbl,
                "end_label": e_lbl,
                "name": "SPITSBLOK"
            })
    if in_block:
        s_lbl = slots[start_idx * 4 if not is_15m else start_idx].time_label
        e_lbl = slots[-1].time_label
        ranges.append({
            "start_idx": history_count + start_idx,
            "end_idx": history_count + n - 1,
            "start_label": s_lbl,
            "end_label": e_lbl,
            "name": "SPITSBLOK"
        })
    return ranges


def extract_plan_heating_ranges(slots: List[Any], history_count: int = 0, is_15m: bool = True, domain: str = "both") -> List[Dict[str, Any]]:
    """
    Unified Single Source of Truth builder for chart active heating overlays.
    Extracts contiguous heating windows (dhw_kw > 0 or heating_kw > 0) directly from canonical plan slots.
    """
    ranges = []
    in_block = False
    start_idx = 0
    n = len(slots) if is_15m else len(slots) // 4
    for i in range(n):
        if is_15m:
            s = slots[i]
            if domain == "dhw":
                is_h = getattr(s, "dhw_kw", 0.0) > 0.05
            elif domain == "space_heating":
                is_h = getattr(s, "heating_kw", 0.0) > 0.05
            else:
                is_h = (getattr(s, "dhw_kw", 0.0) > 0.05) or (getattr(s, "heating_kw", 0.0) > 0.05)
        else:
            if domain == "dhw":
                is_h = any(getattr(slots[i * 4 + k], "dhw_kw", 0.0) > 0.05 for k in range(4) if (i * 4 + k) < len(slots))
            elif domain == "space_heating":
                is_h = any(getattr(slots[i * 4 + k], "heating_kw", 0.0) > 0.05 for k in range(4) if (i * 4 + k) < len(slots))
            else:
                is_h = any((getattr(slots[i * 4 + k], "dhw_kw", 0.0) > 0.05 or getattr(slots[i * 4 + k], "heating_kw", 0.0) > 0.05) for k in range(4) if (i * 4 + k) < len(slots))

            # In 1-hour mode, enforce strict overlay non-overlap: Spitsblok (forced_off) takes priority over heating
            is_locked_hour = any(
                (slots[i * 4 + k].mode_code == "forced_off" or getattr(slots[i * 4 + k], "is_lockout", False))
                for k in range(4) if (i * 4 + k) < len(slots)
            )
            if is_locked_hour:
                is_h = False

        if is_h and not in_block:
            in_block = True
            start_idx = i
        elif not is_h and in_block:
            in_block = False
            s_lbl = slots[start_idx * 4 if not is_15m else start_idx].time_label
            e_lbl = slots[(i - 1) * 4 if not is_15m else (i - 1)].time_label
            ranges.append({
                "start_idx": history_count + start_idx,
                "end_idx": history_count + i - 1,
                "start_label": s_lbl,
                "end_label": e_lbl,
                "name": "VERWARMT"
            })
    if in_block:
        s_lbl = slots[start_idx * 4 if not is_15m else start_idx].time_label
        e_lbl = slots[-1].time_label
        ranges.append({
            "start_idx": history_count + start_idx,
            "end_idx": history_count + n - 1,
            "start_label": s_lbl,
            "end_label": e_lbl,
            "name": "VERWARMT"
        })
    return ranges

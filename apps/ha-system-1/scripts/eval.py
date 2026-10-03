#!/usr/bin/env python3
"""Evaluation Harness for HA System 1 / Laya Decision Engine (PRD EV-01..04).

Measures routing accuracy, latency, and regression metrics against synthetic or household datasets.
Reports accuracy both with and without overrides, providing actionable failure diagnostics.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Ensure app root is on python path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "rootfs" / "usr" / "src" / "app"))

from rootfs.usr.src.app.config import AppConfig  # noqa: E402
from rootfs.usr.src.app.domotica import DomoticaEngine  # noqa: E402
from rootfs.usr.src.app.engine import LayaRouterEngine  # noqa: E402
from rootfs.usr.src.app.resolver import HAResolver  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


def load_dataset(dataset_path: Path) -> List[Dict[str, Any]]:
    """Load JSONL benchmark dataset."""
    if not dataset_path.is_file():
        raise FileNotFoundError(f"Eval dataset not found at {dataset_path}")
    cases = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"⚠️ Warning: skipping invalid JSON on line {idx}: {exc}")
    return cases


def load_overrides(overrides_path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """Load normalized prompt overrides from corrections.yaml."""
    if not overrides_path or not overrides_path.is_file():
        return {}
    try:
        with open(overrides_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            overrides_list = data.get("overrides", [])
            # Map normalized prompt to override dict
            return {
                str(item.get("prompt", "")).strip().lower(): item
                for item in overrides_list
                if isinstance(item, dict) and "prompt" in item
            }
    except Exception as exc:
        print(f"⚠️ Warning: failed to load overrides from {overrides_path}: {exc}")
        return {}


def evaluate_benchmark(
    dataset: List[Dict[str, Any]],
    engine: Optional[LayaRouterEngine],
    domotica_engine: Optional[DomoticaEngine],
    overrides: Dict[str, Dict[str, Any]],
    endpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """Run full evaluation suite across dataset, returning detailed metrics."""
    total = len(dataset)
    if total == 0:
        return {"error": "Empty dataset"}

    correct_raw = 0
    correct_with_overrides = 0
    latencies: List[float] = []

    per_family_stats: Dict[str, Dict[str, int]] = {}
    failures: List[Dict[str, Any]] = []

    import urllib.request

    for item in dataset:
        prompt = item.get("prompt", "")
        exp_family = item.get("expected_family", "")
        if exp_family not in per_family_stats:
            per_family_stats[exp_family] = {"total": 0, "correct_raw": 0, "correct_override": 0}
        per_family_stats[exp_family]["total"] += 1

        norm_p = prompt.strip().lower()

        t0 = time.perf_counter()
        raw_family = "general"
        raw_conf = 0.0

        if endpoint:
            # 1. Evaluate Domotica Fast-Path via HTTP
            ep = endpoint.rstrip("/")
            try:
                req_fp = urllib.request.Request(
                    f"{ep}/v1/domotica/route",
                    data=json.dumps({"prompt": prompt, "execute": False}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req_fp, timeout=5.0) as resp:
                    fp_data = json.loads(resp.read().decode("utf-8"))
                    if fp_data.get("fast_path"):
                        raw_family = "smarthome"
                        raw_conf = fp_data.get("confidence", 0.95)
                        latency = (time.perf_counter() - t0) * 1000.0
                    else:
                        req_r = urllib.request.Request(
                            f"{ep}/v1/route",
                            data=json.dumps({"prompt": prompt, "recent_turns": []}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                        )
                        with urllib.request.urlopen(req_r, timeout=10.0) as resp2:
                            r_data = json.loads(resp2.read().decode("utf-8"))
                            raw_family = r_data.get("family", "general")
                            raw_conf = r_data.get("confidence", {}).get("family", 0.0)
                            latency = r_data.get("latency_ms", (time.perf_counter() - t0) * 1000.0)
            except Exception as exc:
                latency = (time.perf_counter() - t0) * 1000.0
                raw_family = "error"
                print(f"⚠️ Error calling {endpoint}: {exc}")
        else:
            # In-process engine
            d_action = domotica_engine.parse(prompt) if domotica_engine else None
            if d_action and d_action.fast_path:
                raw_family = "smarthome"
                raw_conf = d_action.confidence
                latency = (time.perf_counter() - t0) * 1000.0
            else:
                route_res = engine.route(prompt, recent_turns=[]) if engine else {}
                raw_family = route_res.get("family", "general")
                raw_conf = route_res.get("confidence", {}).get("family", 0.0)
                latency = route_res.get("latency_ms", (time.perf_counter() - t0) * 1000.0)

        latencies.append(latency)

        # Raw evaluation (without overrides)
        is_raw_match = (raw_family == exp_family)
        if is_raw_match:
            correct_raw += 1
            per_family_stats[exp_family]["correct_raw"] += 1

        # Check override
        eff_family = raw_family
        override_applied = False
        if norm_p in overrides:
            eff_family = overrides[norm_p].get("family", raw_family)
            override_applied = True

        is_override_match = (eff_family == exp_family)
        if is_override_match:
            correct_with_overrides += 1
            per_family_stats[exp_family]["correct_override"] += 1

        if not is_override_match:
            failures.append({
                "prompt": prompt,
                "expected": exp_family,
                "predicted_raw": raw_family,
                "predicted_effective": eff_family,
                "confidence": raw_conf,
                "override_applied": override_applied,
                "latency_ms": latency,
            })

    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[len(latencies_sorted) // 2] if latencies_sorted else 0.0
    p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)] if latencies_sorted else 0.0

    return {
        "total_cases": total,
        "accuracy_raw": round((correct_raw / total) * 100.0, 2),
        "accuracy_with_overrides": round((correct_with_overrides / total) * 100.0, 2),
        "overrides_active_count": len(overrides),
        "latency_p50_ms": round(p50, 2),
        "latency_p95_ms": round(p95, 2),
        "per_family_stats": per_family_stats,
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser(description="HA System 1 Evaluation Harness")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=REPO_ROOT / "tests" / "eval_synthetic.jsonl",
        help="Path to JSONL eval dataset",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=None,
        help="Optional path to corrections.yaml",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=90.0,
        help="Minimum overall accuracy percentage required to pass gate (default 90.0)",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Enforce pass/fail quality gate based on --min-accuracy",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default=None,
        help="Optional live HTTP endpoint (e.g. http://local-ha-system-1:8000) for on-device XPU evaluation",
    )
    args = parser.parse_args()

    print("=================================================================")
    print("   HA SYSTEM 1 EVALUATION HARNESS & ACCURACY BENCHMARK          ")
    print("=================================================================")
    print(f"Dataset   : {args.dataset}")
    print(f"Overrides : {args.overrides or 'None (pure zero-shot baseline)'}")

    try:
        dataset = load_dataset(args.dataset)
    except Exception as exc:
        print(f"❌ Failed loading dataset: {exc}")
        sys.exit(1)

    overrides = load_overrides(args.overrides)

    # Initialize in-process engines only if not using remote/live endpoint
    engine = None
    domotica_engine = None
    if not args.endpoint:
        config = AppConfig.load()
        engine = LayaRouterEngine(config)
        engine.initialize()
        resolver = HAResolver()
        domotica_engine = DomoticaEngine(resolver)

    print(f"\nRunning benchmark on {len(dataset)} evaluation cases...")
    res = evaluate_benchmark(dataset, engine, domotica_engine, overrides, endpoint=args.endpoint)

    print("\n-----------------------------------------------------------------")
    print(f"Total Test Cases        : {res['total_cases']}")
    print(f"Accuracy (Raw baseline) : {res['accuracy_raw']}%")
    print(f"Accuracy (W/ overrides) : {res['accuracy_with_overrides']}%")
    print(f"Latency p50 / p95       : {res['latency_p50_ms']} ms / {res['latency_p95_ms']} ms")
    print("-----------------------------------------------------------------")

    print("\nPer-Family Accuracy Breakdown:")
    for fam, stats in res["per_family_stats"].items():
        tot = stats["total"]
        raw_pct = round((stats["correct_raw"] / tot) * 100.0, 1) if tot > 0 else 0
        eff_pct = round((stats["correct_override"] / tot) * 100.0, 1) if tot > 0 else 0
        print(f"  • {fam.upper():<10}: {raw_pct}% raw ({stats['correct_raw']}/{tot}) | {eff_pct}% w/ overrides")

    if res["failures"]:
        print(f"\n❌ Failure Diagnostics ({len(res['failures'])} cases misrouted):")
        for f_idx, fail in enumerate(res["failures"], 1):
            print(f"  [{f_idx}] '{fail['prompt']}'")
            print(f"      Expected : {fail['expected']}")
            print(f"      Got      : {fail['predicted_effective']} (raw: {fail['predicted_raw']}, conf: {fail['confidence']:.2f})")
    else:
        print("\n✓ ZERO FAILURES! All test cases routed with 100% precision.")

    print("=================================================================")

    if args.gate:
        eff_acc = res["accuracy_with_overrides"]
        if eff_acc < args.min_accuracy:
            print(f"❌ GATE FAILED: Accuracy {eff_acc}% is below required threshold of {args.min_accuracy}%!")
            sys.exit(1)
        else:
            print(f"✓ GATE PASSED: Accuracy {eff_acc}% meets or exceeds {args.min_accuracy}%.")
            sys.exit(0)


if __name__ == "__main__":
    main()

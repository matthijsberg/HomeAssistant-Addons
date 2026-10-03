"""Unit tests for the evaluation harness and synthetic dataset (PRD EV-01..04)."""

from pathlib import Path

from scripts.eval import evaluate_benchmark, load_dataset, load_overrides

TESTS_DIR = Path(__file__).parent
DATASET_PATH = TESTS_DIR / "eval_synthetic.jsonl"
OVERRIDES_PATH = TESTS_DIR / "test_corrections.yaml"


def test_synthetic_dataset_conformance():
    """Verify synthetic dataset format, labels, and coverage."""
    assert DATASET_PATH.is_file(), "eval_synthetic.jsonl must exist"
    cases = load_dataset(DATASET_PATH)
    assert len(cases) >= 30, "Benchmark must contain at least 30 evaluation cases"

    allowed_families = {"quick", "smarthome", "general", "code", "deep"}
    allowed_efforts = {"light", "normal", "deep", "high"}

    families_present = set()
    for idx, case in enumerate(cases, 1):
        assert "prompt" in case, f"Case #{idx} missing 'prompt'"
        assert "expected_family" in case, f"Case #{idx} missing 'expected_family'"
        assert case["expected_family"] in allowed_families, f"Unknown family in case #{idx}: {case['expected_family']}"
        assert case.get("expected_effort") in allowed_efforts, f"Unknown effort in case #{idx}"
        families_present.add(case["expected_family"])

    # Ensure all 5 families are covered
    assert families_present == allowed_families, f"All families must be covered. Missing: {allowed_families - families_present}"


def test_overrides_loading():
    """Verify loading and normalization of overrides from corrections.yaml."""
    overrides = load_overrides(OVERRIDES_PATH)
    assert len(overrides) >= 1
    sample_key = next(iter(overrides.keys()))
    assert sample_key == sample_key.lower().strip()
    assert overrides[sample_key]["family"] == "deep"


def test_eval_harness_execution():
    """Verify evaluation harness computes metrics with and without overrides."""
    dataset = [
        {"prompt": "doe de lampen uit", "expected_family": "smarthome", "expected_effort": "light"},
        {"prompt": "wat is de hoofdstad van Frankrijk?", "expected_family": "quick", "expected_effort": "light"},
    ]
    overrides = {
        "wat is de hoofdstad van frankrijk?": {"family": "quick"}
    }
    res = evaluate_benchmark(dataset, None, None, overrides, endpoint=None)
    assert "accuracy_raw" in res
    assert "accuracy_with_overrides" in res
    assert res["total_cases"] == 2

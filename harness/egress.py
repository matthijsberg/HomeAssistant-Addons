import re
import json
import hashlib
from typing import Dict, Any, List, Tuple

# Redaction patterns for secrets in code and diffs
SECRET_PATTERNS = [
    (r"ghp_[A-Za-z0-9_]{36,}", "[REDACTED_GITHUB_TOKEN]"),
    (r"github_pat_[A-Za-z0-9_]{82}", "[REDACTED_GITHUB_PAT]"),
    (r"eyJhbGciOi[A-Za-z0-9_\-\.]+", "[REDACTED_JWT_TOKEN]"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
    (r"(?i)(api_key|token|secret|password|passwd|auth)\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{12,}['\"]", r"\1: '[REDACTED_SECRET]'"),
]

# Estimated token costs (EUR per 1M tokens) - conservative pricing
MODEL_COSTS_EUR = {
    "gemini": {"input": 0.15, "output": 0.60},
    "anthropic": {"input": 3.00, "output": 15.00},
    "openai": {"input": 2.50, "output": 10.00},
    "ollama": {"input": 0.0, "output": 0.0},
    "default": {"input": 1.00, "output": 4.00},
}


def redact_secrets(text: str) -> Tuple[str, int]:
    """Redact sensitive patterns in source code or diffs. Returns (clean_text, count)."""
    clean_text = text
    redaction_count = 0
    for pattern, replacement in SECRET_PATTERNS:
        matches = len(re.findall(pattern, clean_text))
        if matches > 0:
            redaction_count += matches
            clean_text = re.sub(pattern, replacement, clean_text)
    return clean_text, redaction_count


def compute_manifest_hash(manifest: Dict[str, Any]) -> str:
    """Deterministic hash of manifest keys for tamper-proof Ingress approval."""
    canonical_str = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()[:16]


def estimate_job_cost(files_bytes: int, models: Dict[str, Any], passes: int = 1) -> float:
    """Rough estimation of cost in EUR based on byte size and LLM model tiers."""
    # 4 bytes ~= 1 token
    est_input_tokens = (files_bytes / 4) * (1 + passes * 1.5)
    est_output_tokens = est_input_tokens * 0.15

    deep_prov = models.get("deep", {}).get("provider", "default")
    rate = MODEL_COSTS_EUR.get(deep_prov, MODEL_COSTS_EUR["default"])

    cost = (est_input_tokens / 1_000_000 * rate["input"]) + (est_output_tokens / 1_000_000 * rate["output"])
    return round(max(0.01, cost), 2)


def create_egress_manifest(
    targets: List[str],
    files_count: int,
    bytes_count: int,
    redactions: int,
    models: Dict[str, Any],
    passes: int = 1,
) -> Dict[str, Any]:
    est_cost = estimate_job_cost(bytes_count, models, passes)
    manifest = {
        "targets": targets,
        "files_count": files_count,
        "bytes_count": bytes_count,
        "redactions": redactions,
        "models": models,
        "estimated_cost_eur": est_cost,
    }
    manifest["manifest_hash"] = compute_manifest_hash(manifest)
    return manifest


def format_warning_text(manifest: Dict[str, Any], approval_url: str) -> str:
    targets_str = ", ".join(manifest.get("targets", []))
    files = manifest.get("files_count", 0)
    kb = round(manifest.get("bytes_count", 0) / 1024, 1)
    redactions = manifest.get("redactions", 0)
    cost = manifest.get("estimated_cost_eur", 0.0)
    triage_model = manifest.get("models", {}).get("triage", {}).get("model", "default")
    deep_model = manifest.get("models", {}).get("deep", {}).get("model", "default")

    return (
        f"🛡️ Mantis Security Audit Approval Required\n\n"
        f"Mantis wants to send {files} files ({kb} KB, {redactions} secrets redacted) "
        f"from [{targets_str}] to {triage_model} & {deep_model}.\n"
        f"Estimated cost: €{cost:.2f}\n\n"
        f"Approve via Home Assistant Ingress:\n{approval_url}"
    )

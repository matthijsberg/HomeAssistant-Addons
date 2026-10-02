import os
import json
import httpx
from pathlib import Path
from typing import Dict, Any, List, Optional

from .egress import redact_secrets
from scanners.semgrep_runner import run_semgrep
from scanners.gitleaks_runner import run_gitleaks
from scanners.shellcheck_runner import run_shellcheck
from scanners.hadolint_runner import run_hadolint
from surface.extractor import build_deployment_manifest


def call_llm(
    prompt: str,
    system_instruction: str,
    provider_config: Dict[str, Any],
    model_role: str = "triage",
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Invoke configured LLM provider (Gemini, Anthropic, OpenAI, Ollama, or OpenAI-compatible).
    Returns {"text": ..., "tokens_in": ..., "tokens_out": ...}
    """
    provider = provider_config.get("provider", "gemini")
    model = provider_config.get("model", "gemini-2.5-flash")
    api_key = provider_config.get("api_key", "")
    base_url = provider_config.get("base_url", "")

    # Ollama endpoint
    if provider == "ollama":
        url = f"{base_url.rstrip('/')}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "system": system_instruction,
            "stream": False,
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "text": data.get("response", ""),
                "tokens_in": data.get("prompt_eval_count", 0),
                "tokens_out": data.get("eval_count", 0),
            }

    # Gemini native REST endpoint
    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = ""
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
            usage = data.get("usageMetadata", {})
            return {
                "text": text,
                "tokens_in": usage.get("promptTokenCount", 0),
                "tokens_out": usage.get("candidatesTokenCount", 0),
            }

    # Anthropic Messages API
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "system": system_instruction,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
            "temperature": 0.1,
        }
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [])
            text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
            usage = data.get("usage", {})
            return {
                "text": text,
                "tokens_in": usage.get("input_tokens", 0),
                "tokens_out": usage.get("output_tokens", 0),
            }

    # OpenAI / OpenAI-compatible endpoint
    endpoint = f"{base_url.rstrip('/')}/chat/completions" if base_url else "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        usage = data.get("usage", {})
        return {
            "text": text,
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
        }


def run_deterministic_prepass(target_dir: Path) -> Dict[str, Any]:
    """
    Run static scanners and surface extractor without LLM calls.
    Returns prepass results bundle.
    """
    manifest = build_deployment_manifest(target_dir)
    semgrep_leads = run_semgrep(target_dir)
    gitleaks_leads = run_gitleaks(target_dir)
    shellcheck_leads = run_shellcheck(target_dir)
    hadolint_leads = run_hadolint(target_dir)

    all_leads = []
    all_leads.extend(semgrep_leads)
    all_leads.extend(gitleaks_leads)
    all_leads.extend(shellcheck_leads)
    all_leads.extend(hadolint_leads)

    return {
        "manifest": manifest,
        "leads": all_leads,
        "scanner_counts": {
            "semgrep": len(semgrep_leads),
            "gitleaks": len(gitleaks_leads),
            "shellcheck": len(shellcheck_leads),
            "hadolint": len(hadolint_leads),
            "cross_artifact_risks": len(manifest.get("cross_artifact_risks", [])),
        },
    }


def synthesize_patch_check(repo_dir: Path, target_file: str, patch_diff: str) -> str:
    """
    Verify if a proposed unified diff applies cleanly using git apply --check.
    Returns: 'proposed_applies_cleanly' | 'proposed_conflicts' | 'none'
    """
    if not patch_diff or not patch_diff.strip():
        return "none"

    patch_file = repo_dir / ".mantis_temp.patch"
    try:
        patch_file.write_text(patch_diff, encoding="utf-8")
        res = os.system(f"git -C '{repo_dir}' apply --check '{patch_file}' >/dev/null 2>&1")
        if res == 0:
            return "proposed_applies_cleanly"
        else:
            return "proposed_conflicts"
    except Exception:
        return "proposed_conflicts"
    finally:
        if patch_file.exists():
            patch_file.unlink()

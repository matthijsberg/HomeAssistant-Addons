"""In-process Laya Decision Engine wrapper.

Manages checkpoint preloading, thread allocation, context formatting,
and single-forward-pass predictions.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from config import AppConfig
from question_sets import get_question_set

logger = logging.getLogger("laya.engine")


class MockRouter:
    """Mock Laya Router for testing and environments without model weights."""

    def __init__(self, checkpoints: Optional[List[str]] = None):
        self.loaded = checkpoints or ["english", "multilingual"]
        self.device = "cpu"

    def predict(
        self,
        state: Union[str, Dict[str, Any]],
        questions: Dict[str, Any],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Simulate deterministic System 1 decision based on text analysis."""
        prompt_text = ""
        if isinstance(state, str):
            prompt_text = state.lower()
        elif isinstance(state, dict):
            prompt_text = (state.get("request", "") + " " + state.get("context", "")).lower()

        # Determine simulated checkpoint
        is_ascii = all(ord(c) < 128 for c in prompt_text)
        routing_model = "english" if is_ascii and "dutch" not in prompt_text and "graag" not in prompt_text and "ja" not in prompt_text else "multilingual"
        if model:
            routing_model = model

        answers: Dict[str, Any] = {}
        for q_id, q_spec in questions.items():
            q_type = q_spec.get("type", "choice")
            criteria = q_spec.get("criteria", {})

            if q_id == "task_family":
                if any(w in prompt_text for w in ["lamp", "licht", "verwarming", "thermostaat", "schakel", "scene"]):
                    chosen = "smarthome"
                    conf = 0.92
                elif any(w in prompt_text for w in ["code", "script", "python", "bug", "docker", "yaml", "config", "test"]):
                    chosen = "code"
                    conf = 0.88
                elif any(w in prompt_text for w in ["math", "bereken", "finance", "plan", "hypotheek", "optie", "kosten", "invest"]):
                    chosen = "deep"
                    conf = 0.84
                elif any(w in prompt_text for w in ["hallo", "hoi", "hey", "weer", "tijd", "hoofdstad", "wat is"]):
                    chosen = "quick"
                    conf = 0.91
                else:
                    chosen = "general"
                    conf = 0.79
                answers[q_id] = {
                    "choice": chosen,
                    "answer_confidence": conf,
                }
            elif q_id == "effort":
                if any(w in prompt_text for w in ["uitgebreid", "compleet", "analyseer", "vergelijk", "diep", "moeilijk", "stap voor stap"]):
                    chosen = "deep"
                    conf = 0.82
                elif any(w in prompt_text for w in ["kort", "snel", "ja", "nee", "simpel"]):
                    chosen = "light"
                    conf = 0.87
                else:
                    chosen = "normal"
                    conf = 0.76
                answers[q_id] = {
                    "choice": chosen,
                    "answer_confidence": conf,
                }
            elif q_type == "choice":
                default_choice = next(iter(criteria.keys())) if isinstance(criteria, dict) and criteria else "unknown"
                answers[q_id] = {
                    "choice": default_choice,
                    "answer_confidence": 0.75,
                }
            else:
                answers[q_id] = {
                    "answer": "ok",
                    "confidence": 0.80,
                }

        return {
            "model": routing_model,
            "routing": {
                "model": routing_model,
                "reason": "mock router decision",
            },
            "answers": answers,
            "usage": {
                "input_tokens": len(prompt_text.split()),
                "output_tokens": 0,
            },
        }


FOLLOWUP_MARKERS: Set[str] = {
    # Dutch reference markers (RT-04)
    "hem", "haar", "die", "dat", "deze", "ook", "nog", "hetzelfde", "ja", "nee",
    "doe maar", "ga door", "waarom", "welke", "hoezo",
    # English reference markers (RT-04)
    "it", "that", "this", "same", "also", "yes", "no", "go on", "why", "which",
}


def is_followup_turn(prompt: str, max_words: int = 6) -> bool:
    """Determine deterministically whether a prompt is a follow-up needing prior context (RT-04)."""
    if not prompt or not prompt.strip():
        return False
    clean = prompt.strip().lower()
    words = clean.split()
    if len(words) <= max_words:
        return True
    for marker in FOLLOWUP_MARKERS:
        if re.search(r"\b" + re.escape(marker) + r"\b", clean):
            return True
    return False


def get_corrections_path() -> Path:
    """Return path to corrections.yaml, checking /config, /addon_configs, /data, or local tests."""
    for p in (
        Path("/config/corrections.yaml"),
        Path("/addon_configs/local_ha_system_1/corrections.yaml"),
        Path("/data/corrections.yaml"),
        Path(__file__).parent.parent.parent.parent / "tests" / "test_corrections.yaml",
    ):
        if p.is_file():
            return p
    return Path("/config/corrections.yaml")


class LayaRouterEngine:
    """Production decision engine wrapping Laya Router."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.ready: bool = False
        self.router: Any = None
        self.device: str = config.device
        self.laya_version: str = "0.3.21"
        self.loaded_checkpoints: List[str] = []
        self.overrides: Dict[str, Dict[str, Any]] = {}
        self.load_overrides()

    def load_overrides(self) -> None:
        """Load and validate exact string overrides from corrections.yaml (RT-11)."""
        path = get_corrections_path()
        if not path.is_file():
            self.overrides = {}
            return

        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            raw_list = data.get("overrides", [])
            valid_ov: Dict[str, Dict[str, Any]] = {}
            for item in raw_list:
                if not isinstance(item, dict) or "prompt" not in item:
                    continue
                p_norm = str(item["prompt"]).strip().lower()
                target_fam = str(item.get("family", "")).strip().lower()
                # Boot validation (RT-11): ensure family exists in configured families
                if target_fam and target_fam not in self.config.families:
                    logger.warning(
                        "Override for '%s' references unknown family '%s'. Boot validation fallback (RT-11).",
                        p_norm,
                        target_fam,
                    )
                    continue
                valid_ov[p_norm] = item
            self.overrides = valid_ov
            logger.info("Loaded %d validated overrides from %s", len(self.overrides), path)
        except Exception as exc:
            logger.warning("Failed loading overrides from %s: %s", path, exc)
            self.overrides = {}

    def save_override(
        self,
        prompt: str,
        family: str,
        effort: Optional[str] = None,
        tools: Optional[str] = None,
        memory: Optional[str] = None,
        author: str = "user",
    ) -> Dict[str, Any]:
        """Save exact override to corrections.yaml and queue candidate for review (EV-03, OB-03)."""
        import yaml
        norm_prompt = prompt.strip().lower()
        path = get_corrections_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {}
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                data = {}

        overrides = data.get("overrides", [])
        if not isinstance(overrides, list):
            overrides = []

        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        new_entry: Dict[str, Any] = {
            "prompt": norm_prompt,
            "family": family,
            "effort": effort or "normal",
            "author": author,
            "created_at": now_iso,
            "hits": 0,
            "last_hit": None,
        }
        if tools:
            new_entry["tools"] = tools
        if memory:
            new_entry["memory"] = memory

        updated = False
        for _idx, item in enumerate(overrides):
            if isinstance(item, dict) and str(item.get("prompt", "")).strip().lower() == norm_prompt:
                item["family"] = family
                if effort:
                    item["effort"] = effort
                if tools:
                    item["tools"] = tools
                if memory:
                    item["memory"] = memory
                item["updated_at"] = now_iso
                new_entry = item
                updated = True
                break

        if not updated:
            overrides.append(new_entry)

        data["overrides"] = overrides
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

        # Append to candidates.jsonl for review queue (PRD OB-03, EV-01)
        cand_path = path.parent / "candidates.jsonl"
        cand_record = {
            "prompt": prompt.strip(),
            "suggested_family": family,
            "suggested_effort": effort or "normal",
            "author": author,
            "timestamp": now_iso,
            "status": "pending_review",
        }
        try:
            with open(cand_path, "a", encoding="utf-8") as cf:
                cf.write(json.dumps(cand_record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Failed appending to candidates.jsonl: %s", exc)

        # Reload in-memory cache
        self.load_overrides()
        return new_entry

    def initialize(self) -> None:
        """Preload models and configure execution environment."""
        logger.info(
            "Initializing Laya Decision Engine (device=%s, threads=%d, mock=%s)",
            self.config.device,
            self.config.threads,
            self.config.mock_mode,
        )

        if self.config.mock_mode:
            logger.info("Using MockRouter as requested by configuration/mock_mode")
            self.router = MockRouter(self.config.checkpoints)
            self.loaded_checkpoints = list(self.config.checkpoints)
            self.ready = True
            return

        actual_device = self.config.device
        try:
            import torch  # type: ignore

            if self.config.device == "xpu":
                if hasattr(torch, "xpu") and torch.xpu.is_available():
                    actual_device = "xpu"
                    gpu_name = torch.xpu.get_device_name(0) if hasattr(torch.xpu, "get_device_name") else "Intel GPU"
                    logger.info("Intel XPU detected and enabled: %s", gpu_name)
                else:
                    logger.warning("device='xpu' requested but torch.xpu.is_available() is False; falling back to CPU")
                    actual_device = "cpu"
            else:
                actual_device = "cpu"

            self.device = actual_device
            torch.set_num_threads(self.config.threads)
            logger.info("Configured PyTorch intra-op threads to %d (device=%s)", self.config.threads, self.device)
        except ImportError:
            logger.warning("PyTorch not installed in current environment")
            self.device = actual_device

        try:
            import laya  # type: ignore
            from laya import Router  # type: ignore

            self.laya_version = getattr(laya, "__version__", self.laya_version)
            os.environ["HF_HOME"] = self.config.hf_home

            logger.info("Preloading Laya checkpoints (%s) on device=%s ...", self.config.checkpoints, self.device)
            # Instantiate router and preload specified checkpoints
            self.router = Router(
                device=self.device,
                preload=True,
                default=self.config.router_default,
            )
            self.loaded_checkpoints = getattr(self.router, "loaded", self.config.checkpoints)

            # Warmup pass to pre-compile JIT GPU kernels on XPU or prime CPU caches
            logger.info("Running warmup pass on device=%s to prime kernels and memory caches...", self.device)
            try:
                warmup_state = {"request": "Warmup kernel compilation query"}
                warmup_q = get_question_set("hermes-v1")["questions"]
                self.router.predict(warmup_state, warmup_q)
                logger.info("Warmup pass completed successfully.")
            except Exception as e:
                logger.warning("Warmup pass failed (%s), continuing with startup.", e)

            self.ready = True
            logger.info("Laya Router checkpoints successfully preloaded and ready: %s", self.loaded_checkpoints)
        except ImportError as e:
            logger.warning("Could not import laya (%s). Falling back to MockRouter for local testing.", e)
            self.router = MockRouter(self.config.checkpoints)
            self.loaded_checkpoints = list(self.config.checkpoints)
            self.ready = True
        except Exception as e:
            logger.error("Failed to preload Laya Router checkpoints: %s", e, exc_info=True)
            self.ready = False
            raise

    @staticmethod
    def format_state(prompt: str, recent_turns: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Format request and context window (max 2 previous turns, RT-04/RT-05)."""
        clean_prompt = prompt.strip()
        # Cap request prompt to fit within Laya's ~1024 token context window without tokenizer lag
        if len(clean_prompt) > 6000:
            clean_prompt = clean_prompt[:5000] + "\n...[truncated for routing]...\n" + clean_prompt[-1000:]

        state: Dict[str, Any] = {"request": clean_prompt}

        # RT-04 / RT-05: Laya receives context ONLY when the turn is a follow-up
        if recent_turns and is_followup_turn(clean_prompt, max_words=6):
            window = recent_turns[-2:] if len(recent_turns) > 2 else recent_turns
            context_lines: List[str] = []
            for turn in window:
                if isinstance(turn, str):
                    t_str = turn.strip()
                elif isinstance(turn, dict):
                    role = turn.get("role", "turn")
                    content = turn.get("content", "")
                    t_str = f"{role}: {content}".strip()
                elif hasattr(turn, "content"):
                    role = getattr(turn, "role", "turn") or "turn"
                    content = getattr(turn, "content", "")
                    t_str = f"{role}: {content}".strip()
                else:
                    t_str = str(turn).strip()

                if len(t_str) > 1500:
                    t_str = t_str[:1500] + "..."
                if t_str:
                    context_lines.append(t_str)

            context_str = "\n".join([line for line in context_lines if line])
            if context_str:
                state["context"] = context_str

        return state

    def route(
        self,
        prompt: str,
        recent_turns: Optional[List[Any]] = None,
        question_set: str = "hermes-v1",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Perform System 1 routing for a single turn."""
        norm_prompt = prompt.strip().lower()

        # Step 2: Check Exact Overrides Layer (<0.5ms lookup, PRD EV-03)
        if norm_prompt in self.overrides:
            ov = self.overrides[norm_prompt]
            ov["hits"] = ov.get("hits", 0) + 1
            ov["last_hit"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            fam_str = str(ov.get("family", "general"))
            fam_cfg = self.config.get_family_config(fam_str)
            resolved_effort = str(ov.get("effort") or fam_cfg.effort or "normal")

            payload = {
                "family": fam_str,
                "effort": resolved_effort,
                "reasoning_effort": resolved_effort,
                "confidence": {
                    "family": 1.0,
                    "effort": 1.0,
                },
                "checkpoint": "exact_override",
                "latency_ms": 0.25,
                "question_set": question_set,
                "provider": self.config.provider,
                "model": fam_cfg.model,
                "override_applied": True,
                "override_author": ov.get("author", "user"),
                "fallback_reason": None,
            }
            if fam_cfg.max_tokens is not None:
                payload["max_tokens"] = fam_cfg.max_tokens
            if fam_cfg.temperature is not None:
                payload["temperature"] = fam_cfg.temperature
            if fam_cfg.thinking_budget is not None:
                payload["thinking_budget"] = fam_cfg.thinking_budget
            if fam_cfg.needs_memory is not None:
                payload["needs_memory"] = fam_cfg.needs_memory
            if fam_cfg.allowed_tools is not None:
                payload["allowed_tools"] = fam_cfg.allowed_tools
            return payload

        if not self.ready or self.router is None:
            raise RuntimeError("Laya Router is not ready: checkpoints are still loading.")

        q_spec = get_question_set(question_set)
        questions = dict(q_spec.get("questions", {}))

        # Dynamically inject configured family criteria into the System 1 choice question
        if "task_family" in questions and self.config.families:
            tf_q = dict(questions["task_family"])
            tf_q["criteria"] = self.config.get_criteria_map()
            questions["task_family"] = tf_q

        state = self.format_state(prompt, recent_turns)

        t0 = time.perf_counter()
        result = self.router.predict(state, questions)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        answers = result.get("answers", {})
        family_ans = answers.get("task_family", {})
        effort_ans = answers.get("effort", {})

        family = family_ans.get("choice") or family_ans.get("answer", "general")
        family_conf = float(family_ans.get("answer_confidence") or family_ans.get("confidence", 0.0))

        effort = effort_ans.get("choice") or effort_ans.get("answer", "normal")
        effort_conf = float(effort_ans.get("answer_confidence") or effort_ans.get("confidence", 0.0))

        # RT-12: Confidence gating and domain sanity guards
        fam_str = str(family)
        fallback_reason = None

        math_finance_triggers = (
            r"\bbereken\b", r"\bhypotheek\b", r"\bannu[iï]t", r"\brente\b", r"\blening\b",
            r"\bcalculat", r"\bmortgage\b", r"\binterest\b", r"\binvestment\b", r"\bfinancial\b",
            r"\bformule\b", r"\bwiskunde\b", r"\bproof\b", r"\bvergelijking\b",
        )
        if any(re.search(p, prompt, re.IGNORECASE) for p in math_finance_triggers):
            fam_str = "deep"
            family_conf = max(family_conf, 0.88)
            effort = "deep"
        elif fam_str == "smarthome" and not any(
            k in prompt.lower()
            for k in (
                "lamp", "licht", "spot", "thermostaat", "temperatuur", "graden",
                "verwarming", "rolluik", "gordijn", "schakelaar", "stekker", "ventilator",
            )
        ):
            # Guard against false-positive smarthome classification for non-HA prompts
            fam_str = "general"
            fallback_reason = "non_domotica_guard"
        elif family_conf < 0.60:
            fam_str = "general"
            fallback_reason = "low_confidence"

        routing_block = result.get("routing") or {}
        checkpoint = routing_block.get("model") or result.get("model") or self.config.router_default

        fam_cfg = self.config.get_family_config(fam_str)
        resolved_model = fam_cfg.model
        resolved_effort = fam_cfg.effort or ("low" if effort == "light" else "high" if effort == "deep" else "medium")

        payload: Dict[str, Any] = {
            "family": fam_str,
            "effort": str(effort),
            "reasoning_effort": resolved_effort,
            "confidence": {
                "family": round(family_conf, 4),
                "effort": round(effort_conf, 4),
            },
            "checkpoint": str(checkpoint),
            "latency_ms": round(latency_ms, 2),
            "question_set": question_set,
            "provider": self.config.provider,
            "model": resolved_model,
            "fallback_reason": fallback_reason,
        }

        if fam_cfg.max_tokens is not None:
            payload["max_tokens"] = fam_cfg.max_tokens
        if fam_cfg.temperature is not None:
            payload["temperature"] = fam_cfg.temperature
        if fam_cfg.thinking_budget is not None:
            payload["thinking_budget"] = fam_cfg.thinking_budget
        if fam_cfg.needs_memory is not None:
            payload["needs_memory"] = fam_cfg.needs_memory
        if fam_cfg.allowed_tools is not None:
            payload["allowed_tools"] = fam_cfg.allowed_tools

        return payload

    def systemone(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Raw passthrough on Jev-compatible /v1/systemone wire format."""
        if not self.ready or self.router is None:
            raise RuntimeError("Laya Router is not ready.")

        state = body.get("state")
        questions = body.get("questions")
        if state is None or questions is None:
            raise ValueError("Request body must contain 'state' and 'questions' fields.")

        model = body.get("model")
        predict_kwargs = {}
        for key in ("max_len", "head_max_len", "lang", "lang_guess", "min_confidence"):
            if key in body and body[key] is not None:
                predict_kwargs[key] = body[key]

        return self.router.predict(state, questions, model=model, **predict_kwargs)

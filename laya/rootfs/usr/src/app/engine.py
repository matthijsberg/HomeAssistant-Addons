"""In-process Laya Decision Engine wrapper.

Manages checkpoint preloading, thread allocation, context formatting,
and single-forward-pass predictions.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Union

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
                if any(w in prompt_text for w in ["code", "script", "python", "bug", "docker", "yaml", "config", "test"]):
                    chosen = "code"
                    conf = 0.88
                elif any(w in prompt_text for w in ["math", "bereken", "finance", "plan", "hypotheek", "optie", "kosten", "invest"]):
                    chosen = "deep"
                    conf = 0.84
                elif any(w in prompt_text for w in ["hallo", "hoi", "hey", "weer", "tijd", "lamp", "aan", "uit", "wat is 1+1"]):
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


class LayaRouterEngine:
    """Production decision engine wrapping Laya Router."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.ready: bool = False
        self.router: Any = None
        self.device: str = config.device
        self.laya_version: str = "0.3.21"
        self.loaded_checkpoints: List[str] = []

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
        """Format request and context window (max 2 previous turns)."""
        turns = recent_turns or []
        # Strictly truncate to at most the previous 2 turns
        window = turns[-2:] if len(turns) > 2 else turns

        context_lines: List[str] = []
        for turn in window:
            if isinstance(turn, str):
                context_lines.append(turn.strip())
            elif isinstance(turn, dict):
                role = turn.get("role", "turn")
                content = turn.get("content", "")
                context_lines.append(f"{role}: {content}".strip())
            elif hasattr(turn, "content"):
                role = getattr(turn, "role", "turn") or "turn"
                content = getattr(turn, "content", "")
                context_lines.append(f"{role}: {content}".strip())

        context_str = "\n".join([line for line in context_lines if line])
        state: Dict[str, Any] = {"request": prompt.strip()}
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
        if not self.ready or self.router is None:
            raise RuntimeError("Laya Router is not ready: checkpoints are still loading.")

        q_spec = get_question_set(question_set)
        questions = q_spec["questions"]
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

        routing_block = result.get("routing") or {}
        checkpoint = routing_block.get("model") or result.get("model") or self.config.router_default

        resolved_model = self.config.get_model_for_family(str(family))

        return {
            "family": str(family),
            "effort": str(effort),
            "confidence": {
                "family": round(family_conf, 4),
                "effort": round(effort_conf, 4),
            },
            "checkpoint": str(checkpoint),
            "latency_ms": round(latency_ms, 2),
            "question_set": question_set,
            "provider": self.config.provider,
            "model": resolved_model,
        }

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

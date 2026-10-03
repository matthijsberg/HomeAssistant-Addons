"""Versioned Question Sets for Laya System 1 Routing.

Defines the exact criteria text and choice options for task categorization and reasoning effort.
"""

from typing import Any, Dict

QUESTION_SETS: Dict[str, Dict[str, Any]] = {
    "hermes-v1": {
        "version": "1.0",
        "description": "Standard v1 routing criteria for Quick, General, Code, and Deep model tiers",
        "questions": {
            "task_family": {
                "type": "choice",
                "instructions": "What kind of task is this?",
                "criteria": {
                    "quick": "small talk, greetings, simple facts, unit conversions, one-step home control commands",
                    "general": "everyday writing, explaining, summarising, translating, ordinary questions that need a few tool calls",
                    "code": "writing or debugging code or configuration files, multi-step tool or agent work",
                    "deep": "hard reasoning where a wrong answer is costly: maths, finance, planning, comparing complex options",
                },
            },
            "effort": {
                "type": "choice",
                "instructions": "How much thinking does this task need?",
                "criteria": {
                    "light": "the answer is short and obvious",
                    "normal": "needs some thought or a few steps",
                    "deep": "needs careful multi-step reasoning or double-checking",
                },
            },
        },
    }
}


def get_question_set(name: str = "hermes-v1") -> Dict[str, Any]:
    """Retrieve question set by identifier."""
    if name not in QUESTION_SETS:
        raise KeyError(f"Unknown question set '{name}'. Available: {list(QUESTION_SETS.keys())}")
    return QUESTION_SETS[name]


def list_question_sets() -> Dict[str, Any]:
    """Return all registered question sets."""
    return QUESTION_SETS

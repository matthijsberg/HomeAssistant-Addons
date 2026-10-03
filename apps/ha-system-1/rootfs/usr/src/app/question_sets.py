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
    },
    "domotica-v1": {
        "version": "1.0",
        "description": "Semantic slot extraction for Home Assistant domotica actions",
        "questions": {
            "target_scope": {
                "type": "choice",
                "instructions": "Is the target the entire room or a specific device?",
                "criteria": {
                    "entire_area": "the entire room, all lights in the area, alle lampen, het licht in de ruimte, alles in de kamer",
                    "specific_device": "a specific device subtype such as spots, led strip, curtains, desk lamp, specific lamp",
                },
            },
            "device_type": {
                "type": "choice",
                "instructions": "What type of device is specified?",
                "criteria": {
                    "general_light": "general lights or ceiling lamp, lampen, verlichting",
                    "spots": "spotlights, ceiling spots, inbouwspots, spots, spot",
                    "led_strip": "LED strip, leds, strip, accent lighting, curtain light, gordijnen licht, ledstrip",
                    "cover": "curtains, blinds, shutters, rolluik, gordijnen, jaloezieën",
                    "climate": "thermostat, heating, temperature, airco, verwarming, graden",
                    "switch": "plug, socket, switch, relay, stekker, stopcontact",
                },
            },
            "room": {
                "type": "choice",
                "instructions": "In which room or area is the device located?",
                "criteria": {
                    "serre": "serre, sunroom, conservatory",
                    "woonkamer": "woonkamer, living room, lounge",
                    "keuken": "keuken, kitchen",
                    "eetkamer": "eetkamer, dining room",
                    "gang": "gang, hal, hallway",
                    "slaapkamer": "slaapkamer, bedroom",
                    "badkamer": "badkamer, bathroom",
                    "kantoor": "kantoor, werkkamer, office",
                    "tuin": "tuin, veranda, garden",
                    "unspecified": "no specific room mentioned",
                },
            },
        },
    },
}


def get_question_set(name: str = "hermes-v1") -> Dict[str, Any]:
    """Retrieve question set by identifier."""
    if name not in QUESTION_SETS:
        raise KeyError(f"Unknown question set '{name}'. Available: {list(QUESTION_SETS.keys())}")
    return QUESTION_SETS[name]


def list_question_sets() -> Dict[str, Any]:
    """Return all registered question sets."""
    return QUESTION_SETS

"""Configuration loader for Laya Router HA Add-on."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class FamilyConfig:
    """Routing and execution profile for a task family."""

    name: str
    criteria: str
    model: str
    effort: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    thinking_budget: Optional[int] = None
    needs_memory: Optional[bool] = None
    allowed_tools: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize configuration excluding None values for sparse payload delivery."""
        res: Dict[str, Any] = {
            "name": self.name,
            "criteria": self.criteria,
            "model": self.model,
        }
        if self.effort is not None:
            res["effort"] = self.effort
        if self.max_tokens is not None:
            res["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            res["temperature"] = self.temperature
        if self.thinking_budget is not None:
            res["thinking_budget"] = self.thinking_budget
        if self.needs_memory is not None:
            res["needs_memory"] = self.needs_memory
        if self.allowed_tools is not None:
            res["allowed_tools"] = self.allowed_tools
        return res


DEFAULT_FAMILIES: Dict[str, FamilyConfig] = {
    "quick": FamilyConfig(
        name="quick",
        criteria="small talk, greetings, simple facts, unit conversions, general knowledge questions without tools",
        model="gemini-3.5-flash-lite",
        effort="low",
        max_tokens=1024,
        temperature=0.2,
        thinking_budget=0,
        needs_memory=False,
        allowed_tools=[],
    ),
    "smarthome": FamilyConfig(
        name="smarthome",
        criteria="home automation control, turning lights on or off, thermostat temperature, switches, scenes, checking Home Assistant entities or sensor states",
        model="gemini-3.5-flash-lite",
        effort="low",
        max_tokens=1024,
        temperature=0.0,
        thinking_budget=0,
        needs_memory=False,
        allowed_tools=["ha_list_entities", "ha_get_state", "ha_call_service", "ha_list_services", "homeassistant*"],
    ),
    "general": FamilyConfig(
        name="general",
        criteria="everyday writing, explaining, summarising, translating, ordinary questions that need a few tool calls",
        model="gemini-flash-latest",
        effort="medium",
        max_tokens=4096,
        temperature=0.7,
        thinking_budget=None,
        needs_memory=True,
    ),
    "code": FamilyConfig(
        name="code",
        criteria="writing or debugging code or configuration files, multi-step tool or agent work",
        model="gemini-flash-latest",
        effort="high",
        max_tokens=8192,
        temperature=0.1,
        thinking_budget=None,
        needs_memory=True,
    ),
    "deep": FamilyConfig(
        name="deep",
        criteria="hard reasoning where a wrong answer is costly: maths, finance, planning, comparing complex options",
        model="gemini-2.5-pro",
        effort="high",
        max_tokens=8192,
        temperature=0.2,
        thinking_budget=None,
        needs_memory=True,
    ),
}


@dataclass
class AppConfig:
    device: str = "cpu"
    threads: int = 6
    checkpoints: List[str] = field(default_factory=lambda: ["english", "multilingual"])
    router_default: str = "multilingual"
    laya_revision: str = "main"
    api_key: Optional[str] = None
    log_level: str = "info"
    hf_home: str = "/data/hf"
    mock_mode: bool = False
    provider: str = "gemini"
    gateway_url: Optional[str] = None
    families: Dict[str, FamilyConfig] = field(
        default_factory=lambda: {k: FamilyConfig(**asdict(v)) for k, v in DEFAULT_FAMILIES.items()}
    )

    def __init__(
        self,
        device: str = "cpu",
        threads: int = 6,
        checkpoints: Optional[List[str]] = None,
        router_default: str = "multilingual",
        laya_revision: str = "main",
        api_key: Optional[str] = None,
        log_level: str = "info",
        hf_home: str = "/data/hf",
        mock_mode: bool = False,
        provider: str = "gemini",
        gateway_url: Optional[str] = None,
        families: Optional[Dict[str, FamilyConfig]] = None,
        model_quick: Optional[str] = None,
        model_general: Optional[str] = None,
        model_code: Optional[str] = None,
        model_deep: Optional[str] = None,
    ):
        self.device = device
        self.threads = threads
        self.checkpoints = checkpoints if checkpoints is not None else ["english", "multilingual"]
        self.router_default = router_default
        self.laya_revision = laya_revision
        self.api_key = api_key
        self.log_level = log_level
        self.hf_home = hf_home
        self.mock_mode = mock_mode
        self.provider = provider
        self.gateway_url = gateway_url

        if families is not None:
            self.families = dict(families)
        else:
            self.families = {k: FamilyConfig(**asdict(v)) for k, v in DEFAULT_FAMILIES.items()}

        if model_quick is not None and "quick" in self.families:
            self.families["quick"].model = model_quick
        if model_general is not None and "general" in self.families:
            self.families["general"].model = model_general
        if model_code is not None and "code" in self.families:
            self.families["code"].model = model_code
        if model_deep is not None and "deep" in self.families:
            self.families["deep"].model = model_deep

    @property
    def model_quick(self) -> str:
        return self.get_model_for_family("quick")

    @property
    def model_general(self) -> str:
        return self.get_model_for_family("general")

    @property
    def model_code(self) -> str:
        return self.get_model_for_family("code")

    @property
    def model_deep(self) -> str:
        return self.get_model_for_family("deep")

    def get_family_config(self, family: str) -> FamilyConfig:
        """Resolve FamilyConfig for a given family name with graceful fallback."""
        fam = family.lower().strip()
        if fam in self.families:
            return self.families[fam]
        if "general" in self.families:
            return self.families["general"]
        return FamilyConfig(
            name=fam,
            criteria="general task",
            model="gemini-flash-latest",
            effort="medium",
        )

    def get_model_for_family(self, family: str) -> str:
        """Resolve model identifier for a given task family based on configuration."""
        return self.get_family_config(family).model

    def get_models_map(self) -> Dict[str, str]:
        """Return full family-to-model mapping dictionary."""
        return {name: f.model for name, f in self.families.items()}

    def get_criteria_map(self) -> Dict[str, str]:
        """Return full criteria dictionary mapping family names to prompt match descriptions."""
        return {name: f.criteria for name, f in self.families.items()}

    @classmethod
    def load(cls, options_path: str = "/data/options.json") -> "AppConfig":
        """Load configuration from Home Assistant options.json, environment, or defaults."""
        data: Dict[str, Any] = {}
        opt_file = Path(options_path)
        if opt_file.is_file():
            try:
                with open(opt_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"[config] Failed to load {options_path}: {e}")

        # Checkpoints parsing
        raw_cps = data.get("checkpoints") or os.environ.get("LAYA_CHECKPOINTS", "english,multilingual")
        if isinstance(raw_cps, str):
            checkpoints = [cp.strip() for cp in raw_cps.split(",") if cp.strip()]
        else:
            checkpoints = list(raw_cps)

        device = data.get("device") or os.environ.get("LAYA_DEVICE", "cpu")
        threads = int(data.get("threads") or os.environ.get("LAYA_THREADS", 6))
        router_default = data.get("router_default") or os.environ.get("LAYA_ROUTER_DEFAULT", "multilingual")
        laya_revision = data.get("laya_revision") or os.environ.get("LAYA_REVISION", "main")
        api_key = data.get("api_key") or os.environ.get("LAYA_API_KEY")
        if api_key == "":
            api_key = None

        log_level = data.get("log_level") or os.environ.get("LAYA_LOG_LEVEL", "info")
        hf_home = os.environ.get("HF_HOME", "/data/hf")
        mock_mode = os.environ.get("LAYA_MOCK_MODE", "").lower() in ("1", "true", "yes")

        provider = data.get("provider") or os.environ.get("LAYA_PROVIDER", "gemini")
        gateway_url = data.get("gateway_url") or os.environ.get("LAYA_GATEWAY_URL")
        if gateway_url == "":
            gateway_url = None

        # Build families map
        loaded_families: Dict[str, FamilyConfig] = {}
        raw_families = data.get("families")

        if isinstance(raw_families, list):
            for item in raw_families:
                if isinstance(item, dict) and "name" in item:
                    name = str(item["name"]).strip().lower()
                    tools_raw = item.get("allowed_tools")
                    tools_list = None
                    if isinstance(tools_raw, list):
                        tools_list = [str(t).strip() for t in tools_raw]
                    elif isinstance(tools_raw, str):
                        tools_list = [t.strip() for t in tools_raw.split(",") if t.strip()]

                    needs_mem = item.get("needs_memory")
                    if isinstance(needs_mem, str):
                        needs_mem = needs_mem.lower() in ("true", "1", "yes")

                    loaded_families[name] = FamilyConfig(
                        name=name,
                        criteria=str(item.get("criteria", "")).strip(),
                        model=str(item.get("model", "gemini-flash-latest")).strip(),
                        effort=item.get("effort"),
                        max_tokens=int(item["max_tokens"]) if item.get("max_tokens") is not None else None,
                        temperature=float(item["temperature"]) if item.get("temperature") is not None else None,
                        thinking_budget=int(item["thinking_budget"]) if item.get("thinking_budget") is not None else None,
                        needs_memory=needs_mem if needs_mem is not None else None,
                        allowed_tools=tools_list,
                    )
        elif isinstance(raw_families, dict):
            for name, item in raw_families.items():
                if isinstance(item, dict):
                    fam_name = str(name).strip().lower()
                    tools_raw = item.get("allowed_tools")
                    tools_list = None
                    if isinstance(tools_raw, list):
                        tools_list = [str(t).strip() for t in tools_raw]
                    elif isinstance(tools_raw, str):
                        tools_list = [t.strip() for t in tools_raw.split(",") if t.strip()]

                    needs_mem = item.get("needs_memory")
                    if isinstance(needs_mem, str):
                        needs_mem = needs_mem.lower() in ("true", "1", "yes")

                    loaded_families[fam_name] = FamilyConfig(
                        name=fam_name,
                        criteria=str(item.get("criteria", "")).strip(),
                        model=str(item.get("model", "gemini-flash-latest")).strip(),
                        effort=item.get("effort"),
                        max_tokens=int(item["max_tokens"]) if item.get("max_tokens") is not None else None,
                        temperature=float(item["temperature"]) if item.get("temperature") is not None else None,
                        thinking_budget=int(item["thinking_budget"]) if item.get("thinking_budget") is not None else None,
                        needs_memory=needs_mem if needs_mem is not None else None,
                        allowed_tools=tools_list,
                    )

        # Fallback to defaults or legacy model_* keys if no families provided
        if not loaded_families:
            for k, v in DEFAULT_FAMILIES.items():
                loaded_families[k] = FamilyConfig(**asdict(v))

            # Legacy options backward compatibility
            if data.get("model_quick") or os.environ.get("LAYA_MODEL_QUICK"):
                loaded_families["quick"].model = str(data.get("model_quick") or os.environ.get("LAYA_MODEL_QUICK"))
            if data.get("model_general") or os.environ.get("LAYA_MODEL_GENERAL"):
                loaded_families["general"].model = str(data.get("model_general") or os.environ.get("LAYA_MODEL_GENERAL"))
            if data.get("model_code") or os.environ.get("LAYA_MODEL_CODE"):
                loaded_families["code"].model = str(data.get("model_code") or os.environ.get("LAYA_MODEL_CODE"))
            if data.get("model_deep") or os.environ.get("LAYA_MODEL_DEEP"):
                loaded_families["deep"].model = str(data.get("model_deep") or os.environ.get("LAYA_MODEL_DEEP"))

        return cls(
            device=device,
            threads=threads,
            checkpoints=checkpoints,
            router_default=router_default,
            laya_revision=laya_revision,
            api_key=api_key,
            log_level=log_level,
            hf_home=hf_home,
            mock_mode=mock_mode,
            provider=provider,
            gateway_url=gateway_url,
            families=loaded_families,
        )

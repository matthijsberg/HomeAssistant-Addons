"""Configuration loader for Laya Router HA Add-on."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


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
    model_quick: str = "gemini-2.5-flash-lite"
    model_general: str = "gemini-flash-latest"
    model_code: str = "gemini-flash-latest"
    model_deep: str = "gemini-2.5-pro"
    gateway_url: Optional[str] = None

    def get_model_for_family(self, family: str) -> str:
        """Resolve model identifier for a given task family based on configuration."""
        fam = family.lower().strip()
        if fam == "quick":
            return self.model_quick
        elif fam == "code":
            return self.model_code
        elif fam == "deep":
            return self.model_deep
        else:
            return self.model_general

    def get_models_map(self) -> dict:
        """Return full family-to-model mapping dictionary."""
        return {
            "quick": self.model_quick,
            "general": self.model_general,
            "code": self.model_code,
            "deep": self.model_deep,
        }

    @classmethod
    def load(cls, options_path: str = "/data/options.json") -> "AppConfig":
        """Load configuration from Home Assistant options.json, environment, or defaults."""
        data = {}
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
        model_quick = data.get("model_quick") or os.environ.get("LAYA_MODEL_QUICK", "gemini-2.5-flash-lite")
        model_general = data.get("model_general") or os.environ.get("LAYA_MODEL_GENERAL", "gemini-flash-latest")
        model_code = data.get("model_code") or os.environ.get("LAYA_MODEL_CODE", "gemini-flash-latest")
        model_deep = data.get("model_deep") or os.environ.get("LAYA_MODEL_DEEP", "gemini-2.5-pro")
        gateway_url = data.get("gateway_url") or os.environ.get("LAYA_GATEWAY_URL")
        if gateway_url == "":
            gateway_url = None

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
            model_quick=model_quick,
            model_general=model_general,
            model_code=model_code,
            model_deep=model_deep,
            gateway_url=gateway_url,
        )

import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any, List


def extract_addon_config_surface(config_yaml_path: Path) -> Dict[str, Any]:
    """Extract security-relevant fields from add-on config.yaml."""
    surface = {
        "file": str(config_yaml_path),
        "host_network": False,
        "privileged": [],
        "full_access": False,
        "ingress": False,
        "ingress_port": None,
        "ports": {},
        "hassio_api": False,
        "hassio_role": "default",
        "homeassistant_api": False,
        "map": [],
        "apparmor": True,
    }
    if not config_yaml_path.exists():
        return surface

    try:
        content = config_yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        surface["host_network"] = bool(data.get("host_network", False))
        surface["privileged"] = data.get("privileged", [])
        surface["full_access"] = bool(data.get("full_access", False))
        surface["ingress"] = bool(data.get("ingress", False))
        surface["ingress_port"] = data.get("ingress_port")
        surface["ports"] = data.get("ports", {})
        surface["hassio_api"] = bool(data.get("hassio_api", False))
        surface["hassio_role"] = data.get("hassio_role", "default")
        surface["homeassistant_api"] = bool(data.get("homeassistant_api", False))
        surface["map"] = data.get("map", [])
        surface["apparmor"] = bool(data.get("apparmor", True))
    except Exception as e:
        surface["parse_error"] = str(e)

    return surface


def extract_dockerfile_surface(dockerfile_path: Path) -> Dict[str, Any]:
    """Extract user, exposed ports, and base image from Dockerfile."""
    surface = {
        "file": str(dockerfile_path),
        "base_image": None,
        "user": "root",  # default in Docker unless specified
        "exposed_ports": [],
        "has_entrypoint": False,
    }
    if not dockerfile_path.exists():
        return surface

    try:
        lines = dockerfile_path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line_str = line.strip()
            if line_str.upper().startswith("FROM "):
                surface["base_image"] = line_str.split()[1]
            elif line_str.upper().startswith("USER "):
                surface["user"] = line_str.split()[1]
            elif line_str.upper().startswith("EXPOSE "):
                parts = line_str.split()[1:]
                surface["exposed_ports"].extend(parts)
            elif line_str.upper().startswith("ENTRYPOINT ") or line_str.upper().startswith("CMD "):
                surface["has_entrypoint"] = True
    except Exception as e:
        surface["parse_error"] = str(e)

    return surface


def extract_code_surface(repo_dir: Path) -> Dict[str, Any]:
    """Scan python/js/sh code for high-risk constructs and HTTP endpoints."""
    surface = {
        "routes": [],
        "subprocess_calls": [],
        "raw_sql_calls": [],
        "eval_calls": [],
    }

    route_regex = re.compile(r"""@(app|router)\.(get|post|put|delete|patch)\s*\(\s*['"]([^'"]+)['"]""", re.IGNORECASE)
    subproc_regex = re.compile(r"""(subprocess\.Popen|subprocess\.run|os\.system|exec\(|eval\()""")
    sql_regex = re.compile(r"""(\.execute\s*\(\s*f['"]|\.execute\s*\(\s*['"].*%s)""")

    for root, _, files in os.walk(repo_dir):
        for f in files:
            if not f.endswith((".py", ".js", ".ts", ".sh")):
                continue
            f_path = Path(root) / f
            try:
                content = f_path.read_text(encoding="utf-8", errors="ignore")
                rel_p = str(f_path.relative_to(repo_dir))
                
                # Check routes
                for m in route_regex.finditer(content):
                    surface["routes"].append({
                        "file": rel_p,
                        "method": m.group(2).upper(),
                        "path": m.group(3),
                    })
                
                # Check subprocess / eval
                for m in subproc_regex.finditer(content):
                    surface["subprocess_calls"].append({
                        "file": rel_p,
                        "construct": m.group(1),
                    })
                
                # Check raw SQL interpolation
                for m in sql_regex.finditer(content):
                    surface["raw_sql_calls"].append({
                        "file": rel_p,
                        "construct": m.group(1),
                    })
            except Exception:
                continue

    return surface


def build_deployment_manifest(target_dir: Path) -> Dict[str, Any]:
    """
    Build complete deployment surface manifest for the target repository or folder.
    """
    manifest = {
        "target_path": str(target_dir),
        "addon_config": None,
        "dockerfile": None,
        "code_surface": None,
        "cross_artifact_risks": [],
    }

    config_yaml = target_dir / "config.yaml"
    if config_yaml.exists():
        manifest["addon_config"] = extract_addon_config_surface(config_yaml)

    dockerfile = target_dir / "Dockerfile"
    if dockerfile.exists():
        manifest["dockerfile"] = extract_dockerfile_surface(dockerfile)

    manifest["code_surface"] = extract_code_surface(target_dir)

    # Cross-artifact analysis
    addon_cfg = manifest["addon_config"]
    code_srf = manifest["code_surface"]

    if addon_cfg and code_srf:
        # Check: Unauthenticated routes exposed via host_network
        if addon_cfg.get("host_network") and code_srf.get("routes"):
            manifest["cross_artifact_risks"].append({
                "type": "host_network_exposed_routes",
                "severity": "HIGH",
                "cwe": "CWE-306",
                "message": (
                    f"Add-on enables host_network: true and registers {len(code_srf['routes'])} HTTP routes. "
                    "Endpoints will be directly exposed on the host LAN interface without Home Assistant Ingress authentication."
                ),
            })

        # Check: Privileged capabilities or full_access with subprocess execution
        if (addon_cfg.get("privileged") or addon_cfg.get("full_access")) and code_srf.get("subprocess_calls"):
            manifest["cross_artifact_risks"].append({
                "type": "privileged_subprocess_execution",
                "severity": "HIGH",
                "cwe": "CWE-250",
                "message": (
                    "Add-on runs with elevated privileges/full_access and performs subprocess/eval executions. "
                    "Any command injection in code could grant privileged container escape."
                ),
            })

        # Check: Read-write map to homeassistant_config with file operations
        rw_ha_maps = [m for m in addon_cfg.get("map", []) if "homeassistant" in m and ":rw" in m]
        if rw_ha_maps:
            manifest["cross_artifact_risks"].append({
                "type": "readwrite_homeassistant_config_map",
                "severity": "MEDIUM",
                "cwe": "CWE-732",
                "message": (
                    f"Add-on mounts Home Assistant configuration directory with write permissions ({rw_ha_maps}). "
                    "Flaws in file handling can compromise Home Assistant Core automations and storage."
                ),
            })

    return manifest

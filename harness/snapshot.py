import os
import hashlib
import fnmatch
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional

DENIED_GLOBS = [
    "**/data/**",
    "**/ssl/**",
    "**/backup/**",
    "**/.storage/**",
    "**/secrets.yaml",
    "**/.env*",
    "**/.hermes/.env*",
    "**/*.db",
    "**/*.sqlite*",
    "**/*.pem",
    "**/*.key",
    "**/id_*",
    "**/known_hosts",
    "**/home-assistant_v2.db*",
    "**/*.log",
]

DEFAULT_ALLOW_LIST = [
    "/addons",
    "/homeassistant",
    "/addon_configs",
    "/share/projects",
]


def is_path_denied(rel_path: str, abs_path: str) -> bool:
    """Check if file matches any denied glob pattern."""
    p_norm = str(Path(abs_path).resolve())
    r_norm = str(rel_path).replace("\\", "/")
    filename = Path(p_norm).name
    
    for pattern in DENIED_GLOBS:
        if fnmatch.fnmatch(p_norm, pattern) or fnmatch.fnmatch(r_norm, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    return False


def validate_target_path(target_path: str, allow_list: Optional[List[str]] = None) -> Path:
    """
    Validate that target path exists and resolves inside the allow-list.
    Raises ValueError if denied or outside allow-list.
    """
    resolved = Path(target_path).resolve()
    if not resolved.exists():
        raise ValueError(f"Target path does not exist: {target_path}")

    active_allow = allow_list or DEFAULT_ALLOW_LIST
    is_allowed = False
    for allowed_prefix in active_allow:
        try:
            allowed_res = Path(allowed_prefix).resolve()
            if resolved == allowed_res or allowed_res in resolved.parents:
                is_allowed = True
                break
        except Exception:
            continue

    if not is_allowed:
        raise ValueError(
            f"Access denied: path '{target_path}' (resolved: '{resolved}') is outside allowed roots: {active_allow}"
        )

    # Check against top-level denied paths
    for pattern in ["/data", "/ssl", "/backup", "/root"]:
        try:
            p_res = Path(pattern).resolve()
            if resolved == p_res or p_res in resolved.parents:
                raise ValueError(f"Path is explicitly forbidden: {target_path}")
        except Exception:
            pass

    return resolved


def compute_tree_content_hash(root_dir: Path) -> str:
    """
    Compute deterministic SHA-256 hash of all non-denied files in directory tree.
    Sorted by path for reproducibility.
    """
    hasher = hashlib.sha256()
    file_entries = []

    for root, _, files in os.walk(root_dir):
        for f in sorted(files):
            abs_p = Path(root) / f
            try:
                rel_p = abs_p.relative_to(root_dir)
            except ValueError:
                rel_p = abs_p
            if is_path_denied(str(rel_p), str(abs_p)):
                continue
            file_entries.append((str(rel_p), abs_p))

    file_entries.sort(key=lambda x: x[0])
    for rel_p, abs_p in file_entries:
        try:
            hasher.update(rel_p.encode("utf-8"))
            with open(abs_p, "rb") as fp:
                while chunk := fp.read(65536):
                    hasher.update(chunk)
        except (OSError, PermissionError):
            continue

    return hasher.hexdigest()[:16]


def compute_snapshot_id(target_path: Path) -> Tuple[str, str]:
    """
    Returns (snapshot_id, ladder_mode)
    Ladder:
    1. clean git -> git commit hash (e.g. '47099edfe')
    2. dirty git -> 'commit:content_hash' (e.g. '47099ed:9f8e21a')
    3. no vcs    -> 'content:content_hash' (e.g. 'content:3c7f1a0')
    """
    if target_path.is_file():
        hasher = hashlib.sha256()
        with open(target_path, "rb") as fp:
            while chunk := fp.read(65536):
                hasher.update(chunk)
        return f"content:{hasher.hexdigest()[:16]}", "single_file"

    is_git_repo = (target_path / ".git").is_dir()
    if is_git_repo:
        try:
            # Check commit
            rev_res = subprocess.run(
                ["git", "-C", str(target_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if rev_res.returncode == 0:
                commit_hash = rev_res.stdout.strip()[:10]
                # Check status
                status_res = subprocess.run(
                    ["git", "-C", str(target_path), "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                is_dirty = bool(status_res.stdout.strip())
                if not is_dirty:
                    return commit_hash, "clean_git"
                else:
                    tree_hash = compute_tree_content_hash(target_path)
                    return f"{commit_hash}:{tree_hash}", "dirty_git"
        except Exception:
            pass

    # Fallback to pure content hash
    tree_hash = compute_tree_content_hash(target_path)
    return f"content:{tree_hash}", "no_vcs"

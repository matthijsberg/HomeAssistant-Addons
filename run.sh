#!/command/with-contenv bash
# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────
# Hermes Agent HA Add-on Entrypoint
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

ADDON_VERSION="$(grep -m1 '^version:' "$(dirname "${BASH_SOURCE[0]}")/config.yaml" 2>/dev/null | cut -d'"' -f2 || echo "2.4.0")"

# ── Section 1: Read options ──────────────────────────────────────────
OPTIONS_FILE="/data/options.json"
if [ ! -f "$OPTIONS_FILE" ]; then
    echo "[run] FATAL: $OPTIONS_FILE not found"
    exit 1
fi

opt() { jq -r ".${1} // empty" "$OPTIONS_FILE"; }
opt_bool() { jq -r ".${1} // false" "$OPTIONS_FILE"; }

LOG_LEVEL=$(opt log_level)
LOG_LEVEL="${LOG_LEVEL:-info}"

log_level_num() {
    case "${1,,}" in
        trace) echo 0 ;;
        debug) echo 1 ;;
        info) echo 2 ;;
        notice) echo 3 ;;
        warning|warn) echo 4 ;;
        error) echo 5 ;;
        fatal) echo 6 ;;
        *) echo 2 ;;
    esac
}
CURRENT_LOG_LEVEL_NUM=$(log_level_num "$LOG_LEVEL")

log() {
    local level="info"
    case "$1" in
        trace|debug|info|notice|warning|error|fatal) level="$1"; shift ;;
    esac
    local lvl_num
    lvl_num=$(log_level_num "$level")
    if [ "$lvl_num" -ge "${CURRENT_LOG_LEVEL_NUM:-2}" ]; then
        local now
        now="$(date +'%Y-%m-%d %H:%M:%S')"
        echo "[$now] [v${ADDON_VERSION}] $*"
    fi
}
export -f log_level_num log 2>/dev/null || true
export ADDON_VERSION LOG_LEVEL CURRENT_LOG_LEVEL_NUM

GIT_URL=$(opt git_url)
GIT_REF=$(opt git_ref)
GIT_TOKEN=$(opt git_token)
AUTO_UPDATE=$(opt_bool auto_update)
HASS_URL=$(opt hass_url)
HASS_URL="${HASS_URL:-http://supervisor/core}"
[ -n "$HASS_URL" ] && export HASS_URL

HASS_TOKEN=$(opt homeassistant_token)
HASS_TOKEN="${HASS_TOKEN:-${SUPERVISOR_TOKEN:-}}"
[ -n "$HASS_TOKEN" ] && export HASS_TOKEN
# shellcheck disable=SC2034  # consumed by resolve_profiles in profile-init.sh
HERMES_HOME_DIR=$(opt hermes_home)
# shellcheck disable=SC2034  # consumed by resolve_profiles in profile-init.sh
# Preserve the documented default even when upgrading from an options.json that
# predates profiles_base, while still allowing an explicit empty value to keep
# legacy flat profile directories.
PROFILES_BASE=$(jq -r 'if has("profiles_base") then (.profiles_base // "") else ".hermes/profiles" end' "$OPTIONS_FILE")
ENABLE_DASHBOARD=$(opt_bool enable_dashboard)
ENABLE_TERMINAL=$(opt_bool enable_terminal)
ENABLE_API=$(opt_bool enable_api)
ENABLE_DESKTOP_BACKEND=$(opt_bool enable_desktop_backend)
ACCESS_PASSWORD=$(opt access_password)

# Validate and normalize the API credential before nginx, installation, or any
# Hermes service can start. The disabled API preserves existing password behavior.
API_SERVER_LIB=""
for _candidate in \
    "/usr/local/lib/hermes-api-server.sh" \
    "$(dirname "${BASH_SOURCE[0]}")/api-server.sh"; do
    if [ -f "$_candidate" ]; then
        API_SERVER_LIB="$_candidate"
        break
    fi
done
if [ -z "$API_SERVER_LIB" ]; then
    echo "[run] FATAL: api-server.sh not found" >&2
    exit 1
fi
# shellcheck source=api-server.sh
source "$API_SERVER_LIB"
if ! api_server_read_json_string \
    "$OPTIONS_FILE" access_password "" ACCESS_PASSWORD; then
    echo "[run] FATAL: could not read access_password" >&2
    exit 1
fi
api_server_validate_env_records "$OPTIONS_FILE" || exit 1
api_server_validate_options || exit 1

# Per-slot gateway supervision helpers (upstream v1.3.1/v1.3.2). The launcher
# keeps add-on-owned API/profile settings authoritative, the supervisor owns
# the gateway's whole process tree, and the logger tees output to both the
# add-on log and the profile's gateway.log.
find_addon_helper() {
    local installed="$1" local_name="$2" require_exec="${3:-false}" candidate
    for candidate in "$installed" "$(dirname "${BASH_SOURCE[0]}")/$local_name"; do
        if [ "$require_exec" = "true" ] && [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"; return 0
        elif [ "$require_exec" != "true" ] && [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"; return 0
        fi
    done
    echo "[run] FATAL: $local_name not found${3:+ or not executable}" >&2
    return 1
}
GATEWAY_LAUNCHER=$(find_addon_helper /usr/local/lib/hermes-gateway-launcher.py gateway-launcher.py) || exit 1
GATEWAY_CHILD=$(find_addon_helper /usr/local/lib/hermes-gateway-child.sh gateway-child.sh true) || exit 1
GATEWAY_SUPERVISOR=$(find_addon_helper /usr/local/lib/hermes-gateway-supervisor.py gateway-supervisor.py) || exit 1
GATEWAY_LOGGER=$(find_addon_helper /usr/local/lib/hermes-gateway-logger.py gateway-logger.py) || exit 1

USE_HA_SSL_CERT=$(opt_bool use_ha_ssl_cert)
HA_SSL_CERTFILE=$(opt ha_ssl_certfile); HA_SSL_CERTFILE="${HA_SSL_CERTFILE:-fullchain.pem}"
HA_SSL_KEYFILE=$(opt ha_ssl_keyfile); HA_SSL_KEYFILE="${HA_SSL_KEYFILE:-privkey.pem}"
ENABLE_PERIODIC_BACKUPS=$(jq -r 'if has("enable_periodic_backups") then .enable_periodic_backups else true end' "$OPTIONS_FILE" 2>/dev/null || echo true)
PERIODIC_BACKUP_INTERVAL_HOURS=$(jq -r 'if has("periodic_backup_interval_hours") then (.periodic_backup_interval_hours // 24) else 24 end' "$OPTIONS_FILE" 2>/dev/null || echo 24)
PERIODIC_BACKUP_KEEP_COUNT=$(jq -r 'if has("periodic_backup_keep_count") then (.periodic_backup_keep_count // 7) else 7 end' "$OPTIONS_FILE" 2>/dev/null || echo 7)
export ENABLE_PERIODIC_BACKUPS PERIODIC_BACKUP_INTERVAL_HOURS PERIODIC_BACKUP_KEEP_COUNT

# ── Section 2: System setup ─────────────────────────────────────────
# Timezone: sync /etc/localtime + /etc/timezone from HA's TZ env var
if [ -n "$TZ" ] && [[ "$TZ" != *..* ]] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
    log "[run] Timezone: $TZ"
fi

# IPv4 DNS priority (always enabled — no practical IPv6-only home networks)
if grep -q "^precedence ::ffff:0:0/96  100" /etc/gai.conf 2>/dev/null; then
    : # already active
elif grep -q "^#[[:space:]]*precedence ::ffff:0:0/96  100" /etc/gai.conf 2>/dev/null; then
    sed -i 's/^#[[:space:]]*\(precedence ::ffff:0:0\/96  100\)/\1/' /etc/gai.conf
else
    echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf
fi

# HA's s6 supervises this wrapper; keep upstream Hermes in foreground mode.
export HERMES_GATEWAY_NO_SUPERVISE=1

# ── Section 3: Profile resolution ────────────────────────────────────
# Source the profile-init library. Resolves the `profiles` list (or legacy
# `hermes_home`) into PROFILE_* arrays and per-profile port arrays.
PROFILE_INIT_LIB=""
for _candidate in \
    "$(dirname "${BASH_SOURCE[0]}")/profile-init.sh" \
    "/usr/local/lib/hermes-profile-init.sh"; do
    if [ -f "$_candidate" ]; then
        PROFILE_INIT_LIB="$_candidate"
        break
    fi
done
if [ -z "$PROFILE_INIT_LIB" ]; then
    log "[run] FATAL: profile-init.sh not found"
    exit 1
fi
# shellcheck source=profile-init.sh
source "$PROFILE_INIT_LIB"

resolve_profiles || exit 1

PRIMARY_HOME="${PROFILE_HOMES[0]}"
export HERMES_HOME="$PRIMARY_HOME"

log "[run] Profiles (${#PROFILE_DIRS[@]}):"
for i in "${!PROFILE_DIRS[@]}"; do
    prefix_label="${PROFILE_PATH_PREFIX[$i]:-/}"
    log "[run]   [$i] ${PROFILE_NAMES[$i]} → ${PROFILE_HOMES[$i]} (route: $prefix_label)"
done

BACKUP_ROOT="/backup/hermes"
BACKUP_SETUP_LIB=""
for _candidate in \
    "$(dirname "${BASH_SOURCE[0]}")/backup-setup.sh" \
    "/usr/local/lib/hermes-backup-setup.sh"; do
    if [ -f "$_candidate" ]; then
        BACKUP_SETUP_LIB="$_candidate"
        break
    fi
done
if [ -n "$BACKUP_SETUP_LIB" ]; then
    # shellcheck source=backup-setup.sh
    source "$BACKUP_SETUP_LIB"
    configure_backup_storage
fi

# ── Section 3b: System paths ─────────────────────────────────────────
BREW_DIR="$HOME/.linuxbrew"
NODE_DIR="$HOME/.npm-global"
GO_DIR="$HOME/.go"
CERTS_DIR="$HOME/.certs"
INGRESS_PORT=49169
HTTP_PORT=8080
HTTPS_PORT=8443
DESKTOP_BACKEND_PORT=9119

# Desktop backend lifecycle helpers. The feature is opt-in and validation runs
# before nginx or any Hermes service starts.
DESKTOP_BACKEND_LIB=""
for _candidate in \
    "$(dirname "${BASH_SOURCE[0]}")/desktop-backend.sh" \
    "/usr/local/lib/hermes-desktop-backend.sh"; do
    if [ -f "$_candidate" ]; then
        DESKTOP_BACKEND_LIB="$_candidate"
        break
    fi
done
if [ -z "$DESKTOP_BACKEND_LIB" ]; then
    log "[run] FATAL: desktop-backend.sh not found"
    exit 1
fi
# shellcheck source=desktop-backend.sh
source "$DESKTOP_BACKEND_LIB"
desktop_backend_validate_options || exit 1

# Start nginx early with loading page (replaced with full config after setup)
cat > /etc/nginx/nginx.conf << LOADCONF
worker_processes 1;
pid /var/run/nginx.pid;
error_log stderr warn;
events { worker_connections 64; }
http {
    server {
        listen ${INGRESS_PORT};
        location / { root /var/www; try_files /loading.html =404; }
        location = /health { return 200 "OK\n"; add_header Content-Type text/plain; }
    }
}
LOADCONF
nginx
log "[run] Loading page active (ingress: $INGRESS_PORT)"

# Create persistent directories (only system infra — Hermes creates its own)
mkdir -p "$NODE_DIR/lib" "$GO_DIR/bin" "$CERTS_DIR"
for i in "${!PROFILE_DIRS[@]}"; do
    mkdir -p "${PROFILE_HOMES[$i]}"
done

# Go
export GOPATH="$GO_DIR"
export GOBIN="$GO_DIR/bin"
export PATH="$GOBIN:$PATH"

# Node global
export NPM_CONFIG_PREFIX="$NODE_DIR"
export PATH="$NODE_DIR/bin:$PATH"

# Homebrew: sync from image on first boot, then persistent
BREW_IMAGE="/home/linuxbrew/.linuxbrew"
if [ -d "$BREW_IMAGE" ] && [ ! -d "$BREW_DIR/bin" ]; then
    log "[run] First boot: syncing Homebrew to persistent storage..."
    rsync -a "$BREW_IMAGE/" "$BREW_DIR/"
    log "[run] Homebrew synced"
fi
if [ -d "$BREW_DIR/bin" ]; then
    export HOMEBREW_PREFIX="$BREW_DIR"
    export HOMEBREW_CELLAR="$BREW_DIR/Cellar"
    export HOMEBREW_REPOSITORY="$BREW_DIR/Homebrew"
    export PATH="$BREW_DIR/sbin:$BREW_DIR/bin:$PATH"
fi

# Snapshot PATH before adding any per-profile venv — used when building per-shell PATH.
BASE_PATH="$PATH"

# ── Section 4: Shell environment ─────────────────────────────────────
# ~/.bashrc: persistent, create-if-missing (user-editable)
if [ ! -f /config/.bashrc ]; then
    cat > /config/.bashrc << 'BASHRC'
# Source Hermes API keys (.env first, then profile overrides)
[ -f "${HERMES_HOME:=$HOME/.hermes}/.env" ] && set -a && . "$HERMES_HOME/.env" && set +a
# Source Hermes environment (paths, variables, tokens — overrides .env)
[ -f ~/.hermes_profile ] && . ~/.hermes_profile

# If not running interactively, stop here
case $- in
    *i*) ;;
      *) return;;
esac

# Working directory
cd ~

# History
HISTCONTROL=ignoreboth
shopt -s histappend
HISTSIZE=1000000
HISTFILESIZE=1000000

# Shell options
shopt -s checkwinsize
shopt -s globstar

# lesspipe
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# Prompt
PS1='\[\033[01;34m\]\w\[\033[00m\]\$ '

# Colors
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias diff='diff --color=auto'
    alias egrep='egrep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias grep='grep --color=auto'
    alias ls='ls --color=auto'
fi

# ls aliases
alias l='ls -CF'
alias la='ls -A'
alias ll='ls -l'
alias lla='ls -Al'

# Alias definitions
[ -f ~/.bash_aliases ] && . ~/.bash_aliases

# Bash completion
if ! shopt -oq posix; then
    if [ -f /usr/share/bash-completion/bash_completion ]; then
        . /usr/share/bash-completion/bash_completion
    elif [ -f /etc/bash_completion ]; then
        . /etc/bash_completion
    fi
fi

# Command-not-found handler
if [ -x /usr/lib/command-not-found ]; then
    command_not_found_handle() { /usr/lib/command-not-found -- "$1"; return $?; }
fi
BASHRC
    log "[run] Created default .bashrc"
fi

# ~/.profile: persistent, create-if-missing (user-editable)
# Hermes autostart is handled by /usr/local/bin/start-hermes (via ttyd),
# not .profile, to avoid recursion when Hermes spawns login subshells.
if [ ! -f /config/.profile ]; then
    cat > /config/.profile << 'PROFILE'
# Source .bashrc for paths and aliases
[ -f ~/.bashrc ] && . ~/.bashrc
PROFILE
    log "[run] Created default .profile"
fi

# ── Section 5: Hermes installation (single shared install) ───────────
# Upstream Hermes itself supports multiple profiles via per-profile HERMES_HOME
# directories (see https://hermes-agent.nousresearch.com/docs/user-guide/profiles).
# We therefore install one shared clone + venv and point each profile's
# HERMES_HOME at its own data directory at gateway start time.
SRC_DIR="$HOME/.hermes/hermes-agent"
VENV_DIR="$SRC_DIR/venv"
MARKER_FILE="$HOME/.hermes_install"

compute_marker() {
    local ref="${GIT_REF:-$(cd "$SRC_DIR" 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}"
    local hash
    hash="$(cd "$SRC_DIR" 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo none)"
    local subs
    subs="$(find "$SRC_DIR" -mindepth 2 -maxdepth 2 -name pyproject.toml -print 2>/dev/null | while IFS= read -r pyproject; do basename "$(dirname "$pyproject")"; done | sort | paste -sd,)"
    echo "${GIT_URL}|${ref}|${hash}|${subs}"
}

install_needed() {
    local current
    current=$(compute_marker)
    if [ ! -f "$MARKER_FILE" ]; then return 0; fi
    if [ "$(cat "$MARKER_FILE")" != "$current" ]; then return 0; fi
    if [ ! -f "$VENV_DIR/bin/activate" ]; then return 0; fi
    if [ ! -f "$VENV_DIR/bin/hermes" ]; then return 0; fi
    return 1
}

# Honor the selected checkout's .python-version (upstream v1.3.4). Older
# revisions without that file keep the historical Python 3.11 default.
required_python_version() {
    local version="3.11"
    if [ -e "$SRC_DIR/.python-version" ] || [ -L "$SRC_DIR/.python-version" ]; then
        version=$(cat "$SRC_DIR/.python-version") || return 1
    fi
    if [[ ! "$version" =~ ^3\.[0-9]+(\.[0-9]+)?$ ]]; then
        log "[run] FATAL: .python-version must contain 3.MINOR or 3.MINOR.PATCH" >&2
        return 1
    fi
    printf '%s\n' "$version"
}

hermes_runtime_works() {
    [ -f "$VENV_DIR/bin/activate" ] && [ -x "$VENV_DIR/bin/hermes" ] && \
        [ -x "$VENV_DIR/bin/python" ] || return 1
    local probe_dir status=0
    probe_dir=$(mktemp -d) || return 1
    # Imports must exercise the installed CLI/config dependencies without writing
    # bytecode or touching the user's profile, even before initial scaffolding.
    (
        cd "$SRC_DIR" || exit 1
        HOME="$probe_dir" HERMES_HOME="$probe_dir" HERMES_PROFILE="" \
            "$VENV_DIR/bin/python" -B -c '
import sys
expected = tuple(map(int, sys.argv[1].split(".")))
if sys.version_info[:len(expected)] != expected:
    sys.exit(1)
import hermes_cli.main
import hermes_cli.config
' "$1"
    ) || status=$?
    rm -rf -- "$probe_dir"
    return "$status"
}

install_hermes_core() {
    mkdir -p "$(dirname "$SRC_DIR")"

    # Clone if missing
    if [ ! -d "$SRC_DIR/.git" ]; then
        log "[run] Cloning Hermes Agent..."
        local clone_url="$GIT_URL"
        if [ -n "$GIT_TOKEN" ]; then
            clone_url=$(echo "$GIT_URL" | sed "s|https://|https://${GIT_TOKEN}@|")
        fi
        local clone_args=()
        if [ -n "$GIT_REF" ]; then
            clone_args+=(--branch "$GIT_REF")
        fi
        git clone "${clone_args[@]}" "$clone_url" "$SRC_DIR"
        (cd "$SRC_DIR" && git submodule update --init --recursive 2>/dev/null || true)
        log "[run] Clone complete: $(cd "$SRC_DIR" && git log --oneline -1)"
    fi

    # Auto-update (stash local changes, pull, restore)
    if [ "$AUTO_UPDATE" = "true" ] && [ -d "$SRC_DIR/.git" ]; then
        log "[run] Pulling latest changes..."
        (
            cd "$SRC_DIR"
            git stash --quiet 2>/dev/null || true
            git pull --ff-only 2>/dev/null || log "[run] Warning: git pull failed (branch may have diverged)"
            git stash pop --quiet 2>/dev/null || true
            git submodule update --init --recursive 2>/dev/null || true
        )
    fi

    # Resolve the selected checkout's pin after clone/update, never from the image.
    local python_version backup_dir rebuild=false
    python_version=$(required_python_version) || return 1
    if ! hermes_runtime_works "$python_version"; then
        rebuild=true
        log "[run] venv missing, broken, or not on Python $python_version — rebuilding"
    fi
    if install_needed || [ "$rebuild" = "true" ]; then
        # Build at the final path so executable shebangs remain valid. Keep the
        # old environment until a replacement passes installation and imports.
        # Ordinary source updates retain a healthy venv and its extra packages.
        backup_dir=$(mktemp -d "${VENV_DIR}.backup.XXXXXX") || return 1
        if [ "$rebuild" = "true" ] && { [ -e "$VENV_DIR" ] || [ -L "$VENV_DIR" ]; }; then
            mv -- "$VENV_DIR" "$backup_dir/venv" || return 1
            log "[run] Previous venv saved at $backup_dir/venv until install succeeds"
        fi
        log "[run] Installing Hermes with Python $python_version (editable)..."
        if (
            if [ "$rebuild" = "true" ]; then
                uv venv "$VENV_DIR" --python "$python_version" || exit 1
            fi
            cd "$SRC_DIR" || exit 1
            uv pip install --python "$VENV_DIR/bin/python" -e ".[all,dev]" 2>&1 | tail -5 || exit 1
            if [ -f "$SRC_DIR/mini-swe-agent/pyproject.toml" ]; then
                uv pip install --python "$VENV_DIR/bin/python" -e "$SRC_DIR/mini-swe-agent" 2>&1 | tail -3 || exit 1
            fi
            if [ -f "$SRC_DIR/tinker-atropos/pyproject.toml" ]; then
                uv pip install --python "$VENV_DIR/bin/python" -e "$SRC_DIR/tinker-atropos" 2>&1 | tail -3 || exit 1
            fi
            hermes_runtime_works "$python_version" || exit 1
            compute_marker > "$backup_dir/marker" || exit 1
            mv -- "$backup_dir/marker" "$MARKER_FILE" || exit 1
        ); then
            rm -rf -- "$backup_dir"
            log "[run] Install complete"
        else
            log "[run] FATAL: Python $python_version install/import validation failed" >&2
            if [ "$rebuild" = "true" ]; then
                rm -rf -- "$VENV_DIR"
                if [ -e "$backup_dir/venv" ] || [ -L "$backup_dir/venv" ]; then
                    mv -- "$backup_dir/venv" "$VENV_DIR" || return 1
                    log "[run] Previous venv restored" >&2
                fi
            fi
            rm -rf -- "$backup_dir"
            return 1
        fi
    else
        log "[run] Install up to date (marker and Python runtime match)"
    fi

    # Link image-installed npm packages into project node_modules
    if [ ! -e "$SRC_DIR/node_modules/agent-browser" ]; then
        mkdir -p "$SRC_DIR/node_modules"
        ln -snf /usr/lib/node_modules/agent-browser "$SRC_DIR/node_modules/agent-browser"
        (cd "$SRC_DIR" && npm audit fix --silent 2>/dev/null || true)
        log "[run] Linked agent-browser into project"
    fi

    # Build dashboard web frontend (single build, shared by every profile)
    if [ -f "$SRC_DIR/web/package.json" ]; then
        local rebuild="false"
        local status_file
        status_file="$(mktemp)"

        if ! /usr/local/bin/hermes-dashboard-patches "$SRC_DIR" "$status_file"; then
            log "[run] WARNING: dashboard compatibility patch failed - continuing startup"
        fi
        if [ -s "$status_file" ]; then
            rebuild="true"
        fi
        rm -f "$status_file"

        if grep -Eq '(src|href)="/assets/' "$SRC_DIR/hermes_cli/web_dist/index.html" 2>/dev/null; then
            rebuild="true"
        fi

        if [ "$rebuild" = "true" ] || [ ! -d "$SRC_DIR/hermes_cli/web_dist/assets" ]; then
            log "[run] Building dashboard frontend..."
            if (cd "$SRC_DIR/web" && npm install --silent 2>&1 | tail -3 && npx vite build --outDir ../hermes_cli/web_dist --emptyOutDir 2>&1 | tail -3); then
                log "[run] Dashboard frontend built"
            else
                log "[run] Warning: dashboard frontend build failed (dashboard will not be available)"
            fi
        fi
    fi
}

install_hermes_core

# Keep the gateway process recognizable to Hermes core without replacing
# Python's argv[0] with a name that breaks Linux venv discovery. A relative
# symlink stays valid when the persisted add-on config is restored elsewhere.
GATEWAY_PYTHON="$VENV_DIR/bin/hermes-gateway"
if [ -e "$GATEWAY_PYTHON" ] && [ ! -L "$GATEWAY_PYTHON" ]; then
    log "[run] FATAL: reserved gateway interpreter path already exists" >&2
    exit 1
fi
ln -snf python "$GATEWAY_PYTHON"

# Activate the shared venv for any tooling (e.g. dashboard module probe).
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Verify version
HERMES_VERSION="$("$VENV_DIR/bin/hermes" --version 2>/dev/null | head -1 || echo "unknown")"
export HERMES_VERSION
log "[run] Hermes version: $HERMES_VERSION"
desktop_backend_validate_runtime || exit 1

# Wrap `hermes` so a bare `hermes backup` lands in /backup. Any reinstall
# (add-on install, venv rebuild, or Hermes' own `hermes update`) regenerates
# bin/hermes, so detect the wrapper by its marker instead of trusting a
# possibly stale hermes.real, and re-wrap whatever entry point is current.
if ! grep -q 'hermes-addon-backup-wrapper' "$VENV_DIR/bin/hermes" 2>/dev/null; then
    mv -f "$VENV_DIR/bin/hermes" "$VENV_DIR/bin/hermes.real"
    sed "s|HERMES_REAL_BIN_PLACEHOLDER|$VENV_DIR/bin/hermes.real|g; s|BACKUP_ROOT_PLACEHOLDER|$BACKUP_ROOT|g" \
        /usr/local/lib/hermes-wrapper.sh.tpl > "$VENV_DIR/bin/hermes"
    chmod +x "$VENV_DIR/bin/hermes"
fi

# ── Section 6: Initial config scaffolding (per profile) ──────────────
scaffold_profile_files() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local name="${PROFILE_NAMES[$i]}"

    if [ ! -f "$home/.env" ] && [ -f "$SRC_DIR/.env.example" ]; then
        cp -p "$SRC_DIR/.env.example" "$home/.env"
        chmod 600 "$home/.env"
        log "[run] [$name] Created .env from source example (chmod 600)"
    fi
    if [ ! -f "$home/config.yaml" ] && [ -f "$SRC_DIR/cli-config.yaml.example" ]; then
        cp -p "$SRC_DIR/cli-config.yaml.example" "$home/config.yaml"
        log "[run] [$name] Created config.yaml from source example"
    fi
    if [ ! -f "$home/SOUL.md" ]; then
        cat > "$home/SOUL.md" << 'SOUL_EOF'
# Hermes Agent Persona

<!--
This file defines the agent's personality and tone.
The agent will embody whatever you write here.
Edit this to customize how Hermes communicates with you.

Examples:
  - "You are a warm, playful assistant who uses kaomoji occasionally."
  - "You are a concise technical expert. No fluff, just facts."
  - "You speak like a friendly coworker who happens to know everything."

This file is loaded fresh each message -- no restart needed.
Delete the contents (or this file) to use the default personality.
-->
SOUL_EOF
        log "[run] [$name] Created SOUL.md template"
    fi

    if [ ! -f "$home/skills/homeassistant/SKILL.md" ]; then
        mkdir -p "$home/skills/homeassistant"
        cat > "$home/skills/homeassistant/SKILL.md" << 'HA_SKILL_EOF'
---
name: homeassistant
description: Home Assistant smart home control & Voice Assist skill. Query entity states, control devices, trigger scenes/automations, and inspect smart home history.
---

# Home Assistant Smart Home & Voice Assist Skill

Use this skill whenever the user asks about smart home status, wants to control Home Assistant entities, or interacts via Voice Assist.

## Guidelines
1. **Identify Entities:** Look up entities by domain and area (e.g. `light.living_room`, `climate.hallway`).
2. **Execute Actions:** Use Home Assistant tools (`ha_call_service`, MCP tools) to perform state changes.
3. **Confirm Actions:** Report concise confirmation back to the user (e.g., "Living room light set to 70%").
4. **Safety:** Confirm before unlocking doors or changing security alarms unless explicitly instructed.

## Voice Assist Guidelines
1. **Concise Spoken Responses:** Keep answers short, clear, and natural to read aloud.
2. **Clean Output:** Avoid markdown tables, code blocks, or heavy lists when replying to voice queries so Text-to-Speech (TTS) engines synthesize natural audio.
HA_SKILL_EOF
        log "[run] [$name] Created Home Assistant skill template"
    fi

    local hass_token="${HASS_TOKEN:-${SUPERVISOR_TOKEN:-}}"
    local hass_url="${HASS_URL:-http://supervisor/core}"
    if [ -n "$hass_token" ] && [ -f "$home/config.yaml" ]; then
        local mcp_tool=""
        for candidate_tool in "$(dirname "${BASH_SOURCE[0]}")/ha_mcp_config.py" "/usr/local/bin/hermes-ha-mcp-config"; do
            if [ -f "$candidate_tool" ]; then mcp_tool="$candidate_tool"; break; fi
        done
        if [ -n "$mcp_tool" ]; then
            "$VENV_DIR/bin/python" "$mcp_tool" "$home/config.yaml" "$hass_url" "$hass_token" "$VENV_DIR/bin" 2>/dev/null || true
            log "[run] [$name] Configured Home Assistant MCP server"
        fi
    fi

    if [ "$i" -gt 0 ] && [ -f "$home/config.yaml" ]; then
        local plat_tool=""
        for candidate_tool in "$(dirname "${BASH_SOURCE[0]}")/ha_platform_config.py" "/usr/local/bin/hermes-ha-platform-config"; do
            if [ -f "$candidate_tool" ]; then plat_tool="$candidate_tool"; break; fi
        done
        if [ -n "$plat_tool" ]; then
            "$VENV_DIR/bin/python" "$plat_tool" "$home/config.yaml" 2>/dev/null || true
            log "[run] [$name] Disabled shared external messaging channels for secondary profile"
        fi
    fi
}

for i in "${!PROFILE_DIRS[@]}"; do
    scaffold_profile_files "$i"
done

# tmux config (persistent, user-editable, single-instance)
if [ ! -f /config/.tmux.conf ]; then
    cat > /config/.tmux.conf << 'TMUX'
set -g default-terminal "tmux-256color"
set -g history-limit 100000
set -g mouse on
TMUX
    log "[run] Created default .tmux.conf"
fi

# ── Section 7: Environment variable passthrough ──────────────────────
# apply_env_vars_for_profile + upsert_env_var live in profile-init.sh
for i in "${!PROFILE_DIRS[@]}"; do
    apply_env_vars_for_profile "$i"
done

# HA integration: pass through if set (shared across profiles)
if [ -n "$HASS_TOKEN" ]; then
    export HASS_TOKEN
    log "[run] HASS_TOKEN injected"
fi
if [ -n "$GIT_TOKEN" ]; then
    export GITHUB_TOKEN="$GIT_TOKEN"
    log "[run] GITHUB_TOKEN injected"
fi
if [ -n "$HASS_URL" ]; then
    export HASS_URL
    log "[run] HASS_URL: $HASS_URL"
fi

# nginx htpasswd (shared)
if [ -n "$ACCESS_PASSWORD" ]; then
    echo "hermes:$(openssl passwd -apr1 "$ACCESS_PASSWORD")" > /etc/nginx/.htpasswd
    chmod 644 /etc/nginx/.htpasswd
    log "[run] Access password set (API key + nginx basic auth)"
else
    rm -f /etc/nginx/.htpasswd
fi

# ~/.hermes_profile: regenerated every start (shared, defaults to primary).
# HERMES_HOME is set only if unset, so ttyd subprocesses that pre-set it
# (per-profile sessions) keep their own profile.
cat > /config/.hermes_profile << ENVSH
: "\${HERMES_HOME:=$PRIMARY_HOME}"
export HERMES_HOME
export HERMES_GATEWAY_NO_SUPERVISE=1
export HERMES_VERSION="$HERMES_VERSION"
$([ -n "$GIT_TOKEN" ] && echo "export GITHUB_TOKEN=\"$GIT_TOKEN\"")
export GOBIN="$GO_DIR/bin"
export GOPATH="$GO_DIR"
$([ -n "$HASS_TOKEN" ] && echo "export HASS_TOKEN=\"$HASS_TOKEN\"")
$([ -n "$HASS_URL" ] && echo "export HASS_URL=\"$HASS_URL\"")
export HOMEBREW_CELLAR="$BREW_DIR/Cellar"
export HOMEBREW_PREFIX="$BREW_DIR"
export HOMEBREW_REPOSITORY="$BREW_DIR/Homebrew"
export NPM_CONFIG_PREFIX="$NODE_DIR"
export PATH="$VENV_DIR/bin:$BREW_DIR/sbin:$BREW_DIR/bin:$GO_DIR/bin:/usr/local/go/bin:$NODE_DIR/bin:\$PATH"
ENVSH
# Holds HASS_TOKEN / GITHUB_TOKEN in plain text.
chmod 600 /config/.hermes_profile
# ── Section 8: TLS certificates (shared) ─────────────────────────────
HA_SSL_DIR="/ssl"
TLS_CERTS_LIB=""
for _candidate in \
    "$(dirname "${BASH_SOURCE[0]}")/tls-certs.sh" \
    "/usr/local/lib/hermes-tls-certs.sh"; do
    if [ -f "$_candidate" ]; then
        TLS_CERTS_LIB="$_candidate"
        break
    fi
done
if [ -n "$TLS_CERTS_LIB" ]; then
    # shellcheck source=tls-certs.sh
    source "$TLS_CERTS_LIB"
    configure_tls_certs
fi

# ── Section 9: Render nginx config ───────────────────────────────────
DASHBOARD_AVAILABLE="false"
# Self-managed Hermes checkouts (install-stamp updateMechanism "self") select
# their dependency generation in hermes_bootstrap, which every Hermes entry
# point imports first. Without it a relaunched dashboard runs on the managed
# interpreter with no dependencies (ModuleNotFoundError: ruamel). Older
# revisions have no hermes_bootstrap module and keep the plain import.
DASHBOARD_BOOT='try:
    import hermes_bootstrap
except ModuleNotFoundError as exc:
    if exc.name != "hermes_bootstrap":
        raise
from hermes_cli.web_server import start_server'
if "$VENV_DIR/bin/python" -c "$DASHBOARD_BOOT" >/dev/null 2>&1; then
    DASHBOARD_AVAILABLE="true"
fi

if [ -n "$ACCESS_PASSWORD" ]; then
    AUTH_BASIC_ON='auth_basic "Hermes Agent"; auth_basic_user_file /etc/nginx/.htpasswd;'
    AUTH_BASIC_OFF='auth_basic off;'
else
    AUTH_BASIC_ON='# no authentication'
    AUTH_BASIC_OFF=''
fi

# Per-profile dashboard tokens — populated after dashboards start.
# Use a placeholder until then so generated nginx config is still valid.
# (Consumed by emit_token_maps / emit_profile_locations in nginx-render.sh.)
# shellcheck disable=SC2034
DASHBOARD_TOKENS=()
for i in "${!PROFILE_DIRS[@]}"; do
    DASHBOARD_TOKENS[$i]="PENDING_TOKEN_$i"
done

# Nginx rendering helpers (sourced from a separate library for testability)
# Resolve adjacent file when run.sh is executed locally; container puts the lib in /usr/local/lib.
NGINX_RENDER_LIB=""
for _candidate in \
    "$(dirname "${BASH_SOURCE[0]}")/nginx-render.sh" \
    "/usr/local/lib/hermes-nginx-render.sh"; do
    if [ -f "$_candidate" ]; then
        NGINX_RENDER_LIB="$_candidate"
        break
    fi
done
if [ -z "$NGINX_RENDER_LIB" ]; then
    log "[run] FATAL: nginx-render.sh not found"
    exit 1
fi
# shellcheck source=nginx-render.sh
source "$NGINX_RENDER_LIB"

render_nginx_config() {
    # Render ports config if any direct-port service enabled
    local include_ports
    if [ "$ENABLE_DASHBOARD" = "true" ] || [ "$ENABLE_TERMINAL" = "true" ] || [ "$ENABLE_API" = "true" ]; then
        cp /etc/nginx/nginx-ports.conf.tpl /etc/nginx/ports.conf
        emit_token_maps | substitute_marker /etc/nginx/ports.conf '%%DASHBOARD_TOKEN_MAPS%%'
        emit_profile_locations http | substitute_marker /etc/nginx/ports.conf '%%HTTP_PROFILE_LOCATIONS%%'
        emit_profile_locations https | substitute_marker /etc/nginx/ports.conf '%%HTTPS_PROFILE_LOCATIONS%%'
        sed -i \
            -e "s|%%HTTP_PORT%%|${HTTP_PORT}|g" \
            -e "s|%%HTTPS_PORT%%|${HTTPS_PORT}|g" \
            -e "s|%%CERTS_DIR%%|${CERTS_DIR}|g" \
            -e "s|%%AUTH_BASIC_ON%%|${AUTH_BASIC_ON}|g" \
            -e "s|%%AUTH_BASIC_OFF%%|${AUTH_BASIC_OFF}|g" \
            /etc/nginx/ports.conf
        include_ports="include /etc/nginx/ports.conf;"
        log "[run] Direct ports: enabled (HTTP: $HTTP_PORT, HTTPS: $HTTPS_PORT)"
    else
        include_ports="# direct ports disabled"
        log "[run] Direct ports: disabled (Ingress only)"
    fi

    cp /etc/nginx/nginx.conf.tpl /etc/nginx/nginx.conf
    emit_upstreams | substitute_marker /etc/nginx/nginx.conf '%%UPSTREAMS%%'
    emit_dashboard_maps | substitute_marker /etc/nginx/nginx.conf '%%DASHBOARD_MAPS%%'
    emit_profile_locations ingress | substitute_marker /etc/nginx/nginx.conf '%%INGRESS_PROFILE_LOCATIONS%%'
    
    local access_log_setting="access_log /dev/stdout minimal;"
    if [ "${CURRENT_LOG_LEVEL_NUM:-2}" -ge 4 ]; then
        access_log_setting="access_log off;"
    fi

    sed -i \
        -e "s|%%INGRESS_PORT%%|${INGRESS_PORT}|g" \
        -e "s|%%CERTS_DIR%%|${CERTS_DIR}|g" \
        -e "s|%%HERMES_VERSION%%|${HERMES_VERSION}|g" \
        -e "s|%%INCLUDE_PORTS%%|${include_ports}|g" \
        -e "s|%%ACCESS_LOG%%|${access_log_setting}|g" \
        /etc/nginx/nginx.conf
}

render_nginx_config

# Render landing page
ADDON_SLUG=$(hostname | tr '-' '_')
SHOW_TERMINAL="false"
[ "$ENABLE_TERMINAL" = "true" ] && SHOW_TERMINAL="true"
SHOW_DASHBOARD="$DASHBOARD_AVAILABLE"
SHOW_DASHBOARD_PORTS="false"
if [ "$ENABLE_DASHBOARD" = "true" ] && [ "$DASHBOARD_AVAILABLE" = "true" ]; then
    SHOW_DASHBOARD_PORTS="true"
fi
SHOW_API="false"
[ "$ENABLE_API" = "true" ] && SHOW_API="true"

# Build profiles JSON for the landing page renderer
build_profiles_json() {
    local i pref
    printf '['
    for i in "${!PROFILE_DIRS[@]}"; do
        [ "$i" -gt 0 ] && printf ','
        pref="${PROFILE_PATH_PREFIX[$i]}"
        printf '{"name":%s,"prefix":%s,"primary":%s}' \
            "$(jq -Rn --arg n "${PROFILE_NAMES[$i]}" '$n')" \
            "$(jq -Rn --arg p "$pref" '$p')" \
            "$([ "$i" -eq 0 ] && echo true || echo false)"
    done
    printf ']'
}
PROFILES_JSON="$(build_profiles_json)"

cp /var/www/landing.html.tpl /var/www/landing.html
# PROFILES_JSON contains JSON; use a different delimiter so braces don't collide
PROFILES_JSON_ESC=$(printf '%s' "$PROFILES_JSON" | sed 's|[\\/&]|\\&|g')
sed -i \
    -e "s|%%HERMES_VERSION%%|${HERMES_VERSION}|g" \
    -e "s|%%ADDON_SLUG%%|${ADDON_SLUG}|g" \
    -e "s|%%SHOW_TERMINAL%%|${SHOW_TERMINAL}|g" \
    -e "s|%%SHOW_DASHBOARD%%|${SHOW_DASHBOARD}|g" \
    -e "s|%%SHOW_DASHBOARD_PORTS%%|${SHOW_DASHBOARD_PORTS}|g" \
    -e "s|%%SHOW_API%%|${SHOW_API}|g" \
    -e "s|%%PROFILES_JSON%%|${PROFILES_JSON_ESC}|g" \
    /var/www/landing.html

log "[run] Nginx configured (ingress: $INGRESS_PORT, HTTP: $HTTP_PORT, HTTPS: $HTTPS_PORT)"

# ── Section 10: Start services (per profile) ─────────────────────────
RUN_SH_PID=$$
GATEWAY_PIDS=()
GATEWAY_LOGGER_PIDS=()
GATEWAY_LOG_PIPES=()
GATEWAY_READY_FILES=()
DASHBOARD_RESTART_AFTER=()
TTYD_HERMES_PIDS=()
TTYD_TERMINAL_PIDS=()
DASHBOARD_PIDS=()
DESKTOP_BACKEND_PID=""

kill_port() {
    local port="$1"
    local pids
    pids=$(ss -tulpn 2>/dev/null | grep -E ":${port}[[:space:]]" | grep -oP 'pid=\K[0-9]+' | sort -u || true)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                log "[run] Freeing port $port: terminating stale process $pid"
                kill -9 "$pid" 2>/dev/null || true
            fi
        done
        sleep 0.5
    fi
}

start_gateway_for_profile() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local name="${PROFILE_NAMES[$i]}"
    local port="${API_PORTS[$i]}"

    log "[run] [$name] Starting gateway (API port: $port)..."
    kill_port "$port"
    if [ "$i" -eq 0 ]; then
        kill_port 3000
        pkill -9 -f "whatsapp-bridge/bridge.js" 2>/dev/null || true
    fi
    mkdir -p "$home/logs"
    local log_pipe="/run/hermes-gateway-${i}.fifo"
    local ready_file="/run/hermes-gateway-${i}.ready"
    rm -f -- "$log_pipe" "$ready_file"
    mkfifo -m 600 "$log_pipe"
    /usr/bin/env -i PATH="/usr/bin:/bin" \
        "$VENV_DIR/bin/python" "$GATEWAY_LOGGER" \
        "$home/logs/gateway.log" "$log_pipe" "$RUN_SH_PID" &
    local logger_pid=$!
    GATEWAY_LOGGER_PIDS[$i]="$logger_pid"
    GATEWAY_LOG_PIPES[$i]="$log_pipe"
    GATEWAY_READY_FILES[$i]="$ready_file"
    (
        cd "$home"
        export HERMES_HOME="$home"
        export PATH="$VENV_DIR/bin:$BASE_PATH"
        export HERMES_S6_SUPERVISED_CHILD="1"
        export HERMES_ADDON_PROFILE_HOME="$home"
        export HERMES_ADDON_MULTIPLEX_PROFILES="false"
        export HERMES_ADDON_GATEWAY_NO_SUPERVISE="1"
        export HERMES_ADDON_SUPERVISED_CHILD="1"
        export HERMES_ADDON_API_HOST="127.0.0.1"
        export HERMES_ADDON_API_PORT="$port"
        export HERMES_ADDON_API_ENABLED="$ENABLE_API"
        if [ "$ENABLE_API" = "true" ]; then
            export HERMES_ADDON_API_KEY="$ACCESS_PASSWORD"
        else
            export HERMES_ADDON_API_KEY=""
        fi
        exec "$GATEWAY_CHILD" \
            "$VENV_DIR/bin/python" \
            "$GATEWAY_SUPERVISOR" \
            "$GATEWAY_LAUNCHER" \
            "$ready_file" \
            "$RUN_SH_PID" \
            > "$log_pipe" 2>&1
    ) &
    local pid=$!
    GATEWAY_PIDS[$i]="$pid"
    local ready_pid=""
    local ready=false
    for _ in $(seq 1 100); do
        if [ -f "$ready_file" ]; then
            IFS= read -r ready_pid < "$ready_file" || true
            if [ "$ready_pid" = "$pid" ]; then
                ready=true
                break
            fi
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 0.05
    done
    if [ "$ready" != "true" ]; then
        local startup_status
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
            local waited=0
            while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 20 ]; do
                sleep 0.1
                waited=$((waited + 1))
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
        set +e
        wait "$pid" 2>/dev/null
        startup_status=$?
        set -e
        cleanup_gateway_logger "$i"
        log "[run] [$name] FATAL: gateway supervisor failed before readiness (code: $startup_status)" >&2
        return 70
    fi
    log "[run] [$name] Gateway PID: $pid (logger PID: $logger_pid)"
}

# Install the dedicated hermes startup wrapper (shared, sources .bashrc).
# Each ttyd subprocess sets HERMES_HOME via env before exec, so .bashrc sources
# the right .env for that session.
install_start_hermes_wrapper() {
    cat > /usr/local/bin/start-hermes << 'WRAPPER'
#!/bin/bash
source ~/.bashrc
hermes
ret=$?
if [ $ret -eq 0 ]; then exit 0; fi
echo ""
echo "Hermes exited with code $ret. Shell is available for debugging."
echo "Run 'hermes' to restart, or 'exit' to close."
exec bash
WRAPPER
    chmod +x /usr/local/bin/start-hermes
}

start_ttyd_for_profile() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local name="${PROFILE_NAMES[$i]}"
    local prefix="${PROFILE_PATH_PREFIX[$i]}"
    local hermes_port="${TTYD_HERMES_PORTS[$i]}"
    local term_port="${TTYD_TERMINAL_PORTS[$i]}"

    log "[run] [$name] Starting ttyd (hermes: $hermes_port, terminal: $term_port)..."
    # tmux server env is captured on first new-session and shared by every
    # later session on the same socket. With one shared socket, profile-N's
    # session would inherit profile-0's HERMES_HOME. Use a per-profile
    # socket (`-L`) so each tmux server inherits the right env.
    env HERMES_HOME="$home" \
        ttyd \
            --port "$hermes_port" \
            --interface 127.0.0.1 \
            --base-path "${prefix}/hermes/" \
            --writable -d 3 \
            tmux -L "hermes-${name}" -u new -A -s "hermes-${name}" /usr/local/bin/start-hermes &
    TTYD_HERMES_PIDS[$i]=$!

    env HERMES_HOME="$home" \
        ttyd \
            --port "$term_port" \
            --interface 127.0.0.1 \
            --base-path "${prefix}/terminal/" \
            --writable -d 3 \
            tmux -L "terminal-${name}" -u new -A -s "terminal-${name}" /usr/bin/bash &
    TTYD_TERMINAL_PIDS[$i]=$!
    log "[run] [$name] ttyd PIDs: hermes=${TTYD_HERMES_PIDS[$i]} terminal=${TTYD_TERMINAL_PIDS[$i]}"
}

start_dashboard_for_profile() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local name="${PROFILE_NAMES[$i]}"
    local port="${DASHBOARD_PORTS[$i]}"

    if [ "$DASHBOARD_AVAILABLE" != "true" ]; then
        log "[run] [$name] Dashboard: not available (web_server module not found)"
        return
    fi
    log "[run] [$name] Starting dashboard (port: $port)..."
    kill_port "$port"
    (
        cd "$home"
        export HERMES_HOME="$home"
        exec "$VENV_DIR/bin/python" -c "${DASHBOARD_BOOT}
start_server(host='127.0.0.1', port=${port}, open_browser=False)"
    ) &
    DASHBOARD_PIDS[$i]=$!
    log "[run] [$name] Dashboard PID: ${DASHBOARD_PIDS[$i]}"
}

# Read the dashboard's ephemeral session token from a running dashboard.
# The dashboard generates a random token on each start and embeds it in index.html.
inject_dashboard_token_for_profile() {
    local i="$1"
    local name="${PROFILE_NAMES[$i]}"
    local port="${DASHBOARD_PORTS[$i]}"

    if [ "$DASHBOARD_AVAILABLE" != "true" ]; then
        return
    fi
    log "[run] [$name] Waiting for dashboard token..."
    local token=""
    for _ in $(seq 1 15); do
        token=$(curl -s "http://127.0.0.1:${port}/" 2>/dev/null \
            | grep -oP '__HERMES_SESSION_TOKEN__="\K[^"]+' || true)
        if [ -n "$token" ]; then
            break
        fi
        sleep 2
    done
    if [ -z "$token" ]; then
        log "[run] [$name] Warning: could not read dashboard token (dashboard API auth may not work)"
        token="UNAVAILABLE"
    fi
    # shellcheck disable=SC2034  # consumed by emit_token_maps / emit_profile_locations in nginx-render.sh
    DASHBOARD_TOKENS[$i]="$token"
    log "[run] [$name] Dashboard token obtained (${#token} chars)"
}

reload_nginx() {
    log "[run] Reloading nginx with full config..."
    nginx -s reload
    log "[run] nginx reloaded"
}

# ── Section 11: Signal handling ──────────────────────────────────────
# Gateway slot lifecycle (upstream v1.3.1/v1.3.2): each slot supervisor is a
# process-group leader that must prove its whole descendant tree is gone
# before a replacement starts, so no gateway is ever duplicated.
gateway_group_alive() {
    local pid="$1"
    kill -0 -- "-$pid" 2>/dev/null
}

signal_gateway_tree() {
    local pid="$1"
    local signal="$2"
    if gateway_group_alive "$pid"; then
        kill -s "$signal" -- "-$pid" 2>/dev/null || true
    elif kill -0 "$pid" 2>/dev/null; then
        kill -s "$signal" "$pid" 2>/dev/null || true
    fi
}

stop_gateway_tree() {
    local pid="$1"
    local name="$2"
    signal_gateway_tree "$pid" TERM
    local waited=0
    while { kill -0 "$pid" 2>/dev/null || gateway_group_alive "$pid"; } \
        && [ "$waited" -lt 100 ]; do
        sleep 0.1
        waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null || gateway_group_alive "$pid"; then
        log "[run] [$name] FATAL: gateway slot supervisor did not prove containment before timeout" >&2
        return 70
    fi
    local supervisor_status
    set +e
    wait "$pid" 2>/dev/null
    supervisor_status=$?
    set -e
    if [ "$supervisor_status" -ne 0 ]; then
        log "[run] [$name] FATAL: unsafe gateway supervisor exit: $supervisor_status" >&2
        return "$supervisor_status"
    fi
    return 0
}

cleanup_gateway_logger() {
    local i="$1"
    local logger_pid="${GATEWAY_LOGGER_PIDS[$i]:-}"
    local log_pipe="${GATEWAY_LOG_PIPES[$i]:-}"
    local ready_file="${GATEWAY_READY_FILES[$i]:-}"
    if [ -n "$logger_pid" ]; then
        local waited=0
        while kill -0 "$logger_pid" 2>/dev/null && [ "$waited" -lt 20 ]; do
            sleep 0.1
            waited=$((waited + 1))
        done
        if kill -0 "$logger_pid" 2>/dev/null; then
            kill -TERM "$logger_pid" 2>/dev/null || true
            waited=0
            while kill -0 "$logger_pid" 2>/dev/null && [ "$waited" -lt 10 ]; do
                sleep 0.1
                waited=$((waited + 1))
            done
        fi
        if kill -0 "$logger_pid" 2>/dev/null; then
            kill -KILL "$logger_pid" 2>/dev/null || true
        fi
        wait "$logger_pid" 2>/dev/null || true
    fi
    if [ -n "$log_pipe" ]; then
        rm -f -- "$log_pipe"
    fi
    if [ -n "$ready_file" ]; then
        rm -f -- "$ready_file"
    fi
    unset 'GATEWAY_LOGGER_PIDS[i]' 'GATEWAY_LOG_PIPES[i]' 'GATEWAY_READY_FILES[i]'
}

SHUTDOWN_PENDING=false

request_shutdown() {
    SHUTDOWN_PENDING=true
}

start_gateway_signal_safe() {
    # Bash runs traps between commands. Defer termination until the supervisor
    # has published its post-reexec ready state or startup cleanup has finished.
    local start_status
    trap request_shutdown SIGTERM SIGINT
    set +e
    start_gateway_for_profile "$@"
    start_status=$?
    set -e
    trap shutdown SIGTERM SIGINT
    if [ "$SHUTDOWN_PENDING" = "true" ]; then
        shutdown
    fi
    return "$start_status"
}

supervise_gateway_profile() {
    local i="$1"
    local pid="${GATEWAY_PIDS[$i]:-}"
    local logger_pid="${GATEWAY_LOGGER_PIDS[$i]:-}"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        if [ -n "$pid" ]; then
            set +e; wait "$pid" 2>/dev/null; EXIT_CODE=$?; set -e
        else
            EXIT_CODE=127
        fi
        cleanup_gateway_logger "$i"
        if [ "$EXIT_CODE" -ne 0 ]; then
            log "[run] [${PROFILE_NAMES[$i]}] FATAL: unsafe gateway supervisor exit: $EXIT_CODE" >&2
            return "$EXIT_CODE"
        fi
        log "[run] [${PROFILE_NAMES[$i]}] Gateway slot exited with containment proven; restarting in 3s..."
        log "[run] (Use the shutdown handler to stop the container.)"
        sleep 3
        start_gateway_signal_safe "$i"
    elif [ -z "$logger_pid" ] || ! kill -0 "$logger_pid" 2>/dev/null; then
        if [ -n "$logger_pid" ]; then
            set +e; wait "$logger_pid" 2>/dev/null; LOGGER_EXIT_CODE=$?; set -e
        else
            LOGGER_EXIT_CODE=127
        fi
        log "[run] [${PROFILE_NAMES[$i]}] Gateway logger exited (code: $LOGGER_EXIT_CODE); restarting gateway tree in 3s..."
        stop_gateway_tree "$pid" "${PROFILE_NAMES[$i]}"
        cleanup_gateway_logger "$i"
        sleep 3
        start_gateway_signal_safe "$i"
    fi
}

# Dashboards were previously started once and never restarted. Each restart
# mints a new session token, so nginx must be re-rendered and reloaded too.
# A crash-looping dashboard is retried at most once every 5 minutes.
supervise_dashboards() {
    [ "$DASHBOARD_AVAILABLE" = "true" ] || return 0
    local i pid now restarted=false
    now=$(date +%s)
    for i in "${!PROFILE_DIRS[@]}"; do
        pid="${DASHBOARD_PIDS[$i]:-}"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            continue
        fi
        if [ "$now" -lt "${DASHBOARD_RESTART_AFTER[$i]:-0}" ]; then
            continue
        fi
        [ -n "$pid" ] && { wait "$pid" 2>/dev/null || true; }
        DASHBOARD_RESTART_AFTER[$i]=$((now + 300))
        log "warning" "[run] [${PROFILE_NAMES[$i]}] Dashboard not running; restarting..."
        start_dashboard_for_profile "$i"
        inject_dashboard_token_for_profile "$i"
        restarted=true
    done
    if [ "$restarted" = "true" ]; then
        render_nginx_config
        reload_nginx
    fi
}

shutdown() {
    echo ""
    log "[run] Shutting down..."
    local shutdown_status=0
    nginx -s quit 2>/dev/null || true
    log "[run] nginx stopped"
    desktop_backend_stop
    for i in "${!PROFILE_DIRS[@]}"; do
        for pid in "${TTYD_TERMINAL_PIDS[$i]:-}" "${TTYD_HERMES_PIDS[$i]:-}" "${DASHBOARD_PIDS[$i]:-}"; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
            fi
        done
    done
    log "[run] ttyd + dashboards stopped"
    if [ -n "${REPORTER_PID:-}" ] && kill -0 "$REPORTER_PID" 2>/dev/null; then
        kill "$REPORTER_PID" 2>/dev/null || true
    fi
    if declare -f stop_periodic_backup_daemon >/dev/null; then
        stop_periodic_backup_daemon
    fi
    for i in "${!PROFILE_DIRS[@]}"; do
        local pid="${GATEWAY_PIDS[$i]:-}"
        if [ -n "$pid" ]; then
            local gateway_status
            set +e
            stop_gateway_tree "$pid" "${PROFILE_NAMES[$i]}"
            gateway_status=$?
            set -e
            if [ "$gateway_status" -ne 0 ]; then
                shutdown_status="$gateway_status"
            fi
        fi
        cleanup_gateway_logger "$i"
        if [ -n "$pid" ]; then
            log "[run] [${PROFILE_NAMES[$i]}] Gateway stopped"
        fi
    done
    # Safety net: the bridge is a gateway descendant and normally already gone.
    pkill -9 -f "whatsapp-bridge/bridge.js" 2>/dev/null || true
    kill_port 3000
    log "[run] Shutdown complete"
    exit "$shutdown_status"
}

# Register signal handler BEFORE starting services
trap shutdown SIGTERM SIGINT

# Supporting Hermes revisions require named profiles to opt into the add-on's
# existing one-gateway-per-profile topology. Complete every config write before
# the first gateway so a partial topology can never be launched.
configure_profile_topology "$VENV_DIR/bin/python" "$VENV_DIR/bin/hermes"

install_start_hermes_wrapper

for i in "${!PROFILE_DIRS[@]}"; do
    start_gateway_signal_safe "$i"
    start_ttyd_for_profile "$i"
    start_dashboard_for_profile "$i"
done

desktop_backend_start

for i in "${!PROFILE_DIRS[@]}"; do
    inject_dashboard_token_for_profile "$i"
done

# Re-render nginx now that we have real dashboard tokens
render_nginx_config

reload_nginx

REPORTER_PID=""
if [ -n "${HASS_TOKEN:-${SUPERVISOR_TOKEN:-}}" ]; then
    reporter_tool=""
    for candidate_tool in "$(dirname "${BASH_SOURCE[0]}")/ha_sensor_reporter.py" "/usr/local/bin/hermes-ha-sensor-reporter"; do
        if [ -f "$candidate_tool" ]; then reporter_tool="$candidate_tool"; break; fi
    done
    if [ -n "$reporter_tool" ]; then
        names_csv="$(IFS=,; echo "${PROFILE_NAMES[*]}")"
        ports_csv="$(IFS=,; echo "${API_PORTS[*]}")"
        "$VENV_DIR/bin/python" "$reporter_tool" "$OPTIONS_FILE" "$ADDON_VERSION" "$names_csv" "$ports_csv" &
        REPORTER_PID=$!
        log "[run] Home Assistant status sensor reporter started (PID: $REPORTER_PID)"
    fi
fi

if declare -f start_periodic_backup_daemon >/dev/null; then
    start_periodic_backup_daemon
fi

log "[run] All services started"
BASE_URL="${HASS_URL:-http://localhost}"
BASE_SCHEME="${BASE_URL%%://*}"
BASE_HOST="${BASE_URL#*://}"
BASE_HOST="${BASE_HOST%%:*}"
BASE_HOST="${BASE_HOST%%/*}"

external_https_port=""
external_http_port=""
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    for _ in 1 2 3; do
        net_json=$(curl -s -m 3 -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" http://supervisor/addons/self/info 2>/dev/null || true)
        if [ -n "$net_json" ] && echo "$net_json" | jq -e '.data.network != null' >/dev/null 2>&1; then
            external_https_port=$(echo "$net_json" | jq -r '.data.network["8443/tcp"] // empty' 2>/dev/null || true)
            external_http_port=$(echo "$net_json" | jq -r '.data.network["8080/tcp"] // empty' 2>/dev/null || true)
            break
        fi
        sleep 1
    done
fi
display_https_port="${external_https_port:-$HTTPS_PORT}"
display_http_port="${external_http_port:-$HTTP_PORT}"

if [ "$BASE_SCHEME" = "https" ]; then
    BASE_URL="${BASE_SCHEME}://${BASE_HOST}:${display_https_port}"
else
    BASE_URL="${BASE_SCHEME}://${BASE_HOST}:${display_http_port}"
fi
echo "─────────────────────────────────────────────"
echo " ${HERMES_VERSION}"
for i in "${!PROFILE_DIRS[@]}"; do
    prefix="${PROFILE_PATH_PREFIX[$i]}"
    label="${PROFILE_NAMES[$i]}"
    echo " Profile ${label} (PID ${GATEWAY_PIDS[$i]}):"
    echo "   Hermes:    ${BASE_URL}${prefix}/hermes/"
    [ "$DASHBOARD_AVAILABLE" = "true" ] && echo "   Dashboard: ${BASE_URL}${prefix}/dashboard/"
    echo "   Terminal:  ${BASE_URL}${prefix}/terminal/"
    echo "   API:       ${BASE_URL}${prefix}/v1/"
done
[ "$ENABLE_DESKTOP_BACKEND" = "true" ] && echo " Desktop:    container port ${DESKTOP_BACKEND_PORT} (use the Home Assistant Network host port)"
echo "─────────────────────────────────────────────"

# ── Section 12: Supervisor loop ──────────────────────────────────────
while true; do
    desktop_backend_supervise
    for i in "${!PROFILE_DIRS[@]}"; do
        supervise_gateway_profile "$i"
    done
    supervise_dashboards
    sleep 5
done

shutdown

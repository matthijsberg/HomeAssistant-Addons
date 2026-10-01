#!/bin/bash
# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────
# Home Assistant user discovery and profile auto-sync library.
# Sourced by profile-init.sh / run.sh.
# ─────────────────────────────────────────────────────────────────────

if ! declare -f log >/dev/null 2>&1; then
  ADDON_VERSION="${ADDON_VERSION:-2.3.2}"
  log() {
    local now
    now="$(date +'%Y-%m-%d %H:%M:%S')"
    echo "[$now] [v${ADDON_VERSION}] $*"
  }
fi

sync_ha_users() {
  local options_file="${OPTIONS_FILE:-/data/options.json}"
  local auto_sync
  auto_sync="$(jq -r 'if has("auto_sync_ha_users") then .auto_sync_ha_users else true end' "$options_file" 2>/dev/null || echo "true")"

  if [ "$auto_sync" != "true" ]; then
    return 0
  fi

  local hass_url="${HASS_URL:-$(jq -r '.hass_url // "http://supervisor/core"' "$options_file" 2>/dev/null)}"
  hass_url="${hass_url%/}"
  local token="${HASS_TOKEN:-$(jq -r '.homeassistant_token // empty' "$options_file" 2>/dev/null)}"
  if [ -z "$token" ]; then
    token="${SUPERVISOR_TOKEN:-}"
  fi

  if [ -z "$token" ]; then
    return 0
  fi

  log "[ha-user-sync] Home Assistant user auto-sync enabled"

  local states_json=""
  if [ -n "$token" ]; then
    # Query HA Core REST API /api/states
    states_json="$(curl -s -k -f -m 5 \
      -H "Authorization: Bearer ${token}" \
      -H "Content-Type: application/json" \
      "${hass_url}/api/states" 2>/dev/null || true)"

    # Fallback to Supervisor internal proxy if initial call produced nothing
    if [ -z "$states_json" ] && [ "$hass_url" != "http://supervisor/core" ]; then
      states_json="$(curl -s -k -f -m 5 \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        "http://supervisor/core/api/states" 2>/dev/null || true)"
    fi
  fi

  if [ -z "$states_json" ] || ! echo "$states_json" | jq -e 'if type == "array" then true else false end' >/dev/null 2>&1; then
    log "[ha-user-sync] Warning: Could not fetch state list from Home Assistant API" >&2
    return 0
  fi

  local base="${PROFILES_BASE-.hermes/profiles}"
  local user_lines
  # Extract human occupants (person.* entities linked to real HA user accounts)
  user_lines="$(echo "$states_json" | jq -r '.[]? | select((.entity_id | startswith("person.")) and .attributes.user_id != null) | "\(.entity_id[7:])|\(.attributes.friendly_name // .entity_id[7:])"' 2>/dev/null || true)"

  if [ -z "$user_lines" ]; then
    log "[ha-user-sync] No human person entities found in Home Assistant"
    return 0
  fi

  local line username display_name name effective_dir profile_home
  local first_user=true
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    username="${line%%|*}"
    display_name="${line#*|}"

    name="$(sanitize_profile_name "$username")"
    [ -n "$name" ] || continue

    effective_dir="$(_resolve_profile_dir "$name" "$base")"
    profile_home="$HOME/$effective_dir"

    mkdir -p "$profile_home"

    # Migrate primary .hermes memories/sessions to first user profile if missing
    if [ "$first_user" = "true" ] && [ -d "$HOME/.hermes" ]; then
      if [ -d "$HOME/.hermes/memories" ] && [ ! -d "$profile_home/memories" ]; then
        cp -r "$HOME/.hermes/memories" "$profile_home/" 2>/dev/null || true
        log "[ha-user-sync] Migrated memories from .hermes to '$name'"
      fi
      if [ -d "$HOME/.hermes/sessions" ] && [ ! -d "$profile_home/sessions" ]; then
        cp -r "$HOME/.hermes/sessions" "$profile_home/" 2>/dev/null || true
        log "[ha-user-sync] Migrated sessions from .hermes to '$name'"
      fi
      if [ -f "$HOME/.hermes/state.db" ] && [ ! -f "$profile_home/state.db" ]; then
        cp "$HOME/.hermes/state.db" "$profile_home/" 2>/dev/null || true
      fi
      if [ -f "$HOME/.hermes/.env" ] && [ ! -f "$profile_home/.env" ]; then
        cp -p "$HOME/.hermes/.env" "$profile_home/.env" 2>/dev/null || true
        chmod 600 "$profile_home/.env" 2>/dev/null || true
        log "[ha-user-sync] Migrated .env credentials from .hermes to '$name'"
      fi
    fi

    # Provision USER.md if missing
    if [ ! -f "$profile_home/USER.md" ]; then
      cat > "$profile_home/USER.md" << USER_EOF
# User Profile for ${display_name}

- **Name:** ${display_name}
- **Home Assistant Username:** ${username}
USER_EOF
      log "[ha-user-sync] Created USER.md for HA user '$username' ($name)"
    fi

    # Provision SOUL.md if missing
    if [ ! -f "$profile_home/SOUL.md" ]; then
      cat > "$profile_home/SOUL.md" << SOUL_EOF
# Hermes Agent Persona for ${display_name}

You are the personal AI assistant for ${display_name} in Home Assistant.
Be helpful, attentive, and tailor your responses and suggestions to ${display_name}'s preferences.
SOUL_EOF
      log "[ha-user-sync] Created SOUL.md for HA user '$username' ($name)"
    fi

    # If profiles list was empty (defaulting to .hermes), replace .hermes with the first auto-discovered user
    if [ "$profiles_from_list" = "false" ] && [ "${PROFILE_DIRS[0]:-}" = ".hermes" ]; then
      PROFILE_DIRS[0]="$effective_dir"
      log "[ha-user-sync] Promoted HA primary user '$username' ($name) to Primary Profile"
    else
      # Check if this profile is already present in PROFILE_DIRS array
      local existing=false
      local existing_dir
      for existing_dir in "${PROFILE_DIRS[@]}"; do
        if [ "$(sanitize_profile_name "$existing_dir")" = "$name" ]; then
          existing=true
          break
        fi
      done

      if [ "$existing" = "false" ]; then
        PROFILE_DIRS+=("$effective_dir")
        log "[ha-user-sync] Auto-added profile '$name' for HA user '$username'"
      fi
    fi

    first_user=false
  done <<< "$user_lines"
}

#!/bin/bash
# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────
# Backup storage setup (sourced by run.sh and by tests).
#
# Consumes from the caller's environment:
#   BACKUP_ROOT      HA's shared backup mount (/backup in prod)
#   PROFILE_HOMES[]  from profile-init.sh's resolve_profiles
#   PROFILE_NAMES[]  from profile-init.sh's resolve_profiles
#
# For each profile, makes "$PROFILE_HOMES[i]/backups" a symlink into
# "$BACKUP_ROOT/$PROFILE_NAMES[i]" so hermes_cli's existing pre-update
# backups (which already write to "$HERMES_HOME/backups/") land on HA's
# shared backup storage for free, with no changes to hermes_cli itself.
# Any files already in a real (non-symlink) backups/ dir are preserved by
# moving them into the new target before the symlink is created.
# ─────────────────────────────────────────────────────────────────────

if ! declare -f log >/dev/null 2>&1; then
  ADDON_VERSION="${ADDON_VERSION:-2.3.2}"
  log() {
    local now
    now="$(date +'%Y-%m-%d %H:%M:%S')"
    echo "[$now] [v${ADDON_VERSION}] $*"
  }
fi

configure_backup_storage() {
    mkdir -p "$BACKUP_ROOT"
    local i home target
    for i in "${!PROFILE_HOMES[@]}"; do
        home="${PROFILE_HOMES[$i]}"
        target="$BACKUP_ROOT/${PROFILE_NAMES[$i]}"
        mkdir -p "$home" "$target"
        if [ -L "$home/backups" ]; then
            continue
        fi
        if [ -d "$home/backups" ]; then
            find "$home/backups" -mindepth 1 -maxdepth 1 -exec mv {} "$target/" \;
            rmdir "$home/backups" 2>/dev/null || true
        fi
        rm -f "$home/backups"
        ln -s "$target" "$home/backups"
        log "[run] Backups for profile '${PROFILE_NAMES[$i]}' → $target"
    done
}

prune_profile_backups() {
    local name="$1"
    local keep="${PERIODIC_BACKUP_KEEP_COUNT:-7}"
    local target="$BACKUP_ROOT/$name"
    [ -d "$target" ] || return 0

    if ! [[ "$keep" =~ ^[0-9]+$ ]] || [ "$keep" -lt 1 ]; then
        keep=7
    fi

    local files=()
    while IFS= read -r f; do
        [ -n "$f" ] && files+=("$f")
    done < <(find "$target" -maxdepth 1 -name "hermes-backup-${name}-*.tar.gz" -type f 2>/dev/null | sort)

    local total="${#files[@]}"
    if [ "$total" -gt "$keep" ]; then
        local to_remove=$((total - keep))
        for ((idx=0; idx<to_remove; idx++)); do
            log "notice" "[backup] Pruning old backup: ${files[$idx]}"
            rm -f "${files[$idx]}"
        done
    fi
}

create_profile_backup() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]:-}"
    local name="${PROFILE_NAMES[$i]:-}"
    [ -n "$home" ] && [ -d "$home" ] || return 0
    [ -n "$name" ] || return 0

    local target="$BACKUP_ROOT/$name"
    mkdir -p "$target"

    local stamp
    stamp="$(date +'%Y-%m-%d_%H%M%S')"
    local archive_name="hermes-backup-${name}-${stamp}.tar.gz"
    local archive_tmp="${target}/.${archive_name}.tmp"
    local archive_dest="${target}/${archive_name}"

    log "[backup] Creating backup for profile '$name'..."
    local tar_rc=0
    tar -czf "$archive_tmp" \
        --exclude="./backups" \
        --exclude="./logs" \
        --exclude="./venv" \
        --exclude="./node_modules" \
        --exclude="./.cache" \
        --exclude="./lsp" \
        --exclude="*.sock" \
        --exclude="*.fifo" \
        -C "$home" . 2>/dev/null || tar_rc=$?

    if { [ "$tar_rc" -eq 0 ] || [ "$tar_rc" -eq 1 ]; } && [ -s "$archive_tmp" ]; then
        mv "$archive_tmp" "$archive_dest"
        log "notice" "[backup] Backup created: $archive_dest"
        prune_profile_backups "$name"
        return 0
    else
        rm -f "$archive_tmp"
        log "warning" "[backup] Warning: backup creation failed for '$name' (tar exit code: $tar_rc)"
        return 1
    fi
}

run_periodic_backups() {
    local i
    for i in "${!PROFILE_HOMES[@]}"; do
        create_profile_backup "$i" || true
    done
}

start_periodic_backup_daemon() {
    if [ "${ENABLE_PERIODIC_BACKUPS:-true}" != "true" ]; then
        log "[backup] Periodic backups disabled"
        return 0
    fi

    local interval_hours="${PERIODIC_BACKUP_INTERVAL_HOURS:-24}"
    if ! [[ "$interval_hours" =~ ^[0-9]+$ ]] || [ "$interval_hours" -lt 1 ]; then
        interval_hours=24
    fi
    local interval_seconds=$((interval_hours * 3600))
    log "notice" "[backup] Starting periodic backup daemon (interval: ${interval_hours}h, retention: ${PERIODIC_BACKUP_KEEP_COUNT:-7})"

    (
        sleep 10
        while true; do
            run_periodic_backups
            sleep "$interval_seconds"
        done
    ) &
    PERIODIC_BACKUP_PID=$!
}

stop_periodic_backup_daemon() {
    if [ -n "${PERIODIC_BACKUP_PID:-}" ] && kill -0 "$PERIODIC_BACKUP_PID" 2>/dev/null; then
        log "[backup] Stopping periodic backup daemon (PID: $PERIODIC_BACKUP_PID)"
        kill -TERM "$PERIODIC_BACKUP_PID" 2>/dev/null || true
        wait "$PERIODIC_BACKUP_PID" 2>/dev/null || true
        unset PERIODIC_BACKUP_PID
    fi
}

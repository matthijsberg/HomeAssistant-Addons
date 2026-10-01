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
  ADDON_VERSION="${ADDON_VERSION:-unknown}"
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

# Copy one SQLite database with the online backup API, which yields a
# consistent snapshot even while Hermes writes to it in WAL mode.
snapshot_sqlite_db() {
    local src="$1" dst="$2" py_bin="python3"
    [ -x /usr/bin/python3 ] && py_bin="/usr/bin/python3"
    "$py_bin" -B - "$src" "$dst" <<'PY_EOF'
import sqlite3
import sys

src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30)
try:
    dst = sqlite3.connect(sys.argv[2])
    try:
        src.backup(dst)
    finally:
        dst.close()
finally:
    src.close()
PY_EOF
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
    local work
    work="$(mktemp -d "${target}/.work.XXXXXX")" || return 1
    local stage="$work/stage" db_excludes="$work/db-excludes" tar_tmp="$work/archive.tar"
    mkdir -p "$stage"
    : > "$db_excludes"

    log "[backup] Creating backup for profile '$name'..."

    # Snapshot live databases; their raw files and WAL/SHM/journal companions
    # are then excluded so the archive holds only the consistent copy.
    local db rel snapshots=0
    while IFS= read -r -d '' db; do
        rel="${db#"$home"/}"
        mkdir -p "$stage/$(dirname "$rel")"
        if snapshot_sqlite_db "$db" "$stage/$rel" 2>/dev/null; then
            printf './%s\n./%s-wal\n./%s-shm\n./%s-journal\n' "$rel" "$rel" "$rel" "$rel" >> "$db_excludes"
            snapshots=$((snapshots + 1))
        else
            rm -f "$stage/$rel"
            log "debug" "[backup] '$rel' is not a readable SQLite database; archiving raw file"
        fi
    done < <(find "$home" \( -path "$home/backups" -o -path "$home/logs" -o -name venv \
                -o -name node_modules -o -name .cache -o -path "$home/lsp" \) -prune \
                -o -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -print0)

    local tar_rc=0
    tar -cf "$tar_tmp" \
        --exclude="./backups" \
        --exclude="./logs" \
        --exclude="./venv" \
        --exclude="./node_modules" \
        --exclude="./.cache" \
        --exclude="./lsp" \
        --exclude="*.sock" \
        --exclude="*.fifo" \
        --anchored --no-wildcards -X "$db_excludes" \
        -C "$home" . 2>/dev/null || tar_rc=$?
    # 1 = "file changed as we read it", expected for live non-database files.
    if [ "$tar_rc" -le 1 ] && [ "$snapshots" -gt 0 ]; then
        (cd "$stage" && find . -type f -print0) \
            | tar -rf "$tar_tmp" -C "$stage" --null -T - 2>/dev/null || tar_rc=2
    fi
    if [ "$tar_rc" -le 1 ]; then
        gzip -c "$tar_tmp" > "$archive_tmp" || tar_rc=2
    fi
    rm -rf -- "$work"

    if [ "$tar_rc" -le 1 ] && [ -s "$archive_tmp" ]; then
        mv "$archive_tmp" "$archive_dest"
        log "notice" "[backup] Backup created: $archive_dest ($snapshots SQLite snapshot(s))"
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

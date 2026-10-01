#!/bin/bash
# Installed in place of $VENV_DIR/bin/hermes (original renamed to hermes.real,
# invoked here by its full path — substituted at container start).
# Defaults a bare `hermes backup` (no explicit --output/-o) into HA's shared
# backup folder instead of $HOME, so a manually-run backup is discoverable by
# HA-level backup tooling without the user needing to remember a flag. All
# other invocations pass straight through unchanged.
if [ "$1" = "backup" ]; then
    shift
    has_output_flag="false"
    for arg in "$@"; do
        case "$arg" in
            --output|--output=*|-o) has_output_flag="true" ;;
        esac
    done
    if [ "$has_output_flag" = "false" ]; then
        exec HERMES_REAL_BIN_PLACEHOLDER backup --output BACKUP_ROOT_PLACEHOLDER "$@"
    fi
    exec HERMES_REAL_BIN_PLACEHOLDER backup "$@"
fi
exec HERMES_REAL_BIN_PLACEHOLDER "$@"

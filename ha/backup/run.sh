#!/bin/sh
set -eu
while true; do
    if sh /backup/backup-once.sh; then
        touch /tmp/last-backup-ok
        rm -f /tmp/last-backup-failed
        delay="${BACKUP_INTERVAL_SECONDS:-86400}"
    else
        echo 'BACKUP FAILED' >&2
        touch /tmp/last-backup-failed
        delay=30
    fi
    sleep "$delay"
done

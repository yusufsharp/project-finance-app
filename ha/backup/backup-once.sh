#!/bin/sh
set -eu
umask 077
export PGCONNECT_TIMEOUT=5
mkdir -p /backups
# Kernel lock is released even if the backup process is killed.
exec 9>/backups/.lock
if ! flock -n 9; then
    echo 'Another backup is running' >&2
    exit 1
fi
temporary=''
trap 'test -z "$temporary" || rm -f "$temporary"' EXIT
temporary=$(mktemp /backups/.pending.XXXXXX)
pg_dump --host db --username finance --dbname finance --format custom --no-owner --no-acl --file "$temporary"
pg_restore --list "$temporary" >/dev/null
name="finance-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
mv "$temporary" "/backups/$name"
temporary=''
(cd /backups && sha256sum "$name" > "$name.sha256")
echo "/backups/$name"
# Generated names contain no spaces; retain the newest seven successful copies.
ls -1t /backups/finance-*.dump | tail -n +8 | while IFS= read -r old; do
    rm -f "$old" "$old.sha256"
done

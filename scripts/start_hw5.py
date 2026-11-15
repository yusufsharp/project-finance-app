#!/usr/bin/env python3
"""Start HA stack and safely import the original HW4 database, if present."""
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, capture=True, **kwargs):
    output = kwargs.pop("stdout", subprocess.PIPE if capture else None)
    return subprocess.run(["docker", *args], cwd=ROOT, check=True,
                          stdout=output,
                          stderr=None, **kwargs)


def compose(*args, **kwargs):
    return docker("compose", *args, **kwargs)


def setup_secrets():
    file = ROOT / ".env"
    value = file.read_text() if file.exists() else ""
    for key in ("POSTGRES_PASSWORD", "REPLICATION_PASSWORD", "RABBITMQ_ERLANG_COOKIE"):
        if not any(line.startswith(key + "=") for line in value.splitlines()):
            value += "\n" + key + "=" + secrets.token_hex(24) + "\n"
    file.write_text(value)
    file.chmod(0o600)


def primary_sql(query):
    return compose("exec", "-T", "pg1", "sh", "-c",
                   'PGPASSWORD="$POSTGRES_PASSWORD" psql -h db -U finance -d finance -tAc "$1"',
                   "sh", query).stdout.decode().strip()


def main():
    setup_secrets()
    compose("config", "--quiet")
    # Finish image builds before pausing the original application.
    compose("build", "operations", "notifications", "pg1", "frontend", capture=False)
    cid = compose("ps", "-q", "db").stdout.decode().strip()
    legacy = False
    if cid:
        image = docker("inspect", "--format", "{{.Config.Image}}", cid).stdout.decode().strip()
        legacy = image.startswith("postgres:")
    dump = None
    original_count = None
    migration_file = ROOT / "backups" / "hw5-migration.json"
    if not legacy and migration_file.exists():
        migration = json.loads(migration_file.read_text())
        if migration["status"] == "pending":
            dump = ROOT / "backups" / migration["dump"]
            original_count = migration["operation_count"]
            print(f"Resuming preserved migration: {dump}", flush=True)
    if legacy:
        compose("stop", "operations", "notifications", capture=False)
        # Keep the old queue intact rather than silently abandoning pending events.
        queues = compose("exec", "-T", "broker", "rabbitmqctl", "list_queues",
                         "name", "messages_ready", "messages_unacknowledged", "--formatter", "json").stdout.decode()
        for row in json.loads(queues[queues.index("["):]):
            if row["messages_ready"] or row["messages_unacknowledged"]:
                raise RuntimeError("Old queue is not empty. Resume HW4 and drain it before migration.")
        original_count = int(compose("exec", "-T", "db", "psql", "-U", "finance", "-d", "finance",
                                     "-tAc", "SELECT count(*) FROM operations").stdout)
        folder = ROOT / "backups"
        folder.mkdir(exist_ok=True)
        dump = folder / ("before-hw5-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".dump")
        with dump.open("xb") as stream:
            dump.chmod(0o600)
            docker("compose", "exec", "-T", "db", "pg_dump", "-U", "finance", "-d", "finance",
                   "-Fc", "--no-owner", "--no-acl", capture=False, stdout=stream)
        print(f"Preserved HW4 database: {original_count} operations, {dump}", flush=True)
        migration_file.write_text(json.dumps({"status": "pending", "dump": dump.name,
                                               "operation_count": original_count}) + "\n")

    infrastructure = ["etcd1", "etcd2", "etcd3", "pg1", "pg2", "pg3", "db",
                      "rabbit1", "rabbit2", "rabbit3", "broker", "cache"]
    compose("up", "-d", "--wait", "--wait-timeout", "240", *infrastructure, capture=False)
    if dump:
        if primary_sql("SELECT to_regclass('public.operations')"):
            raise RuntimeError("HA database already contains operations. Original dump preserved; refusing to overwrite.")
        with dump.open("rb") as stream:
            compose("exec", "-T", "pg1", "sh", "-c",
                    'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --exit-on-error --single-transaction --no-owner --no-acl -h db -U finance -d finance',
                    stdin=stream, capture=False)
        assert int(primary_sql("SELECT count(*) FROM operations")) == original_count
        migration_file.write_text(json.dumps({"status": "complete", "dump": dump.name,
                                               "operation_count": original_count}) + "\n")
        print(f"Migrated and verified {original_count} operations", flush=True)
    compose("up", "-d", "--wait", "--wait-timeout", "180", capture=False)
    print("HW5 ready: http://127.0.0.1:8080/", flush=True)


if __name__ == "__main__":
    main()

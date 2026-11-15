#!/usr/bin/env python3
"""Crash HA leaders, verify recovery and restore a backup into a separate DB.

Creates clearly labelled demo expenses. Never deletes application data or volumes.
Run against a local demonstration stack without concurrent writers.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PG_NODES = ("pg1", "pg2", "pg3")
RABBIT_NODES = ("rabbit1", "rabbit2", "rabbit3")
REPORT = {"started_at": datetime.now(ZoneInfo("Asia/Bangkok")).isoformat(), "checks": {}}


def run(*args, timeout=35):
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def compose(*args, **kwargs):
    return run("docker", "compose", *args, **kwargs)


def sql(query, database="finance", service="db-backup"):
    # Credentials remain in the container environment, never in argv or the report.
    if service == "db-restore":
        return compose("exec", "-T", service, "psql", "-U", "finance", "-d", database, "-tAc", query)
    return compose("exec", "-T", service, "psql", "-h", "db", "-U", "finance", "-d", database, "-tAc", query)


def wait_for(description, function, timeout=90):
    deadline = time.monotonic() + timeout
    last = "condition not yet satisfied"
    while time.monotonic() < deadline:
        try:
            value = function()
            if value:
                return value
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
            last = str(error)
        time.sleep(0.5)
    raise AssertionError(f"Timed out: {description}: {last}")


def request(path, payload=None):
    req = urllib.request.Request("http://127.0.0.1:8080" + path,
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, dict(response.headers), json.load(response)


def create():
    status, _, body = request("/operations", {"amount": "5.25", "category": "ДЗ 5: проверка HA"})
    assert status == 201
    return body["id"]


def uncached(identifier):
    compose("exec", "-T", "cache", "redis-cli", "DEL", "operation:" + identifier)


def cluster(caller="pg1"):
    return json.loads(compose("exec", "-T", caller, "python3", "-c",
                              "import urllib.request; print(urllib.request.urlopen('http://localhost:8008/cluster', timeout=2).read().decode())"))


def primary(caller="pg1"):
    return next((m["name"] for m in cluster(caller)["members"] if m["role"] == "leader"), None)


def replication_ready():
    return sql("SELECT count(*) = 2 AND count(*) FILTER (WHERE sync_state='sync') >= 1 "
               "FROM pg_stat_replication WHERE state='streaming'") == "t"


def queue(caller="rabbit1"):
    output = compose("exec", "-T", caller, "rabbitmqctl", "list_queues",
                     "name", "type", "leader", "members", "online", "messages_ready", "--formatter", "json")
    rows = json.loads(output[output.index("["):])
    return next(row for row in rows if row["name"] == "operation.created")


def event_published(identifier):
    # Outbox removal follows a publisher confirmation, not just API acceptance.
    return sql(f"SELECT count(*) FROM outbox WHERE id='{identifier}'") == "0"


def consumed(identifier):
    logs = compose("logs", "--no-color", "--tail", "400", "notifications")
    return any("Consumed operation.created" in line and identifier in line for line in logs.splitlines())


@contextmanager
def crashed(service):
    cid = compose("ps", "-q", service)
    assert cid, f"Missing service {service}"
    policy = json.loads(run("docker", "inspect", "--format", "{{json .HostConfig.RestartPolicy}}", cid))
    original_policy = policy["Name"]
    if original_policy == "on-failure" and policy.get("MaximumRetryCount"):
        original_policy += ":" + str(policy["MaximumRetryCount"])
    run("docker", "update", "--restart=no", cid)
    started = time.monotonic()
    try:
        run("docker", "kill", "--signal=KILL", cid)
        state = json.loads(run("docker", "inspect", "--format", "{{json .State}}", cid))
        assert state["ExitCode"] == 137 and not state["Running"], state
        print(f"CRASH {service}: SIGKILL, exit=137", flush=True)
        yield started
    finally:
        run("docker", "update", "--restart=" + original_policy, cid)
        run("docker", "start", cid)
        print(f"REJOIN {service}: restarted, policy={original_policy}", flush=True)


def verify_database():
    wait_for("synchronous replication on all three PG nodes", replication_ready)
    before = primary()
    assert before in PG_NODES
    caller = next(node for node in PG_NODES if node != before)
    identifier = create()
    wait_for("initial event consumed", lambda: consumed(identifier))
    uncached(identifier)
    print(f"DATABASE primary={before}, 2 streaming replicas, one synchronous", flush=True)
    with crashed(before) as started:
        after = wait_for("a different primary", lambda: (node if (node := primary(caller)) and node != before else None))
        wait_for("API write after failover", lambda: create())
        status, headers, row = request("/operations/" + identifier)
        assert status == 200 and headers["X-Data-Source"] == "database"
        assert row["id"] == identifier and row["amount"] == "5.25"
        elapsed = round(time.monotonic() - started, 3)
        assert sql("SELECT pg_is_in_recovery()") == "f"
        REPORT["checks"]["database"] = {"old_primary": before, "new_primary": after,
                                          "rto_seconds": elapsed, "target_seconds": 45,
                                          "preserved_operation": identifier, "read_source": headers["X-Data-Source"]}
        assert elapsed <= 45, elapsed
        print(f"DATABASE FAILOVER {before} -> {after}: {elapsed}s; uncached operation preserved", flush=True)
    wait_for("old primary rejoined as a streaming replica", replication_ready, timeout=120)


def verify_broker():
    wait_for("three quorum members online", lambda: len(queue()["online"]) == 3)
    before_queue = queue()
    assert before_queue["type"] == "quorum" and len(before_queue["members"]) == 3, before_queue
    before = before_queue["leader"].split("@")[-1]
    assert before in RABBIT_NODES
    caller = next(node for node in RABBIT_NODES if node != before)
    compose("stop", "notifications")
    try:
        identifier = create()
        wait_for("confirmed event before crash", lambda: event_published(identifier))
        wait_for("queued event before crash", lambda: queue()["messages_ready"] >= 1)
        print(f"BROKER leader={before}; confirmed event {identifier} waits without a consumer", flush=True)
        with crashed(before) as started:
            after_queue = wait_for("quorum elected another leader", lambda: (q if (q := queue(caller))["leader"] != before_queue["leader"] and len(q["online"]) == 2 else None))
            assert after_queue["messages_ready"] >= 1
            new_identifier = create()
            wait_for("publisher confirmation with failed broker", lambda: event_published(new_identifier))
            compose("start", "notifications")
            wait_for("confirmed pre-crash event survived", lambda: consumed(identifier))
            wait_for("new event delivered with one broker down", lambda: consumed(new_identifier))
            elapsed = round(time.monotonic() - started, 3)
            after = after_queue["leader"].split("@")[-1]
            REPORT["checks"]["broker"] = {"old_leader": before, "new_leader": after,
                                            "rto_seconds": elapsed, "target_seconds": 30,
                                            "confirmed_event_survived": identifier,
                                            "post_crash_event": new_identifier}
            assert elapsed <= 30, elapsed
            print(f"BROKER FAILOVER {before} -> {after}: {elapsed}s; confirmed and new events consumed", flush=True)
    finally:
        compose("start", "notifications")
    wait_for("three RabbitMQ replicas online again", lambda: len(queue()["online"]) == 3, timeout=120)


def verify_backup():
    # Compare all operation fields, not merely a successful pg_restore exit status.
    fingerprint_query = "SELECT count(*) || ':' || coalesce(md5(string_agg(row_to_json(o)::text, '|' ORDER BY id)), '') FROM operations o"
    expected = sql(fingerprint_query)
    path = compose("exec", "-T", "db-backup", "sh", "/backup/backup-once.sh")
    name = Path(path).name
    assert name.startswith("finance-") and name.endswith(".dump"), name
    file = ROOT / "backups" / name
    checksum = file.with_suffix(".dump.sha256").read_text().split()[0]
    assert hashlib.sha256(file.read_bytes()).hexdigest() == checksum
    assert sql(fingerprint_query) == expected, "Concurrent writes: repeat test with no other clients"
    started = time.monotonic()
    compose("--profile", "restore", "up", "-d", "--wait", "db-restore", timeout=100)
    restored_database = "verify_" + uuid.uuid4().hex
    compose("exec", "-T", "db-restore", "createdb", "-U", "finance", restored_database)
    compose("exec", "-T", "db-restore", "pg_restore", "--exit-on-error", "--single-transaction",
            "--no-owner", "--no-acl", "-U", "finance", "-d", restored_database, "/backups/" + name)
    actual = sql(fingerprint_query, database=restored_database, service="db-restore")
    assert actual == expected, (expected, actual)
    # Business-level check in the recovered copy.
    identifier = REPORT["checks"]["database"]["preserved_operation"]
    assert sql(f"SELECT amount FROM operations WHERE id='{identifier}'", database=restored_database, service="db-restore") == "5.25"
    elapsed = round(time.monotonic() - started, 3)
    assert elapsed <= 60, elapsed
    REPORT["checks"]["backup_restore"] = {"archive": name, "sha256": checksum,
                                             "restored_database": restored_database,
                                             "matching_fingerprint": actual,
                                             "restore_seconds": elapsed, "target_seconds": 60}
    print(f"BACKUP {name}: SHA-256 OK; restored into {restored_database}; all rows match, {elapsed}s", flush=True)


def main():
    verify_database()
    verify_broker()
    verify_backup()
    REPORT["status"] = "passed"
    REPORT["finished_at"] = datetime.now(ZoneInfo("Asia/Bangkok")).isoformat()
    print("ДЗ 5: all checks passed", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        REPORT["status"] = "failed"
        REPORT["error"] = str(error)
        raise
    finally:
        output = ROOT / "output" / "hw5"
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + "\n")

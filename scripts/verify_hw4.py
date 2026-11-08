#!/usr/bin/env python3
"""Integration checks against the running Compose stack; creates one demo expense."""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    return subprocess.check_output(
        ["docker", "compose", *args], cwd=ROOT, text=True,
        stderr=subprocess.STDOUT, timeout=30,
    )


def execute(service, code, user="nobody"):
    return compose("exec", "-T", "--user", user, service, "python", "-c", code)


def probe(service, host, port, allowed):
    result = json.loads(execute(service, f"""
import errno, json, socket
try:
    with socket.create_connection(({host!r}, {port}), timeout=2):
        print(json.dumps({{"connected": True}}))
except OSError as error:
    print(json.dumps({{"connected": False, "error": errno.errorcode.get(error.errno)}}))
"""))
    assert result["connected"] == allowed, (service, host, port, result)
    if not allowed:
        # A timeout, no route, or no listener does not prove the explicit FW reject.
        assert result["error"] in ("EHOSTUNREACH", "EACCES", "EPERM"), result
    print(f'{"ALLOW" if allowed else "DENY "} {service} -> {host}:{port}')


def rules(service):
    return compose("exec", "-T", "--user", "root", service,
                   "iptables", "-nvxL", "OUTPUT")


def counter(output, name):
    return next(int(line.split()[0]) for line in output.splitlines()
                if f"/* {name} */" in line)


def request(path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8080" + path, data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, response.headers, json.load(response)


def main():
    for service in ("operations", "notifications"):
        identity = execute(service, "import os; print(os.getuid())").strip()
        assert identity != "0", (service, "probe must run as application user")
        # Check PID 1 as well: docker exec defaults alone do not prove app privileges.
        status = execute(service, "print(open('/proc/1/status').read())")
        assert "Uid:\t65534\t65534\t65534\t65534" in status, status
        assert "CapEff:\t0000000000000000" in status, status
        configured = compose("exec", "-T", "--user", "root", service, "iptables", "-S")
        assert "-P INPUT DROP" in configured and "-P OUTPUT DROP" in configured

    # Verify attachment to actual networks, independently of firewall behavior.
    cid = compose("ps", "-q", "notifications").strip()
    networks = json.loads(subprocess.check_output(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", cid],
        text=True, timeout=10,
    ))
    assert len(networks) == 1
    assert next(iter(networks.values()))["IPAddress"] == "172.29.30.30"
    print("SEGMENTATION notifications is connected only to messaging")

    before = rules("notifications")
    probe("operations", "172.29.20.10", 5432, True)
    probe("notifications", "172.29.20.10", 5432, False)
    probe("operations", "172.29.20.11", 6379, True)
    probe("notifications", "172.29.20.11", 6379, False)
    for service in ("operations", "notifications"):
        probe(service, "broker", 5672, True)
    probe("notifications", "172.29.30.20", 8080, False)
    probe("operations", "172.29.20.10", 5433, False)
    after = rules("notifications")
    for name in ("deny-db", "deny-cache"):
        assert counter(after, name) > counter(before, name), (name, before, after)
        print(f'COUNTER {name}: {counter(before, name)} -> {counter(after, name)}')

    status, _, created = request("/operations", {"amount": "450.50", "category": "ДЗ 4"})
    assert status == 201, status
    identifier = created["id"]
    for source in ("database", "cache"):
        status, headers, row = request("/operations/" + identifier)
        assert status == 200 and row["id"] == identifier, row
        assert row["amount"] == "450.50", row
        assert headers["X-Data-Source"] == source, dict(headers)
        print(f'HTTP GET -> {source}')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        logs = compose("logs", "--no-color", "--tail", "100", "notifications")
        if any("Consumed operation.created" in line and identifier in line
               for line in logs.splitlines()):
            break
        time.sleep(0.5)
    else:
        raise AssertionError(f"Notification event not consumed: {identifier}")
    print(f'EVENT operation.created {identifier} consumed by notifications')
    print("ДЗ 4: all checks passed")


if __name__ == "__main__":
    main()

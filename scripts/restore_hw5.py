#!/usr/bin/env python3
"""Restore a verified dump into a new database on the isolated restore instance."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    return subprocess.check_output(["docker", "compose", *args], cwd=ROOT, text=True, timeout=120).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    assert archive.parent == (ROOT / "backups").resolve(), "Use an archive from this project's backups directory"
    assert archive.name.startswith("finance-") and archive.suffix == ".dump"
    checksum = archive.with_suffix(".dump.sha256").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum, "Checksum mismatch"
    compose("--profile", "restore", "up", "-d", "--wait", "db-restore")
    database = "restored_" + uuid.uuid4().hex
    compose("exec", "-T", "db-restore", "createdb", "-U", "finance", database)
    compose("exec", "-T", "db-restore", "pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
            "-U", "finance", "-d", database, "/backups/" + archive.name)
    rows = compose("exec", "-T", "db-restore", "psql", "-U", "finance", "-d", database,
                   "-tAc", "SELECT count(*) FROM operations")
    print(json.dumps({"database": database, "operations": int(rows), "archive": archive.name, "sha256": checksum}, indent=2))


if __name__ == "__main__":
    main()

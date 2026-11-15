#!/usr/bin/env python3
"""Temporarily block operations -> db and prove HTTP failure and recovery."""
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULE = ['-p', 'tcp', '-d', '172.29.20.10', '--dport', '5432', '-m', 'comment',
        '--comment', 'defense-db-block', '-j', 'REJECT', '--reject-with', 'tcp-reset']


def firewall(*args):
    return subprocess.check_output(['docker', 'compose', 'exec', '-T', '--user', 'root',
                                    'operations', 'iptables', *args], cwd=ROOT, text=True, timeout=20)


def status():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080/operations', timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main():
    assert status() == 200, 'API must be healthy before blocking'
    print('BEFORE  GET /operations: HTTP 200', flush=True)
    inserted = False
    try:
        firewall('-I', 'OUTPUT', '1', *RULE)
        inserted = True
        assert status() == 503, 'DB block must cause HTTP 503, not a cached success'
        print('BLOCKED operations -> 172.29.20.10:5432: HTTP 503', flush=True)
        output = firewall('-nvxL', 'OUTPUT')
        rule = next(line for line in output.splitlines() if 'defense-db-block' in line)
        assert int(rule.split()[0]) > 0, rule
        print(rule, flush=True)
    finally:
        if inserted:
            firewall('-D', 'OUTPUT', *RULE)
            print('CLEANUP temporary rule removed', flush=True)
    assert status() == 200, 'API must recover after rule removal'
    print('AFTER   GET /operations: HTTP 200', flush=True)
    print('PASS: legitimate connection blocked, error observed, service recovered', flush=True)


if __name__ == '__main__':
    main()

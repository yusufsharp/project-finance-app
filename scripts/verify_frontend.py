#!/usr/bin/env python3
"""Verify the real frontend/API and cache coherence; remove only created test rows."""
import json
import urllib.error
import urllib.request
import uuid

BASE = 'http://127.0.0.1:8080'


def request(path, method='GET', payload=None, expected=200):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read()
        assert response.status == expected, (method, path, response.status, content)
        return response.headers, json.loads(content) if 'application/json' in response.headers.get('Content-Type', '') else content.decode()


def main():
    headers, page = request('/')
    assert 'text/html' in headers['Content-Type'] and 'Копилка' in page
    assert "script-src 'self'" in headers['Content-Security-Policy']
    for path, content_type in [('/app.js', 'javascript'), ('/styles.css', 'text/css')]:
        headers, _ = request(path)
        assert content_type in headers['Content-Type']
    request('/missing-file.js', expected=404)
    _, ready = request('/health/ready')
    assert ready['ready']
    _, hello = request('/api')
    assert hello['message'] == 'Hello, world!'
    _, initial = request('/operations')
    original_ids = {row['id'] for row in initial['operations']}
    created = []
    marker = 'UI test ' + uuid.uuid4().hex[:8]
    try:
        for invalid in [
            {'amount': '-1', 'category': marker},
            {'amount': '1.001', 'category': marker},
            {'amount': 'NaN', 'category': marker},
            {'amount': 'Infinity', 'category': marker},
            {'amount': '1', 'category': ' '},
            {'amount': '1', 'category': 'x' * 101},
            {'amount': '1', 'category': marker, 'kind': 'other'},
            [],
        ]:
            request('/operations', 'POST', invalid, expected=400)
        _, expense = request('/operations', 'POST', {'amount': '12.34', 'category': marker}, expected=201)
        created.append(expense['id'])
        assert expense['kind'] == 'expense' and expense['amount'] == '12.34'
        path = '/operations/' + expense['id']
        for source in ('database', 'cache'):
            headers, row = request(path)
            assert headers['X-Data-Source'] == source and row['kind'] == 'expense'
        _, updated = request(path, 'PUT', {'amount': '56.78', 'category': marker + ' edited', 'kind': 'income'})
        assert updated['created_at'] == expense['created_at']
        headers, row = request(path)
        assert headers['X-Data-Source'] == 'database'
        assert row['amount'] == '56.78' and row['kind'] == 'income' and row['category'] == marker + ' edited'
        headers, _ = request(path)
        assert headers['X-Data-Source'] == 'cache'
        # Rejected updates cannot partially modify an existing record.
        request(path, 'PUT', {'amount': '0', 'category': marker}, expected=400)
        _, row = request(path)
        assert row['amount'] == '56.78' and row['kind'] == 'income'
        _, all_rows = request('/operations')
        assert next(r for r in all_rows['operations'] if r['id'] == expense['id']) == updated
        request(path, 'DELETE')
        request(path, expected=404)
        request(path, 'DELETE', expected=404)
        request('/operations/not-a-uuid', expected=404)
        request('/operations/' + str(uuid.uuid4()), 'PUT', {'amount': '1', 'category': marker}, expected=404)
        _, final = request('/operations')
        assert {r['id'] for r in final['operations']} == original_ids
        print('PASS: frontend, assets, readiness, CRUD, validation, cache update/delete, original data preserved')
    finally:
        for identifier in created:
            request('/operations/' + identifier, 'DELETE', expected=404 if not any(r['id'] == identifier for r in request('/operations')[1]['operations']) else 200)


if __name__ == '__main__':
    main()

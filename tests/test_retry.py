#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for the Kentik client's retry, rate limit and non-JSON handling
'''

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import requests
from conftest import make_config
from ibx_kentik_prepop.targets.kentik import (KENTIK, MAX_RETRY_WAIT,
                                              retry_wait, should_retry)


class FakeResponse:
    '''
    Minimal stand-in for a requests.Response
    '''

    def __init__(self, status=200, payload='{}', headers=None):
        self.status_code = status
        self.text = payload
        self.content = payload.encode('utf-8')
        self.headers = headers or {}
        return

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code}', response=self)
        return


class FakeSession:
    '''
    Session that replays a scripted list of responses or exceptions
    '''

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.headers = {}
        self.verify = True
        return

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url))
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client(script, slept, monkeypatch):
    '''
    A KENTIK whose session is scripted and whose sleep is recorded

    time.sleep is patched through the fixture so it is restored afterwards -
    replacing it on the module would leak into every other test.
    '''
    import ibx_kentik_prepop.targets.kentik as module
    kentik = KENTIK(make_config())
    kentik.session = FakeSession(script)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: slept.append(seconds))
    return kentik


def test_a_rate_limited_read_is_retried_then_succeeds(monkeypatch):
    slept = []
    kentik = client([FakeResponse(429, '{}', {'Retry-After': '0'}),
                     FakeResponse(200, '{"sites": []}')], slept, monkeypatch)

    assert kentik._request('GET', 'https://example/sites') == {'sites': []}
    assert len(kentik.session.calls) == 2
    assert not kentik.last_error


def test_a_create_is_never_repeated_on_an_ambiguous_failure(monkeypatch):
    # A 502 on a POST may mean the device was created. Repeating it would burn
    # a second licensed slot, so it must be reported rather than retried.
    slept = []
    kentik = client([FakeResponse(502, 'gateway'), FakeResponse(200, '{}')], slept, monkeypatch)

    assert kentik._request('POST', 'https://example/device') is None
    assert len(kentik.session.calls) == 1
    assert kentik.last_error['status'] == 502


def test_a_create_is_retried_when_kentik_says_it_did_not_process_it(monkeypatch):
    slept = []
    kentik = client([FakeResponse(429, 'slow down', {'Retry-After': '0'}),
                     FakeResponse(200, '{"device": {"id": "7"}}')], slept, monkeypatch)

    assert kentik._request('POST', 'https://example/device') == {'device': {'id': '7'}}
    assert len(kentik.session.calls) == 2


def test_a_timed_out_write_is_not_repeated():
    assert should_retry('POST', requests.Timeout('too slow'), 0, 3) is False
    assert should_retry('GET', requests.Timeout('too slow'), 0, 3) is True


def test_retries_stop_at_the_configured_limit(monkeypatch):
    slept = []
    kentik = client([FakeResponse(503, 'busy') for _ in range(6)], slept, monkeypatch)
    kentik.retries = 2

    assert kentik._request('GET', 'https://example/sites') is None
    assert len(kentik.session.calls) == 3
    assert kentik.last_error['attempts'] == 3


def test_a_non_json_two_hundred_is_reported_not_raised(monkeypatch):
    slept = []
    kentik = client([FakeResponse(200, '<html>captive portal</html>')], slept, monkeypatch)

    assert kentik._request('GET', 'https://example/sites') is None
    assert 'not JSON' in kentik.last_error['message']


def test_retry_after_seconds_is_honoured():
    response = FakeResponse(429, '', {'Retry-After': '12'})
    assert retry_wait(response, 0, 2.0) == 12.0


def test_retry_after_http_date_is_honoured():
    when = datetime.now(timezone.utc) + timedelta(seconds=20)
    response = FakeResponse(429, '', {'Retry-After': format_datetime(when)})
    assert 15 <= retry_wait(response, 0, 2.0) <= 21


def test_retry_after_is_capped():
    response = FakeResponse(429, '', {'Retry-After': '86400'})
    assert retry_wait(response, 0, 2.0) == MAX_RETRY_WAIT


def test_backoff_doubles_without_a_header():
    assert retry_wait(FakeResponse(503), 0, 2.0) == 2.0
    assert retry_wait(FakeResponse(503), 1, 2.0) == 4.0
    assert retry_wait(FakeResponse(503), 2, 2.0) == 8.0

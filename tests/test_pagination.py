#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for reading every page of a Kentik list

A truncated read is the one failure that quietly changes the answer: a device
that was not read back is planned as a create, which costs a licence slot.
'''

import json
from conftest import make_config
from ibx_kentik_prepop.targets.kentik import (KENTIK, next_page_token,
                                              reported_total)
from test_retry import FakeResponse, FakeSession


def client(script):
    '''
    A KENTIK whose session replays the given responses
    '''
    kentik = KENTIK(make_config())
    kentik.session = FakeSession(script)
    return kentik


def page(items, key='devices', token=None, total=None):
    '''
    One gRPC-gateway style list response
    '''
    body = {key: items, 'pagination': {}}
    if token is not None:
        body['pagination']['next_page_token'] = token
    if total is not None:
        body['pagination']['total_count'] = total
    return FakeResponse(200, json.dumps(body))


def test_every_page_is_read():
    kentik = client([page([{'id': '1'}, {'id': '2'}], token='2', total=4),
                     page([{'id': '3'}, {'id': '4'}], total=4)])

    devices = kentik.get_devices()

    assert [d['id'] for d in devices] == ['1', '2', '3', '4']
    assert kentik.truncation_warnings == []


def test_a_single_page_response_still_works():
    # The shape this client saw before paging existed: no pagination block
    kentik = client([FakeResponse(200, '{"sites": [{"id": "42"}]}')])

    assert kentik.get_sites() == [{'id': '42'}]
    assert kentik.truncation_warnings == []


def test_a_short_read_is_reported():
    # Kentik says there are 9 but stops offering pages after 2
    kentik = client([page([{'id': '1'}, {'id': '2'}], total=9)])

    assert len(kentik.get_devices()) == 2
    assert kentik.truncation_warnings
    assert 'only 2 were read' in kentik.truncation_warnings[0]


def test_a_page_token_that_is_not_honoured_does_not_loop_forever():
    '''
    If the page token parameter name is wrong the API keeps serving page one.
    Stop and name the knob rather than spinning.
    '''
    kentik = client([page([{'id': '1'}], token='same') for _ in range(20)])

    devices = kentik.get_devices()

    assert len(kentik.session.calls) == 2
    assert len(devices) == 2
    assert 'page token' in kentik.truncation_warnings[0]


def test_the_page_cap_is_reported():
    kentik = client([page([{'id': str(n)}], token=str(n + 1))
                     for n in range(30)])
    kentik.max_pages = 3

    kentik.get_devices()

    assert len(kentik.session.calls) == 3
    assert 'stopped after 3 pages' in kentik.truncation_warnings[0]


def test_page_size_is_only_sent_when_configured():
    kentik = client([page([], total=0)])
    assert kentik.page_size == 0

    kentik.get_devices()
    assert kentik.session.params == [None]


def test_a_configured_page_size_is_sent():
    kentik = client([page([], total=0)])
    kentik.page_size = 500

    kentik.get_devices()
    assert kentik.session.params[0] == {'page_size': 500}


def test_a_failed_read_returns_nothing_without_claiming_truncation():
    kentik = client([FakeResponse(500, 'boom')])
    kentik.retries = 0

    assert kentik.get_devices() == []
    assert kentik.truncation_warnings == []


def test_token_and_total_are_read_from_either_spelling():
    assert next_page_token({'nextPageToken': 'abc'}) == 'abc'
    assert next_page_token({'pagination': {'next_page_token': 'xyz'}}) == 'xyz'
    assert next_page_token({'pagination': {}}) == ''
    assert reported_total({'pagination': {'totalCount': 12}}) == 12
    assert reported_total({'total': '7'}) == 7
    assert reported_total({'sites': []}) is None


def test_a_page_count_is_not_mistaken_for_a_total():
    # 'count' is the size of the page on some APIs, so it must not be read as
    # the size of the collection - a false warning every run teaches the
    # operator to ignore the real one
    assert reported_total({'count': 2}) is None

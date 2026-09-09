#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for applying a plan to Kentik
'''

from dataclasses import replace
from conftest import make_config, make_site
from ibx_kentik_prepop.apply import apply_plan, load_state
from ibx_kentik_prepop.model import (ACTION_CREATE, ACTION_NO_CHANGE,
                                     ACTION_UPDATE, Device, Plan, SitePlan)


class RecordingKentik:
    def __init__(self):
        self.creates = []
        self.updates = []
        self.last_error = {}

    def error_text(self):
        return '400: site title already in use' if self.last_error else ''

    def create_site(self, site, networks):
        self.creates.append((site.name, networks))
        return {'id': '101'}

    def update_site(self, site_id, site, networks, raw_site=None):
        self.updates.append((site_id, site.name, networks, raw_site))
        return {'id': site_id}


def make_plan():
    plan = Plan(source='uddi', site_key='Site')
    plan.entries = [
        SitePlan(site=make_site('NEW-SITE', ('10.3.0.0/24',)), action=ACTION_CREATE,
                 merged={'user_access': ['10.3.0.0/24']}),
        SitePlan(site=make_site('OLD-SITE', ('10.1.0.0/24',)), action=ACTION_UPDATE,
                 kentik_id='42', merged={'user_access': ['10.1.0.0/24']},
                 raw_site={'id': '42', 'siteMarket': 'EMEA'}),
        SitePlan(site=make_site('SAME-SITE', ('10.2.0.0/24',)), action=ACTION_NO_CHANGE,
                 kentik_id='43'),
    ]
    return plan


def test_apply_creates_updates_and_skips(tmp_path):
    config = replace(make_config(), state_file=str(tmp_path / 'state.json'))
    kentik = RecordingKentik()
    results = apply_plan(config, make_plan(), kentik)

    assert results['created'] == ['NEW-SITE']
    assert results['updated'] == ['OLD-SITE']
    assert results['unchanged'] == ['SAME-SITE']
    assert results['failed'] == []
    assert len(kentik.creates) == 1
    assert len(kentik.updates) == 1


def test_apply_passes_the_existing_site_so_unmanaged_fields_survive(tmp_path):
    config = replace(make_config(), state_file=str(tmp_path / 'state.json'))
    kentik = RecordingKentik()
    apply_plan(config, make_plan(), kentik)
    site_id, name, networks, raw_site = kentik.updates[0]

    assert site_id == '42'
    assert raw_site['siteMarket'] == 'EMEA'


def test_apply_records_state_and_the_device_note(tmp_path):
    state_file = tmp_path / 'state.json'
    config = replace(make_config(), state_file=str(state_file))
    plan = make_plan()
    plan.devices = [Device(name='rtr1')]

    results = apply_plan(config, plan, RecordingKentik())
    state = load_state(str(state_file))

    assert results['notes'] and 'Switch the task to devices' in results['notes'][0]
    assert state['sites']['NEW-SITE']['id'] == '101'
    assert state['runs'][-1]['created'] == 1


def test_apply_emits_progress_events(tmp_path):
    config = replace(make_config(), state_file=str(tmp_path / 'state.json'))
    plan = make_plan()
    plan.devices = [Device(name='rtr1')]
    events = []

    apply_plan(config, plan, RecordingKentik(), on_event=events.append)
    kinds = [e['type'] for e in events]
    sites = {e['site']: e['status'] for e in events if e['type'] == 'site'}

    assert kinds[0] == 'start'
    assert kinds[-1] == 'done'
    assert 'note' in kinds
    assert sites == {'NEW-SITE': 'created', 'OLD-SITE': 'updated',
                     'SAME-SITE': 'unchanged'}
    assert events[0]['to_change'] == 2
    assert events[-1]['created'] == 1


def test_apply_events_carry_the_failure_reason(tmp_path):
    class FailingKentik(RecordingKentik):
        def update_site(self, site_id, site, networks, raw_site=None):
            self.last_error = {'status': 403}
            return None

    config = replace(make_config(), state_file=str(tmp_path / 'state.json'))
    events = []
    apply_plan(config, make_plan(), FailingKentik(), on_event=events.append)
    failed = [e for e in events if e.get('status') == 'failed']

    assert failed[0]['site'] == 'OLD-SITE'
    assert 'already in use' in failed[0]['error']
    assert events[-1]['failed'] == 1


def test_apply_reports_failures(tmp_path):
    class FailingKentik(RecordingKentik):
        def create_site(self, site, networks):
            self.last_error = {'status': 400}
            return None

    config = replace(make_config(), state_file=str(tmp_path / 'state.json'))
    results = apply_plan(config, make_plan(), FailingKentik())

    assert results['failed'] == ['NEW-SITE']
    assert results['errors']['NEW-SITE'] == '400: site title already in use'

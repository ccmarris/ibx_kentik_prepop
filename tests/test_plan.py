#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for plan construction and the Kentik diff
'''

from conftest import make_config, record
from ibx_kentik_prepop import plan as plan_module
from ibx_kentik_prepop.model import (ACTION_CREATE, ACTION_NO_CHANGE,
                                     ACTION_UPDATE, Device, Plan)
from ibx_kentik_prepop.plan import attach_devices, build_plan, merge_networks
from ibx_kentik_prepop.summarise import build_sites


class FakeSource:
    name = 'fake'

    def __init__(self, records, devices=None):
        self.records = records
        self.devices = devices or []

    def get_subnets(self):
        return self.records

    def get_devices(self, subnets=None):
        return self.devices


class FakeKentik:
    def __init__(self, sites):
        self.sites = sites

    def site_index(self):
        return {s['title'].casefold(): s for s in self.sites}

    @staticmethod
    def existing_networks(raw_site):
        classification = raw_site.get('addressClassification') or {}
        return {
            'infrastructure': classification.get('infrastructureNetworks', []),
            'user_access': classification.get('userAccessNetworks', []),
            'other': classification.get('otherNetworks', []),
        }


def test_merge_networks_adds_by_default():
    merged, added, removed = merge_networks({'user_access': ['10.1.0.0/24']},
                                            {'user_access': ['192.168.0.0/24']},
                                            replace=False)
    assert merged['user_access'] == ['10.1.0.0/24', '192.168.0.0/24']
    assert added['user_access'] == ['10.1.0.0/24']
    assert removed == {}


def test_merge_networks_replace_removes_unmanaged():
    merged, added, removed = merge_networks({'user_access': ['10.1.0.0/24']},
                                            {'user_access': ['192.168.0.0/24']},
                                            replace=True)
    assert merged['user_access'] == ['10.1.0.0/24']
    assert removed['user_access'] == ['192.168.0.0/24']


def test_merge_networks_no_change():
    merged, added, removed = merge_networks({'user_access': ['10.1.0.0/24']},
                                            {'user_access': ['10.1.0.0/24']},
                                            replace=False)
    assert merged['user_access'] == ['10.1.0.0/24']
    assert (added, removed) == ({}, {})


def test_attach_devices_matches_case_insensitively():
    config = make_config()
    sites, _ = build_sites([record('10.1.0.0/24', site='LON-DC1')], config)
    plan = Plan()
    devices = [Device(name='rtr1', site_name='lon-dc1'),
               Device(name='rtr2', site_name='NOWHERE')]
    attach_devices(sites, devices, plan)

    assert [d.name for d in sites[0].devices] == ['rtr1']
    assert plan.warnings[0].category == 'devices_without_site'


def test_build_plan_without_kentik_plans_creates(monkeypatch):
    config = make_config()
    records = [record('10.1.0.0/24', site='LON-DC1')]
    monkeypatch.setattr(plan_module, 'get_source', lambda cfg: FakeSource(records))

    plan = build_plan(config, kentik=None)

    assert len(plan.entries) == 1
    assert plan.entries[0].action == ACTION_CREATE
    assert plan.entries[0].added['user_access'] == ['10.1.0.0/24']
    assert plan.stats()['actions'][ACTION_CREATE] == 1


def test_build_plan_diffs_existing_site(monkeypatch):
    config = make_config()
    records = [record('10.1.0.0/24', site='LON-DC1'),
               record('10.5.0.0/24', site='NYC-BR2')]
    monkeypatch.setattr(plan_module, 'get_source', lambda cfg: FakeSource(records))

    kentik = FakeKentik([{
        'id': '42',
        'title': 'LON-DC1',
        'addressClassification': {'userAccessNetworks': ['10.1.0.0/24']},
    }])
    plan = build_plan(config, kentik=kentik)
    actions = {e.site.name: e.action for e in plan.entries}

    assert actions == {'LON-DC1': ACTION_NO_CHANGE, 'NYC-BR2': ACTION_CREATE}
    lon = [e for e in plan.entries if e.site.name == 'LON-DC1'][0]
    assert lon.kentik_id == '42'


def test_build_plan_updates_when_a_prefix_is_missing(monkeypatch):
    config = make_config()
    records = [record('10.1.0.0/24', site='LON-DC1'),
               record('10.1.2.0/24', site='LON-DC1')]
    monkeypatch.setattr(plan_module, 'get_source', lambda cfg: FakeSource(records))

    kentik = FakeKentik([{
        'id': '42',
        'title': 'lon-dc1',
        'addressClassification': {'userAccessNetworks': ['10.1.0.0/24']},
    }])
    plan = build_plan(config, kentik=kentik)
    entry = plan.entries[0]

    assert entry.action == ACTION_UPDATE
    assert entry.added['user_access'] == ['10.1.2.0/24']
    assert entry.merged['user_access'] == ['10.1.0.0/24', '10.1.2.0/24']


def test_build_plan_warns_when_no_subnets(monkeypatch):
    config = make_config()
    monkeypatch.setattr(plan_module, 'get_source', lambda cfg: FakeSource([]))
    plan = build_plan(config, kentik=None)
    assert 'no_subnets' in [w.category for w in plan.warnings]


def test_build_plan_filters_device_roles(monkeypatch):
    config = make_config(devices=True, use_gateways=True)
    records = [record('10.1.0.0/24', site='LON-DC1', gateways=['10.1.0.1'])]
    devices = [Device(name='rtr1', role='router', site_name='LON-DC1'),
               Device(name='ap1', role='other', site_name='LON-DC1')]
    monkeypatch.setattr(plan_module, 'get_source',
                        lambda cfg: FakeSource(records, devices))
    monkeypatch.setattr(plan_module, 'get_device_source',
                        lambda cfg, src, p: src)

    plan = build_plan(config, kentik=None)
    assert [d.name for d in plan.devices] == ['rtr1']


def test_fingerprint_covers_the_outcome_not_the_timestamp():
    from ibx_kentik_prepop.plan import plan_fingerprint
    from ibx_kentik_prepop.model import SitePlan
    from conftest import make_site

    def plan_for(networks, action=ACTION_CREATE):
        plan = Plan(source='uddi', site_key='Site', generated='then')
        plan.entries = [SitePlan(site=make_site('LON-DC1'), action=action,
                                 merged={'user_access': networks})]
        return plan

    baseline = plan_fingerprint(plan_for(['10.1.0.0/24']))
    later = plan_for(['10.1.0.0/24'])
    later.generated = 'now'

    assert plan_fingerprint(later) == baseline
    assert plan_fingerprint(plan_for(['10.1.0.0/24', '10.2.0.0/24'])) != baseline
    assert plan_fingerprint(plan_for(['10.1.0.0/24'],
                                     action=ACTION_UPDATE)) != baseline

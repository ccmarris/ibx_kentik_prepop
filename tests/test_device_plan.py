#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for the device task: planning, guards and applying
'''

from dataclasses import replace
from conftest import make_config, record
from ibx_kentik_prepop import plan as plan_module
from ibx_kentik_prepop.apply import apply_device_plan
from ibx_kentik_prepop.model import (ACTION_CREATE, ACTION_EXISTS, ACTION_UPDATE,
                                     Device)
from ibx_kentik_prepop.plan import (build_device_plan, device_apply_problems,
                                    device_mismatch, is_excluded)


def device_config(**overrides):
    base = make_config(devices=True, use_gateways=True)
    defaults = dict(enabled=True, use_gateways=True)
    defaults.update(overrides)
    return replace(base, task='devices',
                   device=replace(base.device, **defaults))


class FakeSource:
    name = 'fake'

    def __init__(self, records, devices):
        self.records = records
        self._devices = devices

    def get_subnets(self):
        return self.records

    def get_devices(self, subnets=None):
        return self._devices


class FakeKentik:
    def __init__(self, sites=None, devices=None, plans=None):
        self.sites = sites or [{'id': '42', 'title': 'LON-DC1'}]
        self.devices = devices or []
        self.plans = plans if plans is not None else [
            {'id': 7, 'name': 'Free_Flow', 'active': True, 'max_devices': 5,
             'devices': [{'id': 'x'}]}]
        self.created = []
        self.updated = []
        self.fail = set()

    def use_config(self, config):
        self.config = config
        return self

    # planning surface
    def site_index(self):
        return {s['title'].casefold(): s for s in self.sites}

    def device_index(self):
        return {str(d['device_name']).casefold(): d for d in self.devices}

    def resolve_plan(self, name='', plan_id=0, plans=None):
        from ibx_kentik_prepop.targets.kentik import KENTIK
        return KENTIK.resolve_plan(self, name, plan_id, self.plans)

    def list_plans(self):
        return self.plans

    @staticmethod
    def plan_capacity(plan):
        from ibx_kentik_prepop.targets.kentik import KENTIK
        return KENTIK.plan_capacity(plan)

    # apply surface
    def get_device(self, device_id):
        return next((d for d in self.devices if str(d['id']) == str(device_id)), None)

    def create_device(self, device, site_id=''):
        if device.name in self.fail:
            return None
        self.created.append((device.name, site_id, tuple(device.sending_ips)))
        return {'id': f'dev-{len(self.created)}'}

    def update_device_placement(self, raw_device, device, site_id=''):
        self.updated.append((device.name, site_id))
        return {'id': raw_device['id']}

    def error_text(self):
        return '400: device name already in use'


def subnets():
    return [record('10.1.0.0/24', site='LON-DC1'),
            record('10.1.4.0/30', site='LON-DC1'),
            record('10.2.0.0/24', site='NYC-BR2')]


def devices():
    return [
        Device(name='lon-rtr-01', mgmt_ip='10.1.0.1', role='router',
               interfaces=[{'address': '10.1.0.1'}, {'address': '10.1.4.1'}],
               origin='network_insight'),
        Device(name='nyc-fw-01', mgmt_ip='10.2.0.1', role='firewall',
               interfaces=[{'address': '10.2.0.1'}], origin='network_insight'),
    ]


def build(config=None, kentik=None, source_devices=None, monkeypatch=None):
    config = config or device_config()
    source = FakeSource(subnets(), source_devices or devices())
    monkeypatch.setattr(plan_module, 'get_source', lambda cfg: source)
    monkeypatch.setattr(plan_module, 'get_device_source',
                        lambda cfg, src, p: source)
    return build_device_plan(config, kentik)


def test_is_excluded_matches_name_kentik_name_or_ip():
    device = Device(name='lon-rtr-01', mgmt_ip='10.1.0.1')

    assert is_excluded(device, ['LON-RTR-01'])[0] is True
    assert is_excluded(device, ['lon_rtr_01'])[0] is True
    assert is_excluded(device, ['10.1.0.1'])[0] is True
    assert is_excluded(device, ['something-else']) == (False, '')


def test_device_mismatch_only_covers_derived_fields():
    config = make_config()
    device = Device(name='lon-rtr-01', mgmt_ip='10.1.0.1')
    raw = {'site_id': 9, 'sending_ips': ['10.9.9.9'],
           'device_snmp_community': 'private'}
    mismatch = device_mismatch(raw, device, '42', config)

    assert set(mismatch) == {'site_id', 'sending_ips'}
    assert mismatch['site_id'] == {'kentik': '9', 'derived': '42'}


def test_build_device_plan_places_devices_and_resolves_the_plan(monkeypatch):
    kentik = FakeKentik()
    plan, config = build(kentik=kentik, monkeypatch=monkeypatch)
    assert kentik.config.device.plan_id == 7
    actions = {e.device.name: e.action for e in plan.device_entries}

    assert actions == {'lon-rtr-01': ACTION_CREATE, 'nyc-fw-01': ACTION_CREATE}
    assert plan.plan_id == 7
    assert plan.capacity['remaining'] == 4
    assert config.device.plan_id == 7

    lon = [e for e in plan.device_entries if e.device.name == 'lon-rtr-01'][0]
    assert lon.site_id == '42'
    assert lon.device.site_match == 'interface'
    assert lon.device.sending_ips == ('10.1.0.1',)


def test_sending_ips_all_uses_every_interface(monkeypatch):
    plan, _ = build(config=device_config(sending_ips='all'),
                    kentik=FakeKentik(), monkeypatch=monkeypatch)
    lon = [e for e in plan.device_entries if e.device.name == 'lon-rtr-01'][0]

    assert lon.device.sending_ips == ('10.1.0.1', '10.1.4.1')


def test_existing_device_is_reported_not_touched(monkeypatch):
    kentik = FakeKentik(devices=[{'id': '55', 'device_name': 'lon_rtr_01',
                                  'site_id': 9, 'sending_ips': ['10.9.9.9']}])
    plan, _ = build(kentik=kentik, monkeypatch=monkeypatch)
    lon = [e for e in plan.device_entries if e.device.name == 'lon-rtr-01'][0]

    assert lon.action == ACTION_EXISTS
    assert lon.kentik_id == '55'
    assert set(lon.mismatch) == {'site_id', 'sending_ips'}


def test_update_existing_opts_into_an_update(monkeypatch):
    kentik = FakeKentik(devices=[{'id': '55', 'device_name': 'lon_rtr_01',
                                  'site_id': 9, 'sending_ips': ['10.9.9.9']}])
    plan, _ = build(config=device_config(update_existing=True), kentik=kentik,
                    monkeypatch=monkeypatch)
    lon = [e for e in plan.device_entries if e.device.name == 'lon-rtr-01'][0]

    assert lon.action == ACTION_UPDATE


def test_excluded_devices_are_marked_and_not_counted(monkeypatch):
    plan, _ = build(config=device_config(exclude=('nyc-fw-01',)),
                    kentik=FakeKentik(), monkeypatch=monkeypatch)
    nyc = [e for e in plan.device_entries if e.device.name == 'nyc-fw-01'][0]

    assert nyc.excluded is True
    assert 'nyc-fw-01' in nyc.exclude_reason
    assert plan.stats()['excluded_devices'] == 1
    assert plan.stats()['device_actions'][ACTION_CREATE] == 1


def test_missing_site_in_kentik_is_warned(monkeypatch):
    plan, _ = build(kentik=FakeKentik(), monkeypatch=monkeypatch)
    categories = [w.category for w in plan.warnings]

    assert 'sites_not_in_kentik' in categories
    nyc = [e for e in plan.device_entries if e.device.name == 'nyc-fw-01'][0]
    assert nyc.site_id == ''


def test_capacity_warning_and_hard_stop(monkeypatch):
    kentik = FakeKentik(plans=[{'id': 7, 'name': 'Free_Flow', 'active': True,
                                'max_devices': 1, 'devices': [{'id': 'x'}]}])
    plan, config = build(kentik=kentik, monkeypatch=monkeypatch)

    assert plan.capacity['remaining'] == 0
    assert 'plan_capacity' in [w.category for w in plan.warnings]

    problems = device_apply_problems(config, plan)
    assert problems and 'exceeds' in problems[0]

    allowed = replace(config, device=replace(config.device,
                                             allow_over_capacity=True))
    assert device_apply_problems(allowed, plan) == []


def test_nms_without_an_agent_is_blocked(monkeypatch):
    plan, config = build(config=device_config(mode='nms'), kentik=FakeKentik(),
                         monkeypatch=monkeypatch)

    assert 'nms_agent' in [w.category for w in plan.warnings]
    assert device_apply_problems(config, plan) == ['NMS mode needs an agent to be selected']

    with_agent = replace(config, device=replace(config.device, agent_id='agent-1'))
    assert device_apply_problems(with_agent, plan) == []


def test_apply_device_plan_creates_skips_and_excludes(monkeypatch, tmp_path):
    kentik = FakeKentik(devices=[{'id': '55', 'device_name': 'nyc_fw_01',
                                  'site_id': 1, 'sending_ips': ['10.2.0.1']}])
    plan, config = build(config=device_config(exclude=()), kentik=kentik,
                         monkeypatch=monkeypatch)
    config = replace(config, state_file=str(tmp_path / 'state.json'))
    plan.device_entries[1].excluded = True
    plan.device_entries[1].exclude_reason = 'excluded by test'

    events = []
    results = apply_device_plan(config, plan, kentik, on_event=events.append)

    assert results['created'] == ['lon-rtr-01']
    assert results['excluded'] == ['nyc-fw-01']
    assert kentik.created == [('lon-rtr-01', '42', ('10.1.0.1',))]
    assert [e['type'] for e in events][0] == 'start'
    assert events[-1]['created'] == 1
    statuses = {e['device']: e['status'] for e in events if e['type'] == 'device'}
    assert statuses == {'lon-rtr-01': 'created', 'nyc-fw-01': 'excluded'}


def test_apply_device_plan_records_failures(monkeypatch, tmp_path):
    kentik = FakeKentik()
    kentik.fail.add('lon-rtr-01')
    plan, config = build(kentik=kentik, monkeypatch=monkeypatch)
    config = replace(config, state_file=str(tmp_path / 'state.json'))

    results = apply_device_plan(config, plan, kentik)

    assert results['failed'] == ['lon-rtr-01']
    assert 'already in use' in results['errors']['lon-rtr-01']


def test_apply_device_plan_updates_when_asked(monkeypatch, tmp_path):
    kentik = FakeKentik(devices=[{'id': '55', 'device_name': 'lon_rtr_01',
                                  'site_id': 9, 'sending_ips': ['10.9.9.9']}])
    plan, config = build(config=device_config(update_existing=True), kentik=kentik,
                         monkeypatch=monkeypatch)
    config = replace(config, state_file=str(tmp_path / 'state.json'))

    results = apply_device_plan(config, plan, kentik)

    assert results['updated'] == ['lon-rtr-01']
    assert kentik.updated == [('lon-rtr-01', '42')]


def test_device_fingerprint_covers_exclusions_and_mode(monkeypatch):
    from ibx_kentik_prepop.plan import plan_fingerprint
    plan, _ = build(kentik=FakeKentik(), monkeypatch=monkeypatch)
    baseline = plan_fingerprint(plan)

    plan.device_entries[0].excluded = True
    assert plan_fingerprint(plan) != baseline

    plan.device_entries[0].excluded = False
    assert plan_fingerprint(plan) == baseline

    plan.device_mode = 'nms'
    assert plan_fingerprint(plan) != baseline


def test_manual_plan_id_used_when_the_api_lists_nothing(monkeypatch):
    kentik = FakeKentik(plans=[])
    plan, config = build(config=device_config(plan_id=4242), kentik=kentik,
                         monkeypatch=monkeypatch)
    categories = [w.category for w in plan.warnings]

    assert plan.plan_id == 4242
    assert plan.capacity['remaining'] is None
    assert 'plan_unavailable' in categories
    assert device_apply_problems(config, plan) == []


def test_no_plan_and_no_manual_id_blocks_the_apply(monkeypatch):
    plan, config = build(kentik=FakeKentik(plans=[]), monkeypatch=monkeypatch)

    assert plan.plan_id == 0
    assert 'plan_unavailable' in [w.category for w in plan.warnings]
    assert 'enter the plan id manually' in device_apply_problems(config, plan)[0]


def test_free_plan_is_preferred_over_the_first_active(monkeypatch):
    kentik = FakeKentik(plans=[
        {'id': 3, 'name': 'Enterprise', 'active': True, 'max_devices': 50,
         'devices': []},
        {'id': 4, 'name': 'Free Flow', 'active': True, 'max_devices': 5,
         'devices': []}])
    plan, _ = build(config=device_config(plan_name='Nonexistent'), kentik=kentik,
                    monkeypatch=monkeypatch)

    assert plan.plan_id == 4

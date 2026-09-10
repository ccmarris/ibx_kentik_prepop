#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for the Kentik payload construction and device write refusal
'''

import pytest
from conftest import make_config, make_site
from ibx_kentik_prepop.model import Device, SiteSubnet
from ibx_kentik_prepop.targets.kentik import KENTIK


def target():
    return KENTIK(make_config())


def test_site_payload_maps_the_three_buckets():
    site = make_site('LON-DC1')
    networks = {'infrastructure': ['10.1.1.0/30'],
                'user_access': ['10.1.0.0/24'],
                'other': []}
    body = target().site_payload(site, networks)['site']

    assert body['title'] == 'LON-DC1'
    assert body['type'] == 'SITE_TYPE_BRANCH'
    assert body['addressClassification'] == {
        'infrastructureNetworks': ['10.1.1.0/30'],
        'userAccessNetworks': ['10.1.0.0/24'],
        'otherNetworks': [],
    }


def test_site_payload_preserves_unmanaged_existing_fields():
    site = make_site('LON-DC1')
    raw = {'id': '42', 'siteMarket': 'EMEA', 'lat': 51.5, 'lon': -0.1,
           'postalAddress': {'city': 'London'}}
    body = target().site_payload(site, {'user_access': ['10.1.0.0/24']}, raw)['site']

    assert body['siteMarket'] == 'EMEA'
    assert body['lat'] == 51.5
    assert body['postalAddress'] == {'city': 'London'}


def test_existing_networks_reads_the_classification():
    raw = {'addressClassification': {'userAccessNetworks': ['10.1.0.0/24'],
                                     'infrastructureNetworks': None}}
    networks = KENTIK.existing_networks(raw)

    assert networks['user_access'] == ['10.1.0.0/24']
    assert networks['infrastructure'] == []
    assert networks['other'] == []


def test_device_payload_sanitises_the_name_and_omits_plan_id():
    device = Device(name='lon-rtr-01', mgmt_ip='10.1.0.1', role='router',
                    vendor='Cisco', model='ISR4451', sending_ips=('10.1.0.1',))
    body = target().device_payload(device, site_id='42')['device']

    assert body['device_name'] == 'lon_rtr_01'
    assert body['device_subtype'] == 'router'
    assert body['sending_ips'] == ['10.1.0.1']
    assert body['site_id'] == 42
    assert body['device_snmp_ip'] == '10.1.0.1'
    assert 'plan_id' not in body


def test_nms_payload_carries_the_agent_and_credential():
    from dataclasses import replace
    from conftest import make_config as base_config
    config = base_config()
    config = replace(config, device=replace(config.device, mode='nms',
                                            agent_id='agent-1',
                                            credential_name='snmp-ro',
                                            monitoring_template_id=7))
    device = Device(name='lon-sw-01', mgmt_ip='10.1.0.2', role='switch')
    body = KENTIK(config).device_payload(device, site_id='42')['device']

    assert body['nms'] == {'ip_address': '10.1.0.2', 'agent_id': 'agent-1',
                           'snmp': {'credential_name': 'snmp-ro', 'port': 161}}
    assert body['monitoring_template_id'] == 7
    assert 'sending_ips' not in body
    assert 'plan_id' not in body


def test_sending_ips_policy_and_per_device_override():
    from dataclasses import replace
    from conftest import make_config as base_config
    from ibx_kentik_prepop.targets.kentik import sending_ips_for

    device = Device(name='lon-rtr-01', mgmt_ip='10.1.0.1',
                    interfaces=[{'address': '10.1.0.1'},
                                {'address': '10.1.4.1'}])
    config = base_config()

    assert sending_ips_for(config, device) == ['10.1.0.1']

    every = replace(config, device=replace(config.device, sending_ips='all'))
    assert sending_ips_for(every, device) == ['10.1.0.1', '10.1.4.1']

    chosen = replace(config, device=replace(config.device,
                                            sending_ip_map={'lon-rtr-01': ['10.1.4.1']}))
    assert sending_ips_for(chosen, device) == ['10.1.4.1']


def test_resolve_plan_matches_free_flow_loosely():
    plans = [{'id': 1, 'name': 'Free Flow', 'active': True, 'max_devices': 5,
              'devices': [{'id': 'a'}, {'id': 'b'}]},
             {'id': 2, 'name': 'Enterprise', 'active': True, 'max_devices': 100,
              'devices': []}]
    kentik = target()

    capacity, warning = kentik.resolve_plan('Free_Flow', plans=plans)
    assert (capacity['id'], capacity['remaining'], warning) == (1, 3, '')

    capacity, warning = kentik.resolve_plan('free flow', plans=plans)
    assert capacity['id'] == 1

    capacity, warning = kentik.resolve_plan(plan_id=2, plans=plans)
    assert capacity['id'] == 2


def test_resolve_plan_falls_back_and_says_so():
    plans = [{'id': 9, 'name': 'Enterprise', 'active': True, 'max_devices': 10,
              'devices': []}]
    capacity, warning = target().resolve_plan('Free_Flow', plans=plans)

    assert capacity['id'] == 9
    assert 'falling back' in warning


def test_update_device_placement_preserves_unknown_fields(monkeypatch):
    kentik = target()
    sent = {}

    def fake_request(method, url, body=None):
        sent.update({'method': method, 'url': url, 'body': body})
        return {'device': {'id': '55'}}

    monkeypatch.setattr(kentik, '_request', fake_request)
    raw = {'id': '55', 'device_name': 'lon_rtr_01', 'device_subtype': 'router',
           'plan_id': 3, 'device_snmp_community': 'keep-me',
           'sending_ips': ['10.9.9.9'], 'site_id': 1,
           'created_date': '2026-01-01', 'site': {'id': 1}}
    device = Device(name='lon-rtr-01', mgmt_ip='10.1.0.1')

    kentik.update_device_placement(raw, device, site_id='42')
    body = sent['body']['device']

    assert sent['method'] == 'PUT'
    assert sent['url'].endswith('/device/v202504beta2/device/55')
    assert body['device_snmp_community'] == 'keep-me'
    assert body['sending_ips'] == ['10.1.0.1']
    assert body['site_id'] == 42
    assert 'created_date' not in body and 'site' not in body


def test_default_plan_name_is_the_free_flowpak_plan():
    from conftest import make_config as base_config
    assert base_config().device.plan_name == 'Free Flowpak Plan'


def test_resolve_plan_matches_the_flowpak_plan_however_it_is_written():
    plans = [{'id': 11, 'name': 'Free Flowpak Plan', 'active': True,
              'max_devices': 5, 'devices': []}]
    kentik = target()

    for spelling in ('Free Flowpak Plan', 'free flowpak plan',
                     'Free_Flowpak_Plan'):
        capacity, warning = kentik.resolve_plan(spelling, plans=plans)
        assert (capacity['id'], warning) == (11, '')


def test_resolve_plan_accepts_a_near_miss_name_and_says_so():
    plans = [{'id': 12, 'name': 'Free Flowpak', 'active': True,
              'max_devices': 5, 'devices': []},
             {'id': 13, 'name': 'Enterprise', 'active': True,
              'max_devices': 50, 'devices': []}]
    capacity, warning = target().resolve_plan('Free Flowpak Plan', plans=plans)

    assert capacity['id'] == 12
    assert 'closest match' in warning


def test_resolve_plan_prefers_a_free_plan_when_the_name_is_absent():
    plans = [{'id': 3, 'name': 'Enterprise', 'active': True, 'max_devices': 50,
              'devices': []},
             {'id': 4, 'name': 'Free Trial Flowpak', 'active': True,
              'max_devices': 2, 'devices': []}]
    capacity, warning = target().resolve_plan('Free Flowpak Plan', plans=plans)

    assert capacity['id'] == 4
    assert 'free plan' in warning


def test_resolve_plan_honours_an_id_the_api_does_not_list():
    capacity, warning = target().resolve_plan(plan_id=99, plans=[])

    assert capacity['id'] == 99
    assert capacity['remaining'] is None
    assert 'entered manually' in warning


def test_resolve_plan_with_no_plans_and_no_id():
    capacity, warning = target().resolve_plan('Free_Flow', plans=[])

    assert capacity is None
    assert 'no active plan' in warning


def test_device_payload_always_carries_the_required_bgp_type():
    device = Device(name='lon-rtr-01', mgmt_ip='10.1.0.1', role='router')
    body = target().device_payload(device)['device']

    assert body['device_bgp_type'] == 'none'
    assert body['device_bgp_flowspec'] is False
    assert 'device_bgp_neighbor_asn' not in body


def test_nms_payload_also_carries_the_bgp_type():
    from dataclasses import replace
    from conftest import make_config as base_config
    config = base_config()
    config = replace(config, device=replace(config.device, mode='nms',
                                            agent_id='agent-1'))
    body = KENTIK(config).device_payload(Device(name='lon-sw-01'))['device']

    assert body['device_bgp_type'] == 'none'


def test_bgp_flowspec_is_selectable():
    from dataclasses import replace
    from conftest import make_config as base_config
    config = base_config()
    config = replace(config, device=replace(config.device, bgp_flowspec=True))
    body = KENTIK(config).device_payload(Device(name='lon-rtr-01'))['device']

    assert body['device_bgp_flowspec'] is True


def test_device_payload_includes_the_bgp_peering_details():
    from dataclasses import replace
    from conftest import make_config as base_config
    config = base_config()
    config = replace(config, device=replace(config.device, bgp_type='device',
                                            bgp_neighbor_asn='65001',
                                            bgp_neighbor_ip='10.0.0.1'))
    body = KENTIK(config).device_payload(Device(name='lon-rtr-01'))['device']

    assert body['device_bgp_type'] == 'device'
    assert body['device_bgp_neighbor_asn'] == '65001'
    assert body['device_bgp_neighbor_ip'] == '10.0.0.1'


def test_device_payload_shares_another_devices_bgp_table():
    from dataclasses import replace
    from conftest import make_config as base_config
    config = base_config()
    config = replace(config, device=replace(config.device,
                                            bgp_type='other_device',
                                            bgp_device_id='500'))
    body = KENTIK(config).device_payload(Device(name='lon-sw-01'))['device']

    assert body['use_bgp_device_id'] == '500'


def test_sample_rate_defaults_to_one_and_is_configurable():
    from dataclasses import replace
    from conftest import make_config as base_config

    device = Device(name='lon-rtr-01', mgmt_ip='10.1.0.1', role='router')
    assert target().device_payload(device)['device']['device_sample_rate'] == 1

    config = base_config()
    config = replace(config, device=replace(config.device, sample_rate=1000))
    body = KENTIK(config).device_payload(device)['device']
    assert body['device_sample_rate'] == 1000

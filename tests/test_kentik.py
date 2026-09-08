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
    assert body['site_id'] == '42'
    assert body['device_snmp_ip'] == '10.1.0.1'
    assert 'plan_id' not in body


def test_create_device_is_refused():
    with pytest.raises(NotImplementedError):
        target().create_device(Device(name='lon-rtr-01'))

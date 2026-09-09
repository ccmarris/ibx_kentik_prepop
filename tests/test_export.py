#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for the Kentik import artefact export
'''

import csv
import io
import json
import pytest
from conftest import make_config, make_site
from ibx_kentik_prepop import export
from ibx_kentik_prepop.model import (ACTION_CREATE, ACTION_NO_CHANGE,
                                     ACTION_UPDATE, Device, Plan, SitePlan)


def sample_plan(with_devices=True):
    new_site = make_site('NYC-BR2', ('10.2.0.0/23',))
    old_site = make_site('LON-DC1', ('10.1.0.0/24',))
    same_site = make_site('SFO-BR9', ('10.9.0.0/24',))

    plan = Plan(source='uddi', site_key='Site', generated='2026-09-08T00:00:00+00:00')
    plan.entries = [
        SitePlan(site=new_site, action=ACTION_CREATE,
                 merged={'user_access': ['10.2.0.0/23']}),
        SitePlan(site=old_site, action=ACTION_UPDATE, kentik_id='42',
                 merged={'user_access': ['10.1.0.0/24'],
                         'infrastructure': ['10.1.4.0/30']},
                 raw_site={'id': '42', 'siteMarket': 'EMEA'}),
        SitePlan(site=same_site, action=ACTION_NO_CHANGE, kentik_id='43',
                 merged={'user_access': ['10.9.0.0/24']}),
    ]
    if with_devices:
        plan.devices = [
            Device(name='lon-rtr-01', mgmt_ip='10.1.0.1', role='router',
                   vendor='Cisco', model='ISR4451', site_name='LON-DC1',
                   sending_ips=('10.1.0.1',), origin='network_insight'),
            Device(name='nyc-fw-01', mgmt_ip='10.2.0.1', role='firewall',
                   site_name='NYC-BR2', sending_ips=('10.2.0.1',), origin='uai'),
        ]
    return plan


def rows_of(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_site_requests_use_post_for_new_and_put_for_existing():
    requests = export.site_requests(sample_plan())
    methods = {r['body']['site']['title']: (r['method'], r['path']) for r in requests}

    assert methods['NYC-BR2'] == ('POST', '/site/v202211/sites')
    assert methods['LON-DC1'] == ('PUT', '/site/v202211/sites/42')
    assert 'SFO-BR9' not in methods


def test_site_requests_can_include_unchanged():
    requests = export.site_requests(sample_plan(), include_unchanged=True)
    titles = [r['body']['site']['title'] for r in requests]
    assert sorted(titles) == ['LON-DC1', 'NYC-BR2', 'SFO-BR9']


def test_site_json_carries_the_classification_and_preserved_fields():
    text = export.render(sample_plan(), make_config(), 'sites-json')
    requests = json.loads(text)
    lon = [r for r in requests if r['body']['site']['title'] == 'LON-DC1'][0]['body']['site']

    assert lon['addressClassification']['userAccessNetworks'] == ['10.1.0.0/24']
    assert lon['addressClassification']['infrastructureNetworks'] == ['10.1.4.0/30']
    assert lon['siteMarket'] == 'EMEA'


def test_sites_csv_columns_and_values():
    text = export.render(sample_plan(), make_config(), 'sites-csv')
    rows = rows_of(text)

    assert list(rows[0]) == list(export.SITE_CSV_HEADERS)
    lon = [r for r in rows if r['title'] == 'LON-DC1'][0]
    assert lon['type'] == 'SITE_TYPE_BRANCH'
    assert lon['kentik_id'] == '42'
    assert lon['user_access_networks'] == '10.1.0.0/24'
    assert lon['infrastructure_networks'] == '10.1.4.0/30'


def test_device_requests_fill_site_id_only_when_known():
    requests = export.device_requests(sample_plan(), make_config())
    by_name = {r['body']['device']['device_name']: r['body']['device']
               for r in requests}

    assert by_name['lon_rtr_01']['site_id'] == 42
    assert 'site_id' not in by_name['nyc_fw_01']
    assert all(r['path'] == '/device/v202504beta2/device' for r in requests)


def test_add_device_csv_matches_the_kentik_loader_columns():
    text = export.render(sample_plan(), make_config(), 'devices-add-csv')
    rows = rows_of(text)

    assert list(rows[0]) == list(export.ADD_DEVICE_CSV_HEADERS)
    lon = [r for r in rows if r['devicename'] == 'lon_rtr_01'][0]
    assert lon['siteid'] == '42'
    assert lon['sendingips'] == '10.1.0.1'
    assert lon['devicedescription'] == 'Cisco ISR4451'
    assert lon['devicesamplerate'] == '1024'


def test_nms_device_csv_columns():
    text = export.render(sample_plan(), make_config(), 'devices-nms-csv')
    rows = rows_of(text)

    assert list(rows[0]) == ['name', 'address', 'agent_id']
    assert rows[0]['name'] == 'lon_rtr_01'
    assert rows[0]['address'] == '10.1.0.1'
    assert rows[0]['agent_id'] == ''


def test_render_rejects_an_unknown_format():
    with pytest.raises(ValueError):
        export.render(sample_plan(), make_config(), 'nonsense')


def test_export_writes_the_artefacts_for_flow_mode(tmp_path):
    written = export.export(sample_plan(), make_config(),
                            str(tmp_path / 'sub' / 'tenant-a'))
    names = sorted(p.name for p in (tmp_path / 'sub').iterdir())

    assert names == ['tenant-a-devices-add.csv', 'tenant-a-devices.json',
                     'tenant-a-sites.csv', 'tenant-a-sites.json']
    assert len(written) == 4
    assert all(note for _, note in written)


def test_export_swaps_the_device_csv_in_nms_mode(tmp_path):
    plan = sample_plan()
    plan.device_mode = 'nms'
    export.export(plan, make_config(), str(tmp_path / 'tenant-nms'))
    names = sorted(p.name for p in tmp_path.iterdir())

    assert 'tenant-nms-devices-nms.csv' in names
    assert 'tenant-nms-devices-add.csv' not in names


def test_export_omits_excluded_devices(tmp_path):
    from ibx_kentik_prepop.model import DevicePlan
    plan = sample_plan()
    plan.device_entries = [
        DevicePlan(device=plan.devices[0], action='create', site_id='42'),
        DevicePlan(device=plan.devices[1], action='create', excluded=True,
                   exclude_reason="excluded by 'nyc-fw-01'"),
    ]
    text = export.render(plan, make_config(), 'devices-json')

    assert 'lon_rtr_01' in text
    assert 'nyc_fw_01' not in text


def test_device_task_export_has_no_site_artefacts():
    from ibx_kentik_prepop.model import DevicePlan, TASK_DEVICES
    plan = sample_plan()
    plan.task = TASK_DEVICES
    plan.device_entries = [DevicePlan(device=plan.devices[0], action='create')]

    assert export.applicable_formats(plan) == ['devices-json', 'devices-add-csv']


def test_export_skips_device_files_without_devices(tmp_path):
    written = export.export(sample_plan(with_devices=False), make_config(),
                            str(tmp_path / 'tenant-b'))
    names = sorted(p.name for p in tmp_path.iterdir())

    assert names == ['tenant-b-sites.csv', 'tenant-b-sites.json']
    assert len(written) == 2


def test_export_strips_a_supplied_suffix(tmp_path):
    export.export(sample_plan(with_devices=False), make_config(),
                  str(tmp_path / 'tenant-c.csv'))
    assert (tmp_path / 'tenant-c-sites.csv').is_file()


def test_applicable_formats_skips_empty_artefacts():
    plan = sample_plan()
    for entry in plan.entries:
        entry.action = ACTION_NO_CHANGE

    assert export.applicable_formats(plan) == ['devices-json', 'devices-add-csv']
    assert 'sites-json' in export.applicable_formats(plan, include_unchanged=True)

    plan.devices = []
    assert export.applicable_formats(plan) == []


def test_export_writes_nothing_when_there_is_nothing_to_do(tmp_path):
    plan = sample_plan(with_devices=False)
    for entry in plan.entries:
        entry.action = ACTION_NO_CHANGE

    written = export.export(plan, make_config(),
                            str(tmp_path / 'sub' / 'tenant-d'))

    assert written == []
    assert list(tmp_path.iterdir()) == []


def test_suffix_for_each_format():
    assert export.suffix_for('sites-json') == '-sites.json'
    assert export.suffix_for('devices-add-csv') == '-devices-add.csv'

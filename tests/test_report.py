#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for the report renderers
'''

import json
from conftest import make_site
from ibx_kentik_prepop import report
from ibx_kentik_prepop.model import ACTION_CREATE, Device, Plan, SitePlan


def sample_plan():
    site = make_site('LON-DC1', ('10.1.0.0/24',))
    site.devices = [Device(name='lon-rtr-01', role='router', mgmt_ip='10.1.0.1',
                           site_name='LON-DC1', origin='network_insight')]
    plan = Plan(source='uddi', site_key='Site', generated='2026-09-08T00:00:00+00:00')
    plan.entries = [SitePlan(site=site, action=ACTION_CREATE,
                             added={'user_access': ['10.1.0.0/24']},
                             merged={'user_access': ['10.1.0.0/24']})]
    plan.devices = list(site.devices)
    plan.add_warning('unattributed_subnets', '1 subnet has no site value', '10.9.0.0/24')
    return plan


def test_render_table_includes_all_sections():
    text = report.render(sample_plan(), 'table')

    assert 'DRY RUN' in text
    assert '== Sites ==' in text
    assert '== Subnets ==' in text
    assert '== Devices' in text
    assert '== Warnings ==' in text
    assert 'LON-DC1' in text
    assert '10.1.0.0/24' in text


def test_render_table_labels_apply_mode():
    assert 'APPLYING' in report.render(sample_plan(), 'table', dry_run=False)


def test_render_json_round_trips():
    data = json.loads(report.render(sample_plan(), 'json'))

    assert data['source'] == 'uddi'
    assert data['stats']['sites'] == 1
    assert data['sites'][0]['action'] == ACTION_CREATE
    assert data['sites'][0]['subnets'][0]['cidr'] == '10.1.0.0/24'
    assert data['devices'][0]['name'] == 'lon-rtr-01'
    assert data['warnings'][0]['category'] == 'unattributed_subnets'


def test_render_table_for_the_device_task():
    from ibx_kentik_prepop.model import DevicePlan, TASK_DEVICES
    plan = sample_plan()
    plan.task = TASK_DEVICES
    plan.device_mode = 'flow'
    plan.plan_name = 'Free_Flow'
    plan.plan_id = 7
    plan.capacity = {'remaining': 3, 'max_devices': 5}
    plan.device_entries = [
        DevicePlan(device=plan.devices[0], action='create', site_id='42'),
    ]
    text = report.render(plan, 'table')

    assert 'plan - devices' in text
    assert 'Free_Flow (id 7) (3 of 5 slot(s) left)' in text
    assert '== Sites ==' not in text
    assert '== Devices ==' in text


def test_render_csv_to_stdout_has_a_section_per_block():
    text = report.render(sample_plan(), 'csv')

    assert '# sites' in text
    assert '# subnets' in text
    assert '# devices' in text
    assert 'lon_rtr_01' in text


def test_render_csv_writes_one_file_per_section(tmp_path):
    outfile = tmp_path / 'plan.csv'
    written = report.render(sample_plan(), 'csv', str(outfile))
    names = sorted(p.name for p in tmp_path.iterdir())

    assert names == ['plan-devices.csv', 'plan-sites.csv', 'plan-subnets.csv',
                     'plan-warnings.csv']
    assert 'plan-sites.csv' in written


def test_render_table_writes_the_outfile(tmp_path):
    outfile = tmp_path / 'plan.txt'
    report.render(sample_plan(), 'table', str(outfile))
    assert 'LON-DC1' in outfile.read_text()

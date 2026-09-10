#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for the web interface, in particular the credentials ini override
'''

import json
import pytest
from ibx_kentik_prepop.web import server


VALID_INI = '''[UDDI]
api_key = test-key
base_url = https://csp.infoblox.com

[KENTIK]
email = tester@example.com
token = test-token
'''


@pytest.fixture
def ini_file(tmp_path):
    path = tmp_path / 'tenant-a.ini'
    path.write_text(VALID_INI, encoding='utf-8')
    return path


@pytest.fixture
def client(monkeypatch, ini_file):
    monkeypatch.setattr(server, 'CONFIG_FILE', str(ini_file))
    monkeypatch.setattr(server, 'YAML_FILE', '')
    monkeypatch.setattr(server, 'LOCK_CONFIG', False)
    monkeypatch.setattr(server, 'EXTRA_INI_DIRS', (ini_file.parent,))
    server.app.config.update(TESTING=True)
    return server.app.test_client()


def test_resolve_defaults_to_the_startup_file(client, ini_file):
    path, error = server.resolve_config_file('')
    assert path == str(ini_file)
    assert error == ''


def test_resolve_accepts_another_usable_ini(client, tmp_path):
    other = tmp_path / 'tenant-b.ini'
    other.write_text(VALID_INI, encoding='utf-8')
    path, error = server.resolve_config_file(str(other))

    assert path == str(other.resolve())
    assert error == ''


def test_resolve_expands_the_user_home(client, monkeypatch, tmp_path):
    home_ini = tmp_path / 'home.ini'
    home_ini.write_text(VALID_INI, encoding='utf-8')
    monkeypatch.setenv('HOME', str(tmp_path))
    path, error = server.resolve_config_file('~/home.ini')

    assert path == str(home_ini.resolve())
    assert error == ''


def test_resolve_rejects_a_missing_file(client, tmp_path):
    path, error = server.resolve_config_file(str(tmp_path / 'nope.ini'))
    assert path == server.CONFIG_FILE
    assert 'not a readable file' in error


def test_resolve_rejects_a_file_without_our_sections(client, tmp_path):
    other = tmp_path / 'unrelated.ini'
    other.write_text('[SOMETHING]\nkey = value\n', encoding='utf-8')
    path, error = server.resolve_config_file(str(other))

    assert path == server.CONFIG_FILE
    assert 'no usable' in error


def test_resolve_refused_when_locked(client, monkeypatch, tmp_path):
    other = tmp_path / 'tenant-b.ini'
    other.write_text(VALID_INI, encoding='utf-8')
    monkeypatch.setattr(server, 'LOCK_CONFIG', True)
    path, error = server.resolve_config_file(str(other))

    assert path == server.CONFIG_FILE
    assert 'lock-config' in error


def test_config_endpoint_reports_the_chosen_file(client, tmp_path):
    other = tmp_path / 'tenant-b.ini'
    other.write_text('[NIOS]\ngm = 10.0.0.1\nuser = admin\npass = secret\n',
                     encoding='utf-8')
    payload = client.get('/api/config',
                         query_string={'config_file': str(other)}).get_json()

    assert payload['ini_file'] == str(other.resolve())
    assert payload['nios']['credentials'] is True
    assert payload['uddi']['credentials'] is False
    assert payload['lock_config'] is False


def test_config_endpoint_rejects_a_bad_override(client):
    response = client.get('/api/config', query_string={'config_file': '/nope.ini'})
    assert response.status_code == 400
    assert 'not a readable file' in response.get_json()['error']


def test_inis_endpoint_lists_candidates_without_values(client, ini_file):
    payload = client.get('/api/inis').get_json()
    paths = [c['path'] for c in payload['candidates']]

    assert str(ini_file.resolve()) in paths
    assert payload['default'] == str(ini_file)
    for candidate in payload['candidates']:
        assert set(candidate) == {'path', 'name', 'sections'}
    assert 'test-key' not in str(payload)


def test_export_endpoint_rejects_a_bad_override(client):
    response = client.post('/api/export', json={'site_key': 'Site',
                                                'config_file': '/nope.ini'})
    assert response.status_code == 400


def test_export_endpoint_requires_a_site_key(client):
    response = client.post('/api/export', json={'source': 'uddi'})
    assert response.status_code == 400
    assert 'site EA/tag' in response.get_json()['error']


def test_export_endpoint_returns_named_artefacts(client, monkeypatch):
    from ibx_kentik_prepop.web import server as web
    from ibx_kentik_prepop.model import ACTION_CREATE, Plan, SitePlan
    from conftest import make_site

    plan = Plan(source='uddi', site_key='Site')
    plan.entries = [SitePlan(site=make_site('LON-DC1', ('10.1.0.0/24',)),
                             action=ACTION_CREATE,
                             merged={'user_access': ['10.1.0.0/24']})]
    monkeypatch.setattr(web, 'build_plan', lambda config, kentik: plan)

    payload = client.post('/api/export', json={'source': 'uddi',
                                               'site_key': 'Site',
                                               'export_prefix': 'tenant-a'}).get_json()
    names = [f['filename'] for f in payload['files']]

    assert names == ['tenant-a-sites.json', 'tenant-a-sites.csv']
    assert '10.1.0.0/24' in payload['files'][0]['content']
    assert payload['stats']['sites'] == 1


def _plan_with(entries):
    from ibx_kentik_prepop.model import Plan
    plan = Plan(source='uddi', site_key='Site')
    plan.entries = entries
    return plan


def _one_site_plan():
    from ibx_kentik_prepop.model import ACTION_CREATE, SitePlan
    from conftest import make_site
    return _plan_with([SitePlan(site=make_site('LON-DC1', ('10.1.0.0/24',)),
                                action=ACTION_CREATE,
                                added={'user_access': ['10.1.0.0/24']},
                                merged={'user_access': ['10.1.0.0/24']})])


def test_agent_summary_reads_the_name_from_the_config(client):
    from ibx_kentik_prepop.web.server import agent_summary

    nested = agent_summary({'id': 'a1', 'running': True,
                            'config': {'name': 'lon-collector',
                                       'site_id': '42'}})
    assert nested == {'id': 'a1', 'name': 'lon-collector', 'status': 'running',
                      'site_id': '42'}

    stopped = agent_summary({'id': 'a2', 'running': False, 'config': {}})
    assert stopped['status'] == 'stopped'
    assert stopped['name'] == ''

    flat = agent_summary({'id': 'a3', 'name': 'legacy', 'status': 'ACTIVE'})
    assert (flat['name'], flat['status']) == ('legacy', 'ACTIVE')

    described = agent_summary({'id': 'a4',
                               'config': {'description': 'spare collector'}})
    assert described['name'] == 'spare collector'


def test_nms_endpoint_flattens_agents(client, monkeypatch):
    from ibx_kentik_prepop.web import server as web

    class FakeKentik:
        last_error = {}

        def __init__(self, config):
            pass

        def list_agents(self):
            return [{'id': 'a1', 'running': True,
                     'config': {'name': 'lon-collector'}}]

        def list_credentials(self):
            return [{'id': 'c1', 'name': 'snmp-ro'}]

        def error_text(self):
            return ''

    monkeypatch.setattr(web, 'KENTIK', FakeKentik)
    payload = client.get('/api/kentik-nms').get_json()

    assert payload['agents'][0]['name'] == 'lon-collector'
    assert payload['agents'][0]['id'] == 'a1'
    assert payload['credentials'][0]['name'] == 'snmp-ro'


def test_kentik_check_reports_missing_credentials(client, tmp_path):
    other = tmp_path / 'nios-only.ini'
    other.write_text('[NIOS]\ngm = 10.0.0.1\nuser = admin\npass = secret\n',
                     encoding='utf-8')
    payload = client.get('/api/kentik-check',
                         query_string={'config_file': str(other)}).get_json()

    assert payload['ok'] is False
    assert 'token' in payload['error'] or 'email' in payload['error']


def test_kentik_check_reports_a_successful_read(client, monkeypatch):
    from ibx_kentik_prepop.web import server as web

    class FakeKentik:
        last_error = {}

        def __init__(self, config):
            pass

        def get_sites(self):
            return [{'id': '1', 'title': 'LON-DC1'}]

        def error_text(self):
            return ''

    monkeypatch.setattr(web, 'KENTIK', FakeKentik)
    payload = client.get('/api/kentik-check').get_json()

    assert payload['ok'] is True
    assert payload['sites'] == 1


def test_apply_still_requires_confirmation(client):
    response = client.post('/api/apply', json={'site_key': 'Site'})
    assert response.status_code == 400
    assert 'confirm' in response.get_json()['error']


def test_apply_requires_the_reviewed_fingerprint(client):
    response = client.post('/api/apply', json={'confirm': True, 'site_key': 'Site'})
    assert response.status_code == 400
    assert 'dry run' in response.get_json()['error']


def test_apply_refuses_a_stale_fingerprint(client, monkeypatch):
    from ibx_kentik_prepop.web import server as web

    monkeypatch.setattr(web, 'build_plan', lambda config, kentik: _one_site_plan())
    monkeypatch.setattr(web, 'KENTIK', lambda config: object())

    response = client.post('/api/apply', json={'confirm': True,
                                               'source': 'uddi',
                                               'site_key': 'Site',
                                               'fingerprint': 'stale0000000000'})
    payload = response.get_json()

    assert response.status_code == 409
    assert 'changed since your dry run' in payload['error']
    assert payload['fingerprint'] != payload['expected']


def test_apply_streams_one_event_per_site(client, monkeypatch):
    from ibx_kentik_prepop.plan import plan_fingerprint
    from ibx_kentik_prepop.web import server as web

    plan = _one_site_plan()
    applied = {}

    def fake_apply(config, applied_plan, kentik, on_event=None):
        applied['plan'] = applied_plan
        on_event({'type': 'start', 'sites': 1, 'to_change': 1, 'stats': {}})
        on_event({'type': 'site', 'site': 'LON-DC1', 'action': 'create',
                  'status': 'created', 'kentik_id': '101'})
        on_event({'type': 'done', 'created': 1, 'updated': 0, 'unchanged': 0,
                  'failed': 0, 'errors': {}})
        return {'created': ['LON-DC1'], 'updated': [], 'unchanged': [],
                'failed': [], 'notes': [], 'errors': {}}

    monkeypatch.setattr(web, 'build_plan', lambda config, kentik: plan)
    monkeypatch.setattr(web, 'KENTIK', lambda config: object())
    monkeypatch.setattr(web, 'apply_plan', fake_apply)

    response = client.post('/api/apply', json={'confirm': True,
                                               'source': 'uddi',
                                               'site_key': 'Site',
                                               'fingerprint': plan_fingerprint(plan)})
    frames = [line for line in response.get_data(as_text=True).split('\n\n') if line]
    events = [json.loads(f.replace('data: ', '')) for f in frames]

    assert response.status_code == 200
    assert response.mimetype == 'text/event-stream'
    assert [e['type'] for e in events] == ['start', 'site', 'done']
    assert events[1]['status'] == 'created'
    assert applied['plan'] is plan


def test_apply_reports_a_worker_exception_as_an_event(client, monkeypatch):
    from ibx_kentik_prepop.plan import plan_fingerprint
    from ibx_kentik_prepop.web import server as web

    plan = _one_site_plan()

    def exploding_apply(config, applied_plan, kentik, on_event=None):
        raise RuntimeError('connection reset')

    monkeypatch.setattr(web, 'build_plan', lambda config, kentik: plan)
    monkeypatch.setattr(web, 'KENTIK', lambda config: object())
    monkeypatch.setattr(web, 'apply_plan', exploding_apply)

    response = client.post('/api/apply', json={'confirm': True,
                                               'source': 'uddi',
                                               'site_key': 'Site',
                                               'fingerprint': plan_fingerprint(plan)})
    text = response.get_data(as_text=True)

    assert '"type": "error"' in text
    assert 'connection reset' in text


def test_apply_rejects_a_bad_override_before_running(client):
    response = client.post('/api/apply', json={'confirm': True,
                                               'site_key': 'Site',
                                               'config_file': '/nope.ini'})
    assert response.status_code == 400

#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for the web interface, in particular the credentials ini override
'''

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


def test_apply_still_requires_confirmation(client):
    response = client.post('/api/apply', json={'site_key': 'Site'})
    assert response.status_code == 400
    assert 'confirm' in response.get_json()['error']


def test_apply_rejects_a_bad_override_before_running(client):
    response = client.post('/api/apply', json={'confirm': True,
                                               'site_key': 'Site',
                                               'config_file': '/nope.ini'})
    assert response.status_code == 400


def test_cli_command_uses_the_resolved_ini(client, tmp_path):
    command = server.cli_command({'source': 'uddi', 'site_key': 'Site',
                                  'devices': True},
                                 str(tmp_path / 'tenant-b.ini'), ['--go'])

    assert '-c' in command
    assert command[command.index('-c') + 1] == str(tmp_path / 'tenant-b.ini')
    assert '--site-key' in command and 'Site' in command
    assert '--devices' in command
    assert command[-1] == '--go'

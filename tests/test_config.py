#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for credential loading from the ini file
'''

from argparse import Namespace
from ibx_kentik_prepop.config import (build_config, read_ini,
                                      validate_kentik_credentials)


def write(tmp_path, text, name='creds.ini'):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return str(path)


def config_for(ini_file):
    args = Namespace(source='uddi', site_key='Site', class_key=None,
                     site_type_key=None, max_prefix_len=None, site_filter=None,
                     include_address_blocks=False,
                     devices=False, use_insight=False, use_uai=False,
                     use_gateways=False, network_view=None, ip_space=None,
                     gm=None)
    return build_config(args, ini_file=ini_file)


def test_sections_are_matched_case_insensitively(tmp_path):
    ini = write(tmp_path, '''[Kentik]
email = tester@example.com
token = secret

[Uddi]
api_key = uddi-key
''')
    creds = read_ini(ini)

    assert creds['KENTIK']['email'] == 'tester@example.com'
    assert creds['UDDI']['api_key'] == 'uddi-key'


def test_mixed_case_section_still_enables_kentik(tmp_path):
    ini = write(tmp_path, '''[Kentik]
email = tester@example.com
token = secret
''')
    config = config_for(ini)

    assert validate_kentik_credentials(config) == []
    assert config.kentik.email == 'tester@example.com'


def test_an_exact_case_section_wins_over_a_variant(tmp_path):
    ini = write(tmp_path, '''[kentik]
email = lower@example.com

[KENTIK]
email = exact@example.com
token = secret
''')
    assert config_for(ini).kentik.email == 'exact@example.com'


def test_kentik_credential_key_aliases(tmp_path):
    ini = write(tmp_path, '''[Kentik]
api_email = tester@example.com
api_token = secret
url = https://api.kentik.eu
''')
    config = config_for(ini)

    assert config.kentik.email == 'tester@example.com'
    assert config.kentik.token == 'secret'
    assert config.kentik.base_url == 'https://api.kentik.eu'


def test_uddi_and_nios_key_aliases(tmp_path):
    ini = write(tmp_path, '''[UDDI]
key = uddi-key
url = https://csp.eu.infoblox.com

[NIOS]
grid_master = 10.0.0.1
username = admin
password = secret
api_version = v2.12.3
''')
    config = config_for(ini)

    assert config.uddi.api_key == 'uddi-key'
    assert config.uddi.base_url == 'https://csp.eu.infoblox.com'
    assert config.nios.gm == '10.0.0.1'
    assert config.nios.user == 'admin'
    assert config.nios.password == 'secret'
    assert config.nios.wapi_version == 'v2.12.3'


def test_quoted_values_are_stripped(tmp_path):
    ini = write(tmp_path, '''[KENTIK]
email = "tester@example.com"
token = 'secret'
''')
    config = config_for(ini)

    assert config.kentik.email == 'tester@example.com'
    assert config.kentik.token == 'secret'


def test_missing_file_is_not_fatal(tmp_path):
    config = config_for(str(tmp_path / 'nope.ini'))
    assert config.kentik.email == ''
    assert validate_kentik_credentials(config)

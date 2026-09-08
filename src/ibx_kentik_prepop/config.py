#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Configuration and credential handling for ibx_kentik_prepop

 Requirements:
   Python 3.10+

 Author: Chris Marrison

 Date Last Updated: 20260908

 Todo:

 Copyright (c) 2026 Chris Marrison / Infoblox

 Redistribution and use in source and binary forms,
 with or without modification, are permitted provided
 that the following conditions are met:

 1. Redistributions of source code must retain the above copyright
 notice, this list of conditions and the following disclaimer.

 2. Redistributions in binary form must reproduce the above copyright
 notice, this list of conditions and the following disclaimer in the
 documentation and/or other materials provided with the distribution.

 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
 ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 POSSIBILITY OF SUCH DAMAGE.

'''
__version__ = '0.1.0'
__author__ = 'Chris Marrison'
__author_email__ = 'chris@infoblox.com'
__license__ = 'BSD'

import configparser
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


DEFAULT_INI_FILE = 'ibx_kentik.ini'
DEFAULT_STATE_FILE = 'ibx_kentik_prepop_state.json'

INI_SECTIONS = ('NIOS', 'UDDI', 'KENTIK')

# NIOS WAPI defaults
DEFAULT_WAPI_VERSION = 'v2.13.1'
DEFAULT_NIOS_PAGE_SIZE = 1000

# Universal DDI (UDDI) defaults. Paths verified against csp.infoblox.com/apidoc
# and in production use by uddi_dns_sync.
DEFAULT_UDDI_BASE_URL = 'https://csp.infoblox.com'
DEFAULT_SUBNET_LIST = '/api/ddi/v1/ipam/subnet'
DEFAULT_ADDRESS_BLOCK_LIST = '/api/ddi/v1/ipam/address_block'
# Universal Asset Insights asset inventory search. VERIFIED shape: POST with a
# FilterEL 'filter', a 'fields' projection and cursor pagination returning
# {data: [...], pagination: {next_page_token}}.
DEFAULT_ASSET_SEARCH = '/api/v1/assets/search'
# VERIFY on a live tenant: 'compute' is the confirmed category for VMs. The
# category covering routers/switches/firewalls needs confirming per tenant.
DEFAULT_ASSET_CATEGORY = 'network'

DEFAULT_PAGE_TOKEN_PARAM = '_page_token'
DEFAULT_NEXT_TOKEN_KEY = 'next_page_token'
DEFAULT_PAGE_SIZE_PARAM = '_limit'
DEFAULT_TAG_FILTER_PARAM = '_tfilter'
DEFAULT_UDDI_PAGE_SIZE = 1000

# Kentik defaults. Sites use the Site API v202211 on the grpc host, devices use
# the v5 admin API. VERIFY the auth header names against the tenant's API docs
# on first run - everything else depends on them.
DEFAULT_KENTIK_BASE_URL = 'https://api.kentik.com'
DEFAULT_KENTIK_GRPC_BASE_URL = 'https://grpc.api.kentik.com'
DEFAULT_SITE_LIST = '/site/v202211/sites'
DEFAULT_SITE_CREATE = '/site/v202211/sites'
DEFAULT_SITE_UPDATE = '/site/v202211/sites/{site_id}'
DEFAULT_DEVICE_LIST = '/api/v5/devices'
DEFAULT_DEVICE_CREATE = '/api/v5/device'
DEFAULT_AUTH_EMAIL_HEADER = 'X-CH-Auth-Email'
DEFAULT_AUTH_TOKEN_HEADER = 'X-CH-Auth-API-Token'

# Site derivation defaults
DEFAULT_SITE_TYPE = 'SITE_TYPE_BRANCH'
DEFAULT_CLASS_KEY = ''
DEFAULT_MAX_PREFIX_LEN = 0
DEFAULT_INFRA_PATTERNS = ('mgmt', 'management', 'transit', 'p2p',
                          'point-to-point', 'loopback', 'infra', 'wan')
DEFAULT_INFRA_PREFIX_LEN = 30
DEFAULT_SITE_TYPE_MAP = {
    'dc': 'SITE_TYPE_DATA_CENTER',
    'datacenter': 'SITE_TYPE_DATA_CENTER',
    'data center': 'SITE_TYPE_DATA_CENTER',
    'data centre': 'SITE_TYPE_DATA_CENTER',
    'cloud': 'SITE_TYPE_CLOUD',
    'branch': 'SITE_TYPE_BRANCH',
    'office': 'SITE_TYPE_BRANCH',
    'remote': 'SITE_TYPE_BRANCH',
    'connectivity': 'SITE_TYPE_CONNECTIVITY',
    'pop': 'SITE_TYPE_CONNECTIVITY',
    'customer': 'SITE_TYPE_CUSTOMER',
}

DEFAULT_TIMEOUT = 30


@dataclass(frozen=True)
class NiosConfig:
    gm: str = ''
    user: str = ''
    password: str = ''
    wapi_version: str = DEFAULT_WAPI_VERSION
    valid_cert: bool = False
    network_view: str = ''
    timeout_seconds: int = DEFAULT_TIMEOUT
    page_size: int = DEFAULT_NIOS_PAGE_SIZE


@dataclass(frozen=True)
class UddiConfig:
    base_url: str = DEFAULT_UDDI_BASE_URL
    api_key: str = ''
    ip_space: str = ''
    verify_ssl: bool = True
    timeout_seconds: int = DEFAULT_TIMEOUT
    page_token_param: str = DEFAULT_PAGE_TOKEN_PARAM
    next_token_key: str = DEFAULT_NEXT_TOKEN_KEY
    page_size_param: str = DEFAULT_PAGE_SIZE_PARAM
    tag_filter_param: str = DEFAULT_TAG_FILTER_PARAM
    page_size: int = DEFAULT_UDDI_PAGE_SIZE
    subnet_list: str = DEFAULT_SUBNET_LIST
    address_block_list: str = DEFAULT_ADDRESS_BLOCK_LIST
    asset_search: str = DEFAULT_ASSET_SEARCH
    asset_category: str = DEFAULT_ASSET_CATEGORY


@dataclass(frozen=True)
class KentikConfig:
    email: str = ''
    token: str = ''
    base_url: str = DEFAULT_KENTIK_BASE_URL
    grpc_base_url: str = DEFAULT_KENTIK_GRPC_BASE_URL
    verify_ssl: bool = True
    timeout_seconds: int = DEFAULT_TIMEOUT
    site_list: str = DEFAULT_SITE_LIST
    site_create: str = DEFAULT_SITE_CREATE
    site_update: str = DEFAULT_SITE_UPDATE
    device_list: str = DEFAULT_DEVICE_LIST
    device_create: str = DEFAULT_DEVICE_CREATE
    auth_email_header: str = DEFAULT_AUTH_EMAIL_HEADER
    auth_token_header: str = DEFAULT_AUTH_TOKEN_HEADER


@dataclass(frozen=True)
class SiteConfig:
    '''
    How sites, classifications and summarisation are derived from source data
    '''
    site_key: str = ''
    class_key: str = DEFAULT_CLASS_KEY
    site_type_key: str = ''
    default_site_type: str = DEFAULT_SITE_TYPE
    site_type_map: dict = field(default_factory=lambda: dict(DEFAULT_SITE_TYPE_MAP))
    max_prefix_len: int = DEFAULT_MAX_PREFIX_LEN
    infra_patterns: tuple = DEFAULT_INFRA_PATTERNS
    infra_prefix_len: int = DEFAULT_INFRA_PREFIX_LEN
    site_filter: str = ''
    include_address_blocks: bool = False
    replace_networks: bool = False


@dataclass(frozen=True)
class DeviceConfig:
    '''
    Device discovery behaviour. Kentik device writes are not enabled in v1.
    '''
    enabled: bool = False
    use_insight: bool = False
    use_uai: bool = False
    use_gateways: bool = False
    roles: tuple = ('router', 'switch', 'firewall')
    plan_id: int = 0
    sample_rate: int = 1024
    minimize_snmp: bool = True


@dataclass(frozen=True)
class ProjectConfig:
    source: str = 'uddi'
    nios: NiosConfig = field(default_factory=NiosConfig)
    uddi: UddiConfig = field(default_factory=UddiConfig)
    kentik: KentikConfig = field(default_factory=KentikConfig)
    site: SiteConfig = field(default_factory=SiteConfig)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    state_file: str = DEFAULT_STATE_FILE


def read_ini(ini_file: str) -> dict:
    '''
    Read credentials from the ini file

    Parameters:
        ini_file (str): path to the ini file

    Returns:
        dict: section name -> {key: value}, empty for missing sections
    '''
    creds = {section: {} for section in INI_SECTIONS}
    config = configparser.ConfigParser()
    usable = True

    try:
        files_read = config.read(ini_file)
    except configparser.Error as exc:
        logger.warning('Could not parse credentials file %s: %s', ini_file, exc)
        usable = False
        files_read = []

    if usable and not files_read:
        logger.debug('Credentials file %s not found', ini_file)
        usable = False

    if usable:
        for section in INI_SECTIONS:
            if section in config:
                for key in config[section]:
                    creds[section][key] = config[section][key].strip("'\"")
                logger.debug('Read %d keys from [%s] in %s',
                             len(creds[section]), section, ini_file)
            else:
                logger.debug('No [%s] section found in %s', section, ini_file)

    return creds


def _as_bool(value, default: bool = False) -> bool:
    '''
    Coerce an ini/yaml value into a bool

    Parameters:
        value: source value (str, bool, None)
        default (bool): value to use when unset

    Returns:
        bool: coerced value
    '''
    if value is None or value == '':
        result = default
    elif isinstance(value, bool):
        result = value
    else:
        result = str(value).strip().lower() in ('true', 'yes', 'y', '1', 'on')
    return result


def _as_tuple(value) -> tuple:
    '''
    Coerce a scalar or list into a tuple of stripped strings

    Parameters:
        value: None, comma separated string, list or tuple

    Returns:
        tuple: tuple of non-empty strings
    '''
    if value is None:
        parts = []
    elif isinstance(value, str):
        parts = [p.strip() for p in value.split(',')]
    elif isinstance(value, (list, tuple)):
        parts = [str(p).strip() for p in value]
    else:
        raise ValueError(f'Expected a string or list, got {type(value).__name__}')
    return tuple(p for p in parts if p)


def _resolve(cli, env_key: str, ini_value: str, default: str = '') -> str:
    '''
    Resolve a value by priority: CLI flag, environment variable, ini, default

    Parameters:
        cli: value supplied on the command line (or None)
        env_key (str): environment variable name
        ini_value (str): value read from the ini file
        default (str): fallback value

    Returns:
        str: the resolved value
    '''
    result = default
    if cli not in (None, ''):
        result = cli
    elif os.environ.get(env_key):
        result = os.environ[env_key]
    elif ini_value not in (None, ''):
        result = ini_value
    return result


def load_yaml(path: str | Path) -> dict:
    '''
    Load the optional YAML behaviour config

    Parameters:
        path (str or Path): path to the YAML file

    Returns:
        dict: parsed config, empty dict when no path given
    '''
    raw = {}
    if path:
        config_path = Path(path)
        loaded = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError('Configuration root must be a mapping')
        raw = loaded
        logger.debug('Loaded YAML config from %s', config_path)
    return raw


def _section(raw: dict, name: str) -> dict:
    '''
    Fetch and validate a YAML section

    Parameters:
        raw (dict): full YAML document
        name (str): section name

    Returns:
        dict: the section, empty dict when absent
    '''
    section = raw.get(name) or {}
    if not isinstance(section, dict):
        raise ValueError(f'{name} section must be a mapping')
    return section


def build_config(args, ini_file: str = '', yaml_file: str = '') -> ProjectConfig:
    '''
    Assemble the project configuration from ini, YAML, env and CLI args

    Credential priority is CLI flag, then environment variable, then ini file.
    YAML supplies behaviour and any endpoint or field name overrides.

    Parameters:
        args: parsed argparse namespace (or any object with the CLI attributes)
        ini_file (str): path to the credentials ini file
        yaml_file (str): path to the optional YAML behaviour config

    Returns:
        ProjectConfig: validated configuration
    '''
    creds = read_ini(ini_file or DEFAULT_INI_FILE)
    raw = load_yaml(yaml_file)

    nios_ini = creds.get('NIOS', {})
    uddi_ini = creds.get('UDDI', {})
    kentik_ini = creds.get('KENTIK', {})

    nios_yaml = _section(raw, 'nios')
    uddi_yaml = _section(raw, 'uddi')
    kentik_yaml = _section(raw, 'kentik')
    site_yaml = _section(raw, 'site')
    device_yaml = _section(raw, 'device')

    nios = NiosConfig(
        gm=_resolve(getattr(args, 'gm', None), 'IBX_NIOS_GM', nios_ini.get('gm', ''),
                    nios_yaml.get('gm', '')),
        user=_resolve(None, 'IBX_NIOS_USER', nios_ini.get('user', ''), 'admin'),
        password=_resolve(None, 'IBX_NIOS_PASS', nios_ini.get('pass', '')),
        wapi_version=str(nios_yaml.get('wapi_version',
                                       nios_ini.get('wapi_version', DEFAULT_WAPI_VERSION))),
        valid_cert=_as_bool(nios_yaml.get('valid_cert', nios_ini.get('valid_cert')), False),
        network_view=_resolve(getattr(args, 'network_view', None), 'IBX_NIOS_VIEW',
                              nios_yaml.get('network_view', '')),
        timeout_seconds=int(nios_yaml.get('timeout_seconds', DEFAULT_TIMEOUT)),
        page_size=int(nios_yaml.get('page_size', DEFAULT_NIOS_PAGE_SIZE)),
    )

    uddi = UddiConfig(
        base_url=_resolve(None, 'IB_BASE_URL', uddi_ini.get('base_url', ''),
                          uddi_yaml.get('base_url', DEFAULT_UDDI_BASE_URL)).rstrip('/'),
        api_key=_resolve(None, 'IB_API_KEY', uddi_ini.get('api_key', '')),
        ip_space=_resolve(getattr(args, 'ip_space', None), 'IB_IP_SPACE',
                          uddi_yaml.get('ip_space', '')),
        verify_ssl=_as_bool(uddi_yaml.get('verify_ssl'), True),
        timeout_seconds=int(uddi_yaml.get('timeout_seconds', DEFAULT_TIMEOUT)),
        page_size=int(uddi_yaml.get('page_size', DEFAULT_UDDI_PAGE_SIZE)),
        subnet_list=str(uddi_yaml.get('subnet_list', DEFAULT_SUBNET_LIST)),
        address_block_list=str(uddi_yaml.get('address_block_list', DEFAULT_ADDRESS_BLOCK_LIST)),
        asset_search=str(uddi_yaml.get('asset_search', DEFAULT_ASSET_SEARCH)),
        asset_category=str(uddi_yaml.get('asset_category', DEFAULT_ASSET_CATEGORY)),
    )

    kentik = KentikConfig(
        email=_resolve(None, 'KENTIK_EMAIL', kentik_ini.get('email', '')),
        token=_resolve(None, 'KENTIK_TOKEN', kentik_ini.get('token', '')),
        base_url=_resolve(None, 'KENTIK_BASE_URL', kentik_ini.get('base_url', ''),
                          kentik_yaml.get('base_url', DEFAULT_KENTIK_BASE_URL)).rstrip('/'),
        grpc_base_url=_resolve(None, 'KENTIK_GRPC_BASE_URL',
                               kentik_ini.get('grpc_base_url', ''),
                               kentik_yaml.get('grpc_base_url',
                                               DEFAULT_KENTIK_GRPC_BASE_URL)).rstrip('/'),
        verify_ssl=_as_bool(kentik_yaml.get('verify_ssl'), True),
        timeout_seconds=int(kentik_yaml.get('timeout_seconds', DEFAULT_TIMEOUT)),
        site_list=str(kentik_yaml.get('site_list', DEFAULT_SITE_LIST)),
        site_create=str(kentik_yaml.get('site_create', DEFAULT_SITE_CREATE)),
        site_update=str(kentik_yaml.get('site_update', DEFAULT_SITE_UPDATE)),
        device_list=str(kentik_yaml.get('device_list', DEFAULT_DEVICE_LIST)),
        device_create=str(kentik_yaml.get('device_create', DEFAULT_DEVICE_CREATE)),
        auth_email_header=str(kentik_yaml.get('auth_email_header', DEFAULT_AUTH_EMAIL_HEADER)),
        auth_token_header=str(kentik_yaml.get('auth_token_header', DEFAULT_AUTH_TOKEN_HEADER)),
    )

    site_type_map = dict(DEFAULT_SITE_TYPE_MAP)
    for key, value in (site_yaml.get('site_type_map') or {}).items():
        site_type_map[str(key).strip().lower()] = str(value)

    site = SiteConfig(
        site_key=str(getattr(args, 'site_key', None) or site_yaml.get('site_key', '')).strip(),
        class_key=str(getattr(args, 'class_key', None) or site_yaml.get('class_key', '')).strip(),
        site_type_key=str(getattr(args, 'site_type_key', None)
                          or site_yaml.get('site_type_key', '')).strip(),
        default_site_type=str(site_yaml.get('default_site_type', DEFAULT_SITE_TYPE)),
        site_type_map=site_type_map,
        max_prefix_len=int(getattr(args, 'max_prefix_len', None)
                           or site_yaml.get('max_prefix_len', DEFAULT_MAX_PREFIX_LEN)),
        infra_patterns=_as_tuple(site_yaml.get('infra_patterns')) or DEFAULT_INFRA_PATTERNS,
        infra_prefix_len=int(site_yaml.get('infra_prefix_len', DEFAULT_INFRA_PREFIX_LEN)),
        site_filter=str(getattr(args, 'site_filter', None) or site_yaml.get('site_filter', '')),
        include_address_blocks=_as_bool(getattr(args, 'include_address_blocks', None)
                                        or site_yaml.get('include_address_blocks'), False),
        replace_networks=_as_bool(getattr(args, 'replace_networks', None)
                                  or site_yaml.get('replace_networks'), False),
    )

    device = DeviceConfig(
        enabled=_as_bool(getattr(args, 'devices', None) or device_yaml.get('enabled'), False),
        use_insight=_as_bool(getattr(args, 'use_insight', None)
                             or device_yaml.get('use_insight'), False),
        use_uai=_as_bool(getattr(args, 'use_uai', None) or device_yaml.get('use_uai'), False),
        use_gateways=_as_bool(getattr(args, 'use_gateways', None)
                              or device_yaml.get('use_gateways'), False),
        roles=_as_tuple(device_yaml.get('roles')) or ('router', 'switch', 'firewall'),
        plan_id=int(device_yaml.get('plan_id', 0)),
        sample_rate=int(device_yaml.get('sample_rate', 1024)),
        minimize_snmp=_as_bool(device_yaml.get('minimize_snmp'), True),
    )

    source = str(getattr(args, 'source', None) or raw.get('source', 'uddi')).lower()
    if source not in ('nios', 'uddi'):
        raise ValueError(f"source must be 'nios' or 'uddi', got {source!r}")

    if site.max_prefix_len and not 1 <= site.max_prefix_len <= 32:
        raise ValueError('max_prefix_len must be between 1 and 32')

    config = ProjectConfig(
        source=source,
        nios=nios,
        uddi=uddi,
        kentik=kentik,
        site=site,
        device=device,
        state_file=str(raw.get('state_file', DEFAULT_STATE_FILE)),
    )

    logger.debug('Configuration assembled for source %s', source)
    return config


def validate_source_credentials(config: ProjectConfig) -> list:
    '''
    Check the credentials needed for the selected source are present

    Parameters:
        config (ProjectConfig): assembled configuration

    Returns:
        list: list of human readable problems, empty when usable
    '''
    problems = []
    if config.source == 'nios':
        if not config.nios.gm:
            problems.append('NIOS grid master address is not set ([NIOS] gm)')
        if not config.nios.user or not config.nios.password:
            problems.append('NIOS credentials are not set ([NIOS] user/pass)')
    else:
        if not config.uddi.api_key:
            problems.append('UDDI API key is not set ([UDDI] api_key)')
    if not config.site.site_key:
        problems.append('No site EA/tag key selected (--site-key)')
    for problem in problems:
        logger.error('Configuration problem: %s', problem)
    return problems


def validate_kentik_credentials(config: ProjectConfig) -> list:
    '''
    Check the Kentik credentials are present

    Parameters:
        config (ProjectConfig): assembled configuration

    Returns:
        list: list of human readable problems, empty when usable
    '''
    problems = []
    if not config.kentik.email:
        problems.append('Kentik API email is not set ([KENTIK] email)')
    if not config.kentik.token:
        problems.append('Kentik API token is not set ([KENTIK] token)')
    for problem in problems:
        logger.error('Configuration problem: %s', problem)
    return problems


def with_site_key(config: ProjectConfig, site_key: str) -> ProjectConfig:
    '''
    Return a copy of the config with a different site key

    Parameters:
        config (ProjectConfig): source configuration
        site_key (str): replacement EA/tag key

    Returns:
        ProjectConfig: updated copy
    '''
    return replace(config, site=replace(config.site, site_key=site_key))

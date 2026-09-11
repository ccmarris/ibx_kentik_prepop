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
# Devices use the versioned device API rather than legacy /api/v5/device,
# because only that schema carries the nms block needed for NMS devices.
# (Verified against kentik/api-schema-public
# proto/kentik/device/v202504beta2/device.proto.)
DEFAULT_DEVICE_LIST = '/device/v202504beta2/device'
DEFAULT_DEVICE_CREATE = '/device/v202504beta2/device'
DEFAULT_DEVICE_READ = '/device/v202504beta2/device/{device_id}'
DEFAULT_DEVICE_UPDATE = '/device/v202504beta2/device/{device_id}'
# Plans come from the documented v5 admin API (id, name, active, max_devices,
# max_fps, deviceTypes, devices).
DEFAULT_PLAN_LIST = '/api/v5/plans'
DEFAULT_AGENT_LIST = '/kagent/v202401/agents'
DEFAULT_CREDENTIAL_LIST = '/credential/v202407alpha1/group'
DEFAULT_AUTH_EMAIL_HEADER = 'X-CH-Auth-Email'
DEFAULT_AUTH_TOKEN_HEADER = 'X-CH-Auth-API-Token'

# Site derivation defaults. 'Site' is the conventional EA/tag name, so it is
# the default rather than something the operator has to supply every time.
DEFAULT_SITE_KEY = 'Site'
DEFAULT_SITE_TYPE = 'SITE_TYPE_BRANCH'
DEFAULT_CLASS_KEY = ''
DEFAULT_MAX_PREFIX_LEN = 0
DEFAULT_INFRA_PATTERNS = ('mgmt', 'management', 'transit', 'p2p',
                          'point-to-point', 'loopback', 'infra', 'wan')
DEFAULT_INFRA_PREFIX_LEN = 30
# EA/tag names looked for when populating the Kentik site postal address and
# coordinates. Matched case-insensitively; first hit wins. PostalAddress
# requires address, city and country, so a partial address is not submitted.
DEFAULT_ADDRESS_KEYS = {
    'address': ('address', 'street', 'street_address', 'site_address', 'address1'),
    'city': ('city', 'town', 'locality'),
    'region': ('region', 'state', 'county', 'province'),
    'postal_code': ('postal_code', 'postcode', 'post_code', 'zip', 'zip_code'),
    'country': ('country', 'country_code', 'country_name'),
}
DEFAULT_GEO_KEYS = {
    'lat': ('latitude', 'lat'),
    'lon': ('longitude', 'long', 'lon', 'lng'),
}
REQUIRED_ADDRESS_FIELDS = ('address', 'city', 'country')

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

# Device defaults. The plan is resolved by name so it works on any tenant;
# 'Free Flowpak Plan' is Kentik's no-cost flow plan.
DEFAULT_PLAN_NAME = 'Free Flowpak Plan'
DEFAULT_DEVICE_MODE = 'flow'
DEFAULT_DEVICE_SUBTYPE = 'router'
# Flow sample rate reported to Kentik. 1 means unsampled - the right default
# for pre-population, where the rate the device actually uses is not known.
DEFAULT_SAMPLE_RATE = 1
DEFAULT_SENDING_IPS = 'mgmt'
DEFAULT_SNMP_PORT = 161

# device_bgp_type is a required string on the device API, not a boolean.
# 'none' uses generic IP/ASN mapping and needs nothing else; 'device' peers with
# the device itself and requires an ASN plus a peering address; 'other_device'
# shares an already-peered device's routing table and requires that device's id.
# Fields and EA/tag names a device description is taken from, in order of
# preference. Whatever is found goes into the Kentik device_description.
DEFAULT_DESCRIPTION_KEYS = ('description', 'comment', 'comments', 'notes',
                            'purpose', 'device_description', 'role')

# How SNMP is collected, independent of what the device is for. Both agent
# options attach a Universal Agent through the device's nms block - the portal
# shows them on the device's SNMP tab as a Collection Agent:
#   none        - no SNMP
#   community   - Kentik polls with a community string (device_snmp_community)
#   agent-flow  - "Agent-based SNMP for Flow Enrichment (Traffic device)": the
#                 agent polls interface data to enrich the device's flow
#   agent-full  - "Agent-based SNMP for Full Monitoring": the agent also
#                 collects the full NMS metric set, which is where the
#                 monitoring template applies
# Both agent modes need an agent and a credential from the Kentik credential
# vault.
# Network Insight and UAI are enabled by default: each only exists on one
# platform, so enabling both simply means "use whichever discovery this
# platform has". Gateway inference stays opt-in because it invents devices from
# a DHCP option rather than discovering them.
DEFAULT_USE_INSIGHT = True
DEFAULT_USE_UAI = True
DEFAULT_USE_GATEWAYS = False

DEFAULT_SNMP_MODE = 'none'
SNMP_MODES = ('none', 'community', 'agent-flow', 'agent-full')
AGENT_SNMP_MODES = ('agent-flow', 'agent-full')

DEFAULT_BGP_TYPE = 'none'
BGP_TYPES = ('none', 'device', 'other_device')
# device_bgp_flowspec is the boolean that sits alongside it
DEFAULT_BGP_FLOWSPEC = False

DEFAULT_TIMEOUT = 30

# Kentik rate limits and occasionally answers a burst with a 5xx. A bulk device
# apply is hundreds of consecutive writes, so transient failures are retried
# rather than reported as a failed device the operator has to chase.
DEFAULT_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 2.0

# Kentik list pagination. The whole site and device list has to be read: an
# object missing from a truncated response is planned as a create, which for a
# device means a duplicate name or a wasted licence slot. Page size is left at 0
# - meaning "do not ask, take whatever the API gives" - because the parameter
# names below are not confirmed for every API version, and sending an unknown
# query parameter is a worse failure than not paging. A page token in the
# response is followed whatever the page size, and a short read is reported.
# VERIFY the two parameter names against the tenant's API docs if a large
# account ever reports a truncation warning.
DEFAULT_KENTIK_PAGE_SIZE = 0
DEFAULT_KENTIK_PAGE_SIZE_PARAM = 'page_size'
DEFAULT_KENTIK_PAGE_TOKEN_PARAM = 'page_token'
DEFAULT_KENTIK_MAX_PAGES = 100


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
    device_read: str = DEFAULT_DEVICE_READ
    device_update: str = DEFAULT_DEVICE_UPDATE
    plan_list: str = DEFAULT_PLAN_LIST
    agent_list: str = DEFAULT_AGENT_LIST
    credential_list: str = DEFAULT_CREDENTIAL_LIST
    auth_email_header: str = DEFAULT_AUTH_EMAIL_HEADER
    auth_token_header: str = DEFAULT_AUTH_TOKEN_HEADER
    retries: int = DEFAULT_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    page_size: int = DEFAULT_KENTIK_PAGE_SIZE
    page_size_param: str = DEFAULT_KENTIK_PAGE_SIZE_PARAM
    page_token_param: str = DEFAULT_KENTIK_PAGE_TOKEN_PARAM
    max_pages: int = DEFAULT_KENTIK_MAX_PAGES


@dataclass(frozen=True)
class SiteConfig:
    '''
    How sites, classifications and summarisation are derived from source data
    '''
    site_key: str = DEFAULT_SITE_KEY
    class_key: str = DEFAULT_CLASS_KEY
    site_type_key: str = ''
    default_site_type: str = DEFAULT_SITE_TYPE
    site_type_map: dict = field(default_factory=lambda: dict(DEFAULT_SITE_TYPE_MAP))
    max_prefix_len: int = DEFAULT_MAX_PREFIX_LEN
    infra_patterns: tuple = DEFAULT_INFRA_PATTERNS
    infra_prefix_len: int = DEFAULT_INFRA_PREFIX_LEN
    site_filter: str = ''
    include_address_blocks: bool = False
    address_keys: dict = field(default_factory=lambda: dict(DEFAULT_ADDRESS_KEYS))
    geo_keys: dict = field(default_factory=lambda: dict(DEFAULT_GEO_KEYS))
    use_address: bool = True


@dataclass(frozen=True)
class DeviceConfig:
    '''
    Device discovery and creation behaviour
    '''
    enabled: bool = False
    use_insight: bool = DEFAULT_USE_INSIGHT
    use_uai: bool = DEFAULT_USE_UAI
    use_gateways: bool = DEFAULT_USE_GATEWAYS
    roles: tuple = ('router', 'switch', 'firewall')
    description_keys: tuple = DEFAULT_DESCRIPTION_KEYS
    mode: str = DEFAULT_DEVICE_MODE
    subtype: str = DEFAULT_DEVICE_SUBTYPE
    plan_name: str = DEFAULT_PLAN_NAME
    plan_id: int = 0
    sample_rate: int = DEFAULT_SAMPLE_RATE
    minimize_snmp: bool = True
    snmp_mode: str = DEFAULT_SNMP_MODE
    snmp_community: str = ''
    # Left empty deliberately: a tenant rejected this field with "Expected a
    # value of type `never`", so the device write schema forbids it.
    flow_snmp_credential_name: str = ''
    send_flow_snmp_credential: bool = True
    sending_ips: str = DEFAULT_SENDING_IPS
    sending_ip_map: dict = field(default_factory=dict)
    bgp_type: str = DEFAULT_BGP_TYPE
    bgp_flowspec: bool = DEFAULT_BGP_FLOWSPEC
    bgp_neighbor_asn: str = ''
    bgp_neighbor_ip: str = ''
    bgp_neighbor_ip6: str = ''
    bgp_device_id: str = ''
    agent_id: str = ''
    credential_name: str = ''
    snmp_port: int = DEFAULT_SNMP_PORT
    monitoring_template_id: int = 0
    update_existing: bool = False
    allow_over_capacity: bool = False
    exclude: tuple = ()


@dataclass(frozen=True)
class ProjectConfig:
    source: str = 'uddi'
    task: str = 'sites'
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
        # Section names are matched case-insensitively: [Kentik], [kentik] and
        # [KENTIK] are the same section as far as this tool is concerned. An
        # exact-case match is applied last so it wins if a file has both.
        for canonical in INI_SECTIONS:
            found = [s for s in config.sections()
                     if s.casefold() == canonical.casefold() and s != canonical]
            if canonical in config.sections():
                found.append(canonical)
            for section in found:
                for key in config[section]:
                    creds[canonical][key.strip().lower()] = \
                        config[section][key].strip("'\"")
            if found:
                logger.debug('Read %d key(s) from [%s] in %s',
                             len(creds[canonical]), found[-1], ini_file)
            else:
                logger.debug('No [%s] section found in %s', canonical, ini_file)

    return creds


def _first(mapping: dict, *keys) -> str:
    '''
    Return the first non-empty value among several accepted key spellings

    Different tools in this collection have used different names for the same
    credential, so a handful of aliases are accepted per field.

    Parameters:
        mapping (dict): section values from the ini
        keys: key names to try, in order of preference

    Returns:
        str: the first non-empty value, empty string when none are set
    '''
    value = ''
    for key in keys:
        candidate = mapping.get(key)
        if candidate not in (None, ''):
            value = candidate
            break
    return value


def _tri_bool(cli, yaml_value, default: bool) -> bool:
    '''
    Resolve a three-state boolean: command line, then YAML, then the default

    argparse's store_true cannot express "not given", so the flags that have a
    True default use BooleanOptionalAction and arrive as None when unset.

    Parameters:
        cli: value from the command line, or None when not given
        yaml_value: value from the YAML config, or None when absent
        default (bool): value to use when neither was given

    Returns:
        bool: resolved value
    '''
    if cli is not None:
        result = bool(cli)
    elif yaml_value is not None:
        result = _as_bool(yaml_value, default)
    else:
        result = default
    return result


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
        gm=_resolve(getattr(args, 'gm', None), 'IBX_NIOS_GM',
                    _first(nios_ini, 'gm', 'grid_master', 'host', 'master'),
                    nios_yaml.get('gm', '')),
        user=_resolve(None, 'IBX_NIOS_USER', _first(nios_ini, 'user', 'username'),
                      'admin'),
        password=_resolve(None, 'IBX_NIOS_PASS',
                          _first(nios_ini, 'pass', 'password')),
        wapi_version=str(nios_yaml.get('wapi_version',
                                       _first(nios_ini, 'wapi_version',
                                              'api_version', 'version')
                                       or DEFAULT_WAPI_VERSION)),
        valid_cert=_as_bool(nios_yaml.get('valid_cert', nios_ini.get('valid_cert')), False),
        network_view=_resolve(getattr(args, 'network_view', None), 'IBX_NIOS_VIEW',
                              nios_yaml.get('network_view', '')),
        timeout_seconds=int(nios_yaml.get('timeout_seconds', DEFAULT_TIMEOUT)),
        page_size=int(nios_yaml.get('page_size', DEFAULT_NIOS_PAGE_SIZE)),
    )

    uddi = UddiConfig(
        base_url=_resolve(None, 'IB_BASE_URL',
                          _first(uddi_ini, 'base_url', 'url'),
                          uddi_yaml.get('base_url', DEFAULT_UDDI_BASE_URL)).rstrip('/'),
        api_key=_resolve(None, 'IB_API_KEY',
                         _first(uddi_ini, 'api_key', 'apikey', 'key', 'token')),
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
        email=_resolve(None, 'KENTIK_EMAIL',
                       _first(kentik_ini, 'email', 'api_email', 'user',
                              'username')),
        token=_resolve(None, 'KENTIK_TOKEN',
                       _first(kentik_ini, 'token', 'api_token', 'api_key',
                              'key')),
        base_url=_resolve(None, 'KENTIK_BASE_URL',
                          _first(kentik_ini, 'base_url', 'url'),
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
        device_read=str(kentik_yaml.get('device_read', DEFAULT_DEVICE_READ)),
        device_update=str(kentik_yaml.get('device_update', DEFAULT_DEVICE_UPDATE)),
        plan_list=str(kentik_yaml.get('plan_list', DEFAULT_PLAN_LIST)),
        agent_list=str(kentik_yaml.get('agent_list', DEFAULT_AGENT_LIST)),
        credential_list=str(kentik_yaml.get('credential_list', DEFAULT_CREDENTIAL_LIST)),
        auth_email_header=str(kentik_yaml.get('auth_email_header', DEFAULT_AUTH_EMAIL_HEADER)),
        auth_token_header=str(kentik_yaml.get('auth_token_header', DEFAULT_AUTH_TOKEN_HEADER)),
        retries=int(kentik_yaml.get('retries', DEFAULT_RETRIES)),
        retry_backoff=float(kentik_yaml.get('retry_backoff', DEFAULT_RETRY_BACKOFF)),
        page_size=int(kentik_yaml.get('page_size', DEFAULT_KENTIK_PAGE_SIZE)),
        page_size_param=str(kentik_yaml.get('page_size_param',
                                            DEFAULT_KENTIK_PAGE_SIZE_PARAM)),
        page_token_param=str(kentik_yaml.get('page_token_param',
                                             DEFAULT_KENTIK_PAGE_TOKEN_PARAM)),
        max_pages=int(kentik_yaml.get('max_pages', DEFAULT_KENTIK_MAX_PAGES)),
    )

    site_type_map = dict(DEFAULT_SITE_TYPE_MAP)
    for key, value in (site_yaml.get('site_type_map') or {}).items():
        site_type_map[str(key).strip().lower()] = str(value)

    address_keys = {}
    for field_name, candidates in DEFAULT_ADDRESS_KEYS.items():
        override = (site_yaml.get('address_keys') or {}).get(field_name)
        address_keys[field_name] = _as_tuple(override) or candidates

    geo_keys = {}
    for field_name, candidates in DEFAULT_GEO_KEYS.items():
        override = (site_yaml.get('geo_keys') or {}).get(field_name)
        geo_keys[field_name] = _as_tuple(override) or candidates

    site = SiteConfig(
        site_key=(str(getattr(args, 'site_key', None)
                      or site_yaml.get('site_key', '')).strip()
                  or DEFAULT_SITE_KEY),
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
        address_keys=address_keys,
        geo_keys=geo_keys,
        use_address=_as_bool(site_yaml.get('use_address'), True),
    )

    mode = str(getattr(args, 'device_mode', None)
               or device_yaml.get('mode', DEFAULT_DEVICE_MODE)).lower()
    if mode not in ('flow', 'nms'):
        raise ValueError(f"device mode must be 'flow' or 'nms', got {mode!r}")

    sending_ips = str(getattr(args, 'sending_ips', None)
                      or device_yaml.get('sending_ips', DEFAULT_SENDING_IPS)).lower()
    if sending_ips not in ('mgmt', 'all'):
        raise ValueError(f"sending_ips must be 'mgmt' or 'all', got {sending_ips!r}")

    snmp_mode = str(getattr(args, 'snmp_mode', None)
                    or device_yaml.get('snmp_mode', DEFAULT_SNMP_MODE)).lower()
    if snmp_mode not in SNMP_MODES:
        raise ValueError(f"snmp_mode must be one of {', '.join(SNMP_MODES)}, "
                         f'got {snmp_mode!r}')
    # An NMS device is fully monitored by an agent by definition.
    if mode == 'nms':
        snmp_mode = 'agent-full'

    bgp_type = str(getattr(args, 'bgp_type', None)
                   or device_yaml.get('bgp_type', DEFAULT_BGP_TYPE)).lower()
    if bgp_type not in BGP_TYPES:
        raise ValueError(f"bgp_type must be one of {', '.join(BGP_TYPES)}, "
                         f'got {bgp_type!r}')

    exclude = list(_as_tuple(getattr(args, 'exclude_device', None)))
    exclude.extend(_as_tuple(device_yaml.get('exclude')))
    exclude_file = getattr(args, 'exclude_file', None)
    if exclude_file:
        for line in Path(exclude_file).read_text(encoding='utf-8').splitlines():
            entry = line.split('#')[0].strip()
            if entry:
                exclude.append(entry)

    device = DeviceConfig(
        enabled=_as_bool(getattr(args, 'devices', None) or device_yaml.get('enabled'), False),
        use_insight=_tri_bool(getattr(args, 'use_insight', None),
                              device_yaml.get('use_insight'),
                              DEFAULT_USE_INSIGHT),
        use_uai=_tri_bool(getattr(args, 'use_uai', None),
                          device_yaml.get('use_uai'), DEFAULT_USE_UAI),
        use_gateways=_tri_bool(getattr(args, 'use_gateways', None),
                               device_yaml.get('use_gateways'),
                               DEFAULT_USE_GATEWAYS),
        roles=_as_tuple(device_yaml.get('roles')) or ('router', 'switch', 'firewall'),
        description_keys=(_as_tuple(device_yaml.get('description_keys'))
                          or DEFAULT_DESCRIPTION_KEYS),
        mode=mode,
        subtype=str(device_yaml.get('subtype', DEFAULT_DEVICE_SUBTYPE)),
        plan_name=str(getattr(args, 'plan_name', None)
                      or device_yaml.get('plan_name', DEFAULT_PLAN_NAME)),
        plan_id=int(getattr(args, 'plan_id', None) or device_yaml.get('plan_id', 0)),
        sample_rate=int(getattr(args, 'sample_rate', None)
                        or device_yaml.get('sample_rate', DEFAULT_SAMPLE_RATE)),
        minimize_snmp=_as_bool(device_yaml.get('minimize_snmp'), True),
        snmp_mode=snmp_mode,
        snmp_community=str(device_yaml.get('snmp_community', '')),
        flow_snmp_credential_name=str(
            device_yaml.get('flow_snmp_credential_name', '')),
        send_flow_snmp_credential=_as_bool(
            device_yaml.get('send_flow_snmp_credential'), True),
        sending_ips=sending_ips,
        sending_ip_map=dict(getattr(args, 'sending_ip_map', None) or {}),
        bgp_type=bgp_type,
        bgp_flowspec=_as_bool(getattr(args, 'bgp_flowspec', None)
                              or device_yaml.get('bgp_flowspec'),
                              DEFAULT_BGP_FLOWSPEC),
        bgp_neighbor_asn=str(getattr(args, 'bgp_neighbor_asn', None)
                             or device_yaml.get('bgp_neighbor_asn', '')),
        bgp_neighbor_ip=str(getattr(args, 'bgp_neighbor_ip', None)
                            or device_yaml.get('bgp_neighbor_ip', '')),
        bgp_neighbor_ip6=str(getattr(args, 'bgp_neighbor_ip6', None)
                             or device_yaml.get('bgp_neighbor_ip6', '')),
        bgp_device_id=str(getattr(args, 'bgp_device_id', None)
                          or device_yaml.get('bgp_device_id', '')),
        agent_id=str(getattr(args, 'agent_id', None) or device_yaml.get('agent_id', '')),
        credential_name=str(getattr(args, 'credential_name', None)
                            or device_yaml.get('credential_name', '')),
        snmp_port=int(device_yaml.get('snmp_port', DEFAULT_SNMP_PORT)),
        monitoring_template_id=int(getattr(args, 'monitoring_template_id', None)
                                   or device_yaml.get('monitoring_template_id', 0)),
        update_existing=_as_bool(getattr(args, 'update_devices', None)
                                 or device_yaml.get('update_existing'), False),
        allow_over_capacity=_as_bool(getattr(args, 'allow_over_capacity', None)
                                     or device_yaml.get('allow_over_capacity'), False),
        exclude=tuple(dict.fromkeys(exclude)),
    )

    source = str(getattr(args, 'source', None) or raw.get('source', 'uddi')).lower()
    if source not in ('nios', 'uddi'):
        raise ValueError(f"source must be 'nios' or 'uddi', got {source!r}")

    task = str(getattr(args, 'task', None) or raw.get('task', 'sites')).lower()
    if task not in ('sites', 'devices'):
        raise ValueError(f"task must be 'sites' or 'devices', got {task!r}")

    if site.max_prefix_len and not 1 <= site.max_prefix_len <= 32:
        raise ValueError('max_prefix_len must be between 1 and 32')

    config = ProjectConfig(
        source=source,
        task=task,
        nios=nios,
        uddi=uddi,
        kentik=kentik,
        site=site,
        device=device,
        state_file=str(raw.get('state_file', DEFAULT_STATE_FILE)),
    )

    logger.debug('Configuration assembled for source %s, task %s', source, task)
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
    if config.task == 'devices' and not (config.device.use_insight
                                         or config.device.use_uai
                                         or config.device.use_gateways):
        problems.append('Every device source is turned off, so there is '
                        'nothing to read devices from')
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

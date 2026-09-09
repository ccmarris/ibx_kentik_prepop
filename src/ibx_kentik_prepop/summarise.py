#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Site derivation, subnet summarisation and classification

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

import fnmatch
import ipaddress
import logging
import re
from collections import Counter
from ibx_kentik_prepop.config import REQUIRED_ADDRESS_FIELDS, ProjectConfig
from ibx_kentik_prepop.model import (CLASSIFICATIONS, CLASS_INFRASTRUCTURE,
                                     CLASS_OTHER, CLASS_USER_ACCESS, Site,
                                     SiteSubnet)

logger = logging.getLogger(__name__)


# Networks that are always infrastructure regardless of prefix length
ALWAYS_INFRA = ('127.0.0.0/8', '169.254.0.0/16', 'fe80::/10')

# Kentik device names accept 4-60 characters of alphanumerics and underscores
DEVICE_NAME_MIN = 4
DEVICE_NAME_MAX = 60
DEVICE_NAME_PAD = '_dev'

_WHITESPACE = re.compile(r'\s+')
_NOT_ALLOWED = re.compile(r'[^A-Za-z0-9_]+')


def normalise_site_name(value: str) -> str:
    '''
    Tidy a raw EA/tag value for use as a Kentik site title

    Leading/trailing whitespace is removed and internal runs of whitespace are
    collapsed so 'LON-DC1 ' and 'LON  DC1' do not become separate sites.

    Parameters:
        value (str): raw EA or tag value

    Returns:
        str: normalised site name, empty string when unusable
    '''
    name = ''
    if value is not None:
        name = _WHITESPACE.sub(' ', str(value)).strip()
    return name


def site_match_key(name: str) -> str:
    '''
    Comparison key for a site name

    Parameters:
        name (str): normalised site name

    Returns:
        str: casefolded comparison key
    '''
    return normalise_site_name(name).casefold()


def matches_filter(name: str, pattern: str) -> bool:
    '''
    Test a site name against an optional glob filter

    Parameters:
        name (str): site name
        pattern (str): glob pattern, empty means match everything

    Returns:
        bool: True when the site should be included
    '''
    if pattern:
        result = fnmatch.fnmatch(name.casefold(), pattern.casefold())
    else:
        result = True
    return result


def sanitise_device_name(name: str) -> str:
    '''
    Coerce a discovered device name into Kentik's naming rules

    Kentik accepts 4 to 60 characters of alphanumerics and underscores only.

    Parameters:
        name (str): raw device name or address

    Returns:
        str: sanitised name, empty string when nothing usable remains
    '''
    clean = _NOT_ALLOWED.sub('_', str(name or '')).strip('_')
    clean = re.sub(r'_{2,}', '_', clean)
    if clean and len(clean) < DEVICE_NAME_MIN:
        clean = f'{clean}{DEVICE_NAME_PAD}'[:DEVICE_NAME_MAX]
    if len(clean) > DEVICE_NAME_MAX:
        clean = clean[:DEVICE_NAME_MAX].rstrip('_')
    return clean


def tag_value(tags: dict, candidates) -> tuple:
    '''
    Find the first populated tag/EA among several candidate names

    Parameters:
        tags (dict): the record's tag or EA dictionary
        candidates: candidate key names, in order of preference

    Returns:
        tuple: (value, the key it came from) - both empty when nothing matched
    '''
    found = ('', '')
    for candidate in candidates or ():
        for key, value in (tags or {}).items():
            if key.casefold() != str(candidate).casefold():
                continue
            text = '' if value is None else str(value).strip()
            if text:
                found = (text, key)
                break
        if found[0]:
            break
    return found


def _majority(values: list) -> tuple:
    '''
    Most common value in a list, and whether the list disagreed

    Parameters:
        values (list): candidate values

    Returns:
        tuple: (value, list of the other values seen)
    '''
    winner = ''
    others = []
    populated = [v for v in values if v]
    if populated:
        counts = Counter(populated)
        winner = counts.most_common(1)[0][0]
        others = sorted(v for v in counts if v != winner)
    return winner, others


def _coordinate(text: str, limit: float) -> float:
    '''
    Parse and range-check a coordinate

    Parameters:
        text (str): raw value
        limit (float): absolute bound (90 for latitude, 180 for longitude)

    Returns:
        float: the coordinate, or None when unusable
    '''
    value = None
    try:
        candidate = float(str(text).strip())
    except (TypeError, ValueError):
        candidate = None
    if candidate is not None and -limit <= candidate <= limit:
        value = candidate
    return value


def extract_address(records: list, config: ProjectConfig) -> dict:
    '''
    Derive a Kentik postal address and coordinates from a site's subnet metadata

    Kentik's PostalAddress requires address, city and country, so a partial
    address is dropped rather than submitted. Coordinates are independent of the
    address and are range-checked.

    Parameters:
        records (list): normalised source subnet records for one site
        config (ProjectConfig): assembled configuration

    Returns:
        dict: postal, lat, lon, source (Kentik field -> EA/tag name) and notes
    '''
    result = {'postal': {}, 'lat': None, 'lon': None, 'source': {}, 'notes': []}

    if not config.site.use_address:
        return result

    postal = {}
    for field_name, candidates in config.site.address_keys.items():
        hits = [tag_value(r.get('tags'), candidates) for r in records]
        value, others = _majority([v for v, _ in hits])
        if not value:
            continue
        postal[field_name] = value
        result['source'][field_name] = next(k for v, k in hits if v == value)
        if others:
            result['notes'].append(
                ('address_conflict',
                 f'{field_name} differs across this site\'s subnets, using '
                 f'{value!r}', ', '.join(others)))

    missing = [f for f in REQUIRED_ADDRESS_FIELDS if not postal.get(f)]
    if postal and missing:
        result['notes'].append(
            ('partial_address',
             f'Postal address not submitted: Kentik requires '
             f'{", ".join(REQUIRED_ADDRESS_FIELDS)} and '
             f'{", ".join(missing)} is missing',
             ', '.join(f'{k}={v}' for k, v in sorted(postal.items()))))
    elif postal:
        result['postal'] = postal

    for field_name, limit in (('lat', 90.0), ('lon', 180.0)):
        candidates = config.site.geo_keys.get(field_name, ())
        hits = [tag_value(r.get('tags'), candidates) for r in records]
        value, _ = _majority([v for v, _ in hits])
        if not value:
            continue
        coordinate = _coordinate(value, limit)
        if coordinate is None:
            result['notes'].append(
                ('bad_coordinate',
                 f'{field_name} value {value!r} is not a number within '
                 f'+/-{limit:g}, ignored', ''))
        else:
            result[field_name] = coordinate
            result['source'][field_name] = next(k for v, k in hits if v == value)

    if (result['lat'] is None) != (result['lon'] is None):
        result['notes'].append(
            ('partial_coordinates',
             'Only one of latitude/longitude was found, so neither was used', ''))
        result['lat'] = None
        result['lon'] = None

    return result


def parse_network(cidr: str):
    '''
    Parse a CIDR string into an ip_network object

    Parameters:
        cidr (str): network in CIDR notation

    Returns:
        ip_network: parsed network, or None when unparseable
    '''
    network = None
    try:
        network = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except (ValueError, TypeError) as exc:
        logger.debug('Could not parse network %r: %s', cidr, exc)
    return network


def classify_subnet(record: dict, config: ProjectConfig) -> str:
    '''
    Decide which Kentik address classification bucket a subnet belongs to

    An explicit classification EA/tag wins. Otherwise small prefixes, link
    local and loopback space, and subnets whose name or comment matches an
    infrastructure pattern are treated as infrastructure; everything else is
    user access. 'other' is only ever set explicitly.

    Parameters:
        record (dict): normalised source subnet record
        config (ProjectConfig): assembled configuration

    Returns:
        str: one of the CLASSIFICATIONS values
    '''
    classification = ''
    override = normalise_site_name(record.get('class_override', '')).lower()

    if override:
        aliases = {
            'infrastructure': CLASS_INFRASTRUCTURE,
            'infra': CLASS_INFRASTRUCTURE,
            'infrastructurenetworks': CLASS_INFRASTRUCTURE,
            'user_access': CLASS_USER_ACCESS,
            'user access': CLASS_USER_ACCESS,
            'user': CLASS_USER_ACCESS,
            'access': CLASS_USER_ACCESS,
            'useraccessnetworks': CLASS_USER_ACCESS,
            'other': CLASS_OTHER,
            'othernetworks': CLASS_OTHER,
        }
        classification = aliases.get(override, '')
        if not classification:
            logger.warning('Unrecognised classification %r on %s, using heuristic',
                           override, record.get('cidr'))

    if not classification:
        network = parse_network(record.get('cidr', ''))
        text = f"{record.get('name', '')} {record.get('comment', '')}".lower()
        infra = False

        if network is not None:
            if network.prefixlen >= config.site.infra_prefix_len:
                infra = True
            for always in ALWAYS_INFRA:
                reserved = ipaddress.ip_network(always)
                if network.version == reserved.version and network.subnet_of(reserved):
                    infra = True

        for pattern in config.site.infra_patterns:
            if pattern and pattern.lower() in text:
                infra = True

        classification = CLASS_INFRASTRUCTURE if infra else CLASS_USER_ACCESS

    return classification


def derive_site_type(record: dict, config: ProjectConfig) -> str:
    '''
    Map a source site type EA/tag value onto the Kentik site type enum

    Parameters:
        record (dict): normalised source subnet record
        config (ProjectConfig): assembled configuration

    Returns:
        str: a Kentik SITE_TYPE_* value
    '''
    site_type = config.site.default_site_type
    raw = normalise_site_name(record.get('site_type', '')).lower()

    if raw:
        if raw.upper().startswith('SITE_TYPE_'):
            site_type = raw.upper()
        elif raw in config.site.site_type_map:
            site_type = config.site.site_type_map[raw]
        else:
            for key, value in config.site.site_type_map.items():
                if key in raw:
                    site_type = value
                    break

    return site_type


def collapse(networks: list) -> list:
    '''
    Exact collapse of a list of networks, per address family

    collapse_addresses only merges contained or adjacent networks, so the
    result covers exactly the same address space as the input.

    Parameters:
        networks (list): list of ip_network objects

    Returns:
        list: collapsed ip_network objects, sorted by version then address
    '''
    result = []
    for version in (4, 6):
        family = [n for n in networks if n.version == version]
        if family:
            result.extend(sorted(ipaddress.collapse_addresses(family)))
    return result


def aggregate(networks: list, max_prefix_len: int, foreign: list) -> tuple:
    '''
    Optionally aggregate collapsed networks up to a prefix length floor

    Aggregation is lossy - the supernet covers address space that was not in
    the source data - so a candidate supernet is only accepted when it does
    not overlap any network belonging to another site.

    Parameters:
        networks (list): collapsed ip_network objects for one site/bucket
        max_prefix_len (int): prefix length floor, 0 disables aggregation
        foreign (list): ip_network objects belonging to other sites

    Returns:
        tuple: (list of ip_network, bool indicating aggregation was refused)
    '''
    result = list(networks)
    refused = False

    if max_prefix_len:
        candidates = []
        for network in networks:
            if network.prefixlen > max_prefix_len:
                supernet = network.supernet(new_prefix=max_prefix_len)
                clash = False
                for other in foreign:
                    if other.version == supernet.version and other.overlaps(supernet):
                        clash = True
                        break
                if clash:
                    refused = True
                    candidates.append(network)
                else:
                    candidates.append(supernet)
            else:
                candidates.append(network)
        result = collapse(candidates)

    return result, refused


def build_subnets(records: list, config: ProjectConfig, foreign: list) -> tuple:
    '''
    Turn a site's source subnet records into summarised SiteSubnet objects

    Parameters:
        records (list): normalised source subnet records for one site
        config (ProjectConfig): assembled configuration
        foreign (list): ip_network objects belonging to other sites

    Returns:
        tuple: (list of SiteSubnet, list of (category, message) notes)
    '''
    subnets = []
    notes = []
    buckets = {c: [] for c in CLASSIFICATIONS}

    for record in records:
        network = parse_network(record.get('cidr', ''))
        if network is None:
            notes.append(('unparseable_subnet',
                          f"Ignored unparseable network {record.get('cidr')!r}"))
            continue
        buckets[classify_subnet(record, config)].append((network, record))

    for classification in CLASSIFICATIONS:
        members = buckets[classification]
        if not members:
            continue
        sources = [n for n, _ in members]
        summarised, refused = aggregate(collapse(sources),
                                        config.site.max_prefix_len,
                                        foreign)
        if refused:
            notes.append(('aggregation_refused',
                          f'Aggregation to /{config.site.max_prefix_len} would have '
                          f'overlapped another site in the {classification} bucket, '
                          f'kept exact prefixes'))

        for network in summarised:
            covered = tuple(str(n) for n in sources
                            if n.version == network.version and n.subnet_of(network))
            comments = [r.get('comment', '') for n, r in members
                        if n.version == network.version and n.subnet_of(network)
                        and r.get('comment')]
            subnets.append(SiteSubnet(cidr=str(network),
                                      classification=classification,
                                      source_cidrs=covered,
                                      comment=comments[0] if len(comments) == 1 else ''))

    return subnets, notes


def build_sites(records: list, config: ProjectConfig) -> tuple:
    '''
    Group normalised subnet records into sites and summarise them

    Parameters:
        records (list): normalised source subnet records
        config (ProjectConfig): assembled configuration

    Returns:
        tuple: (list of Site, list of (category, message, detail) warnings)
    '''
    warnings = []
    grouped = {}
    variants = {}
    unattributed = []

    for record in records:
        name = normalise_site_name(record.get('site', ''))
        if not name:
            unattributed.append(record.get('cidr', ''))
            continue
        key = site_match_key(name)
        grouped.setdefault(key, []).append(record)
        variants.setdefault(key, set()).add(name)

    if unattributed:
        warnings.append(('unattributed_subnets',
                         f'{len(unattributed)} subnet(s) have no value for '
                         f'{config.site.site_key!r} and were skipped',
                         ', '.join(unattributed[:20])))

    for key, names in variants.items():
        if len(names) > 1:
            warnings.append(('site_name_variants',
                             f'{len(names)} spellings of the same site were merged',
                             ' | '.join(sorted(names))))

    # Networks per site key, used for the cross-site aggregation safety check
    site_networks = {}
    for key, members in grouped.items():
        nets = [parse_network(r.get('cidr', '')) for r in members]
        site_networks[key] = [n for n in nets if n is not None]

    sites = []
    for key in sorted(grouped):
        name = sorted(variants[key])[0]
        if not matches_filter(name, config.site.site_filter):
            logger.debug('Site %s excluded by filter %s', name, config.site.site_filter)
            continue

        foreign = []
        for other_key, networks in site_networks.items():
            if other_key != key:
                foreign.extend(networks)

        subnets, notes = build_subnets(grouped[key], config, foreign)
        for category, message in notes:
            warnings.append((category, f'{name}: {message}', ''))

        address = extract_address(grouped[key], config)
        for category, message, detail in address['notes']:
            warnings.append((category, f'{name}: {message}', detail))

        site = Site(name=name,
                    subnets=subnets,
                    site_type=derive_site_type(grouped[key][0], config),
                    postal=address['postal'],
                    lat=address['lat'],
                    lon=address['lon'],
                    address_source=address['source'])
        sites.append(site)
        logger.info('Site %s: %d source subnet(s) summarised to %d prefix(es)',
                    name, len(grouped[key]), len(subnets))

    overlaps = find_cross_site_overlaps(sites)
    for detail in overlaps:
        warnings.append(('cross_site_overlap',
                         'Summarised prefixes from different sites overlap',
                         detail))

    return sites, warnings


def find_cross_site_overlaps(sites: list) -> list:
    '''
    Report summarised prefixes that overlap between two sites

    Parameters:
        sites (list): list of Site objects

    Returns:
        list: human readable overlap descriptions
    '''
    overlaps = []
    flat = []
    for site in sites:
        for subnet in site.subnets:
            network = parse_network(subnet.cidr)
            if network is not None:
                flat.append((site.name, network))

    for index, (name, network) in enumerate(flat):
        for other_name, other in flat[index + 1:]:
            if name == other_name or other.version != network.version:
                continue
            if network.overlaps(other):
                overlaps.append(f'{name} {network} overlaps {other_name} {other}')

    return overlaps

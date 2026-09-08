#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Source adapter interface and shared helpers

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

import logging
from ibx_kentik_prepop.model import ROLE_FIREWALL, ROLE_OTHER, ROLE_ROUTER, ROLE_SWITCH

logger = logging.getLogger(__name__)


# Substrings used to map a discovered device type/model onto a role
ROLE_PATTERNS = (
    (ROLE_FIREWALL, ('firewall', 'fortigate', 'palo alto', 'panos', 'asa',
                     'checkpoint', 'check point', 'srx', 'sonicwall')),
    (ROLE_ROUTER, ('router', 'gateway', 'isr', 'asr', 'mx', 'edge')),
    (ROLE_SWITCH, ('switch', 'catalyst', 'nexus', 'ex4', 'qfx', 'aruba')),
)


class SiteSource:
    '''
    Interface implemented by every Infoblox source adapter

    Adapters normalise platform data into plain dicts so that the planning and
    summarisation code never sees NIOS or UDDI specifics.

    A normalised subnet record has the keys:
        cidr (str), site (str), class_override (str), site_type (str),
        name (str), comment (str), gateways (list), source_id (str)

    get_devices() returns Device objects from ibx_kentik_prepop.model.
    '''

    name = 'base'

    def __init__(self, config) -> None:
        '''
        Store configuration

        Parameters:
            config (ProjectConfig): assembled configuration

        Returns:
            None
        '''
        self.config = config
        return

    def get_subnets(self) -> list:
        '''
        Retrieve normalised subnet records

        Returns:
            list: list of normalised subnet record dicts
        '''
        raise NotImplementedError

    def get_devices(self, subnets: list = None) -> list:
        '''
        Retrieve device candidates

        Parameters:
            subnets (list): normalised subnet records, for site attribution

        Returns:
            list: list of Device objects
        '''
        raise NotImplementedError

    def get_keys(self) -> dict:
        '''
        Retrieve the available EA/tag keys and how many objects carry each

        Returns:
            dict: key name -> count of objects with a non-empty value
        '''
        raise NotImplementedError


def role_from_text(*values) -> str:
    '''
    Infer a device role from discovery text (type, model, description)

    Parameters:
        values: any number of strings to search

    Returns:
        str: one of the ROLE_* values
    '''
    text = ' '.join(str(v or '') for v in values).lower()
    role = ROLE_OTHER
    for candidate, patterns in ROLE_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                role = candidate
                break
        if role != ROLE_OTHER:
            break
    return role


def site_for_ip(address: str, subnets: list) -> str:
    '''
    Find the site of the most specific subnet containing an address

    Parameters:
        address (str): IP address
        subnets (list): normalised subnet records

    Returns:
        str: site value, empty string when no subnet contains the address
    '''
    import ipaddress
    site = ''
    best = -1

    try:
        ip = ipaddress.ip_address(str(address).strip())
    except (ValueError, TypeError):
        ip = None

    if ip is not None:
        for record in subnets:
            try:
                network = ipaddress.ip_network(record.get('cidr', ''), strict=False)
            except (ValueError, TypeError):
                continue
            if network.version == ip.version and ip in network:
                if network.prefixlen > best:
                    best = network.prefixlen
                    site = record.get('site', '')

    return site


def count_keys(records: list, key_field: str) -> dict:
    '''
    Count how many records carry each key in a metadata dict field

    Parameters:
        records (list): raw source objects
        key_field (str): name of the dict field holding metadata

    Returns:
        dict: key name -> count of records with a non-empty value
    '''
    counts = {}
    for record in records:
        metadata = record.get(key_field) or {}
        if not isinstance(metadata, dict):
            continue
        for key, value in metadata.items():
            if isinstance(value, dict):
                value = value.get('value', '')
            if value not in (None, '', [], {}):
                counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

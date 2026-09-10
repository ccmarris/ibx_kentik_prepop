#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Universal DDI (UDDI) IPAM source adapter

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
import requests
from ibx_kentik_prepop.model import Device, ROLE_ROUTER
from ibx_kentik_prepop.sources.base import SiteSource, count_keys, site_for_ip

logger = logging.getLogger(__name__)


# DHCP option names that carry the default gateway
ROUTER_OPTION_NAMES = ('routers', 'router')
OPTION_CODE_LIST = '/api/ddi/v1/dhcp/option_code'


class UDDI(SiteSource):
    '''
    Read subnets, tags and DHCP router options from Universal DDI IPAM
    '''

    name = 'uddi'

    def __init__(self, config) -> None:
        '''
        Initialise an authenticated session against the Infoblox Portal

        Parameters:
            config (ProjectConfig): assembled configuration

        Returns:
            None
        '''
        super().__init__(config)
        self.base_url = config.uddi.base_url
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Token {config.uddi.api_key}',
            'Content-Type': 'application/json',
        })
        self.session.verify = config.uddi.verify_ssl
        self.timeout = config.uddi.timeout_seconds
        self._option_codes = None
        logger.debug('UDDI source initialised against %s', self.base_url)
        return

    def paginate(self, path: str, params: dict = None) -> list:
        '''
        Retrieve every page of a UDDI list endpoint

        Parameters:
            path (str): API path, e.g. /api/ddi/v1/ipam/subnet
            params (dict): additional query parameters

        Returns:
            list: accumulated results, empty list on failure
        '''
        results = []
        query = dict(params or {})
        query[self.config.uddi.page_size_param] = self.config.uddi.page_size
        url = f'{self.base_url}{path}'
        token = None
        page = 0

        while True:
            if token:
                query[self.config.uddi.page_token_param] = token
            logger.debug('GET %s params=%s', url, query)
            try:
                response = self.session.get(url, params=query, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                logger.error('UDDI request to %s failed: %s', url, exc)
                results = []
                break

            payload = response.json()
            batch = payload.get('results', payload.get('data', []))
            results.extend(batch)
            page += 1
            logger.debug('Page %d returned %d object(s)', page, len(batch))
            token = (payload.get(self.config.uddi.next_token_key)
                     or (payload.get('pagination') or {}).get(self.config.uddi.next_token_key))
            if not token or not batch:
                break

        logger.info('Retrieved %d object(s) from %s', len(results), path)
        return results

    def _tag_value(self, tags: dict, key: str) -> str:
        '''
        Read a tag value, tolerating case differences in the key

        Parameters:
            tags (dict): tag dictionary from the API
            key (str): tag key to read

        Returns:
            str: tag value, empty string when absent
        '''
        value = ''
        if key and isinstance(tags, dict):
            if key in tags:
                value = tags[key]
            else:
                for tag_key, tag_value in tags.items():
                    if tag_key.casefold() == key.casefold():
                        value = tag_value
                        break
        return '' if value is None else str(value)

    def option_code_names(self) -> dict:
        '''
        Build a map of DHCP option code id to option name

        Returns:
            dict: option code id -> name, empty when the lookup fails
        '''
        if self._option_codes is None:
            codes = {}
            for code in self.paginate(OPTION_CODE_LIST):
                if code.get('id'):
                    codes[code['id']] = str(code.get('name', ''))
            self._option_codes = codes
            logger.debug('Cached %d DHCP option code name(s)', len(codes))
        return self._option_codes

    def _gateways(self, obj: dict) -> list:
        '''
        Extract default gateway addresses from a subnet's DHCP options

        Parameters:
            obj (dict): raw subnet object

        Returns:
            list: gateway addresses found on the routers option
        '''
        gateways = []
        options = obj.get('dhcp_options') or []
        if options:
            names = self.option_code_names()
            for option in options:
                name = str(option.get('option_code_name')
                           or names.get(option.get('option_code', ''), '')).lower()
                if name in ROUTER_OPTION_NAMES:
                    for value in str(option.get('option_value', '')).split(','):
                        if value.strip():
                            gateways.append(value.strip())
        return gateways

    def _normalise(self, obj: dict) -> dict:
        '''
        Convert a raw UDDI subnet or address block into a normalised record

        Parameters:
            obj (dict): raw API object

        Returns:
            dict: normalised subnet record
        '''
        tags = obj.get('tags') or {}
        prefix = obj.get('cidr')
        address = obj.get('address', '')
        cidr = f'{address}/{prefix}' if prefix is not None else address
        return {
            'cidr': cidr,
            'site': self._tag_value(tags, self.config.site.site_key),
            'class_override': self._tag_value(tags, self.config.site.class_key),
            'site_type': self._tag_value(tags, self.config.site.site_type_key),
            'name': str(obj.get('name', '')),
            'comment': str(obj.get('comment', '')),
            'gateways': self._gateways(obj),
            'source_id': str(obj.get('id', '')),
            'tags': tags,
        }

    def get_subnets(self) -> list:
        '''
        Retrieve normalised subnet records from UDDI IPAM

        Returns:
            list: list of normalised subnet record dicts
        '''
        params = {}
        if self.config.uddi.ip_space:
            params['_filter'] = f"space=='{self.config.uddi.ip_space}'"

        raw = self.paginate(self.config.uddi.subnet_list, params)
        if self.config.site.include_address_blocks:
            raw.extend(self.paginate(self.config.uddi.address_block_list, params))

        records = [self._normalise(obj) for obj in raw]
        logger.info('Normalised %d UDDI subnet record(s)', len(records))
        return records

    def get_keys(self) -> dict:
        '''
        Retrieve the tag keys in use on subnets with populated counts

        Returns:
            dict: tag key -> count of subnets carrying a value
        '''
        raw = self.paginate(self.config.uddi.subnet_list)
        return count_keys(raw, 'tags')

    def get_devices(self, subnets: list = None) -> list:
        '''
        Infer router candidates from the DHCP routers option on each subnet

        Discovered device inventory comes from the Network Insight or UAI
        adapters; this is the fallback when neither is available.

        Parameters:
            subnets (list): normalised subnet records

        Returns:
            list: list of Device objects with origin 'default_gateway'
        '''
        devices = {}
        for record in subnets or []:
            for gateway in record.get('gateways', []):
                if gateway in devices:
                    continue
                devices[gateway] = Device(
                    name=gateway.replace('.', '_').replace(':', '_'),
                    mgmt_ip=gateway,
                    role=ROLE_ROUTER,
                    description=(f"Default gateway for "
                                 f"{record.get('cidr', '')}"
                                 f"{' - ' + record['comment'] if record.get('comment') else ''}"),
                    site_name=record.get('site', '') or site_for_ip(gateway, subnets or []),
                    site_match='subnet',
                    interfaces=[{'name': 'gateway', 'address': gateway,
                                 'description': record.get('cidr', ''),
                                 'speed': '', 'type': ''}],
                    origin='default_gateway',
                    raw={'subnet': record.get('cidr', '')},
                )
        logger.info('Inferred %d gateway device candidate(s)', len(devices))
        return list(devices.values())

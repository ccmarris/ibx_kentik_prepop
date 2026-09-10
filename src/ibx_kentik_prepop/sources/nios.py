#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    NIOS WAPI IPAM source adapter

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
import urllib3
import requests
from ibx_kentik_prepop.model import Device, ROLE_ROUTER
from ibx_kentik_prepop.sources.base import SiteSource, count_keys, site_for_ip

logger = logging.getLogger(__name__)


NETWORK_FIELDS = 'extattrs,comment,options,network_view,members'
CONTAINER_FIELDS = 'extattrs,comment,network_view'
ROUTER_OPTION_NAMES = ('routers', 'router')


class NIOS(SiteSource):
    '''
    Read networks, Extensible Attributes and DHCP router options from NIOS
    '''

    name = 'nios'

    def __init__(self, config) -> None:
        '''
        Initialise an authenticated WAPI session against the grid master

        Parameters:
            config (ProjectConfig): assembled configuration

        Returns:
            None
        '''
        super().__init__(config)
        self.wapi_url = (f'https://{config.nios.gm}/wapi/'
                         f'{config.nios.wapi_version}')
        self.session = requests.Session()
        self.session.auth = (config.nios.user, config.nios.password)
        self.session.verify = config.nios.valid_cert
        if not config.nios.valid_cert:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.timeout = config.nios.timeout_seconds
        logger.debug('NIOS source initialised against %s', self.wapi_url)
        return

    def get_all(self, objtype: str, return_fields: str = '', params: dict = None) -> list:
        '''
        Retrieve every page of a WAPI object type

        Parameters:
            objtype (str): WAPI object type, e.g. 'network'
            return_fields (str): comma separated additional return fields
            params (dict): additional query parameters

        Returns:
            list: accumulated objects, empty list on failure
        '''
        results = []
        query = dict(params or {})
        query.update({
            '_paging': 1,
            '_return_as_object': 1,
            '_max_results': self.config.nios.page_size,
        })
        if return_fields:
            query['_return_fields+'] = return_fields
        if self.config.nios.network_view:
            query.setdefault('network_view', self.config.nios.network_view)

        url = f'{self.wapi_url}/{objtype}'
        page_id = None
        page = 0

        while True:
            if page_id:
                query['_page_id'] = page_id
            logger.debug('GET %s params=%s', url, query)
            try:
                response = self.session.get(url, params=query, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                logger.error('NIOS request to %s failed: %s', url, exc)
                results = []
                break

            payload = response.json()
            batch = payload.get('result', []) if isinstance(payload, dict) else payload
            results.extend(batch)
            page += 1
            logger.debug('Page %d returned %d object(s)', page, len(batch))
            page_id = payload.get('next_page_id') if isinstance(payload, dict) else None
            if not page_id:
                break

        logger.info('Retrieved %d %s object(s) from NIOS', len(results), objtype)
        return results

    @staticmethod
    def _ea_value(extattrs: dict, key: str) -> str:
        '''
        Read an Extensible Attribute value, tolerating case differences

        Parameters:
            extattrs (dict): the extattrs dict from WAPI
            key (str): EA name

        Returns:
            str: EA value, empty string when absent
        '''
        value = ''
        if key and isinstance(extattrs, dict):
            entry = extattrs.get(key)
            if entry is None:
                for ea_key, ea_entry in extattrs.items():
                    if ea_key.casefold() == key.casefold():
                        entry = ea_entry
                        break
            if isinstance(entry, dict):
                value = entry.get('value', '')
            elif entry is not None:
                value = entry
        if isinstance(value, list):
            value = ','.join(str(v) for v in value)
        return '' if value is None else str(value)

    @staticmethod
    def _gateways(obj: dict) -> list:
        '''
        Extract default gateway addresses from a network's DHCP options

        Parameters:
            obj (dict): raw network object

        Returns:
            list: gateway addresses found on the routers option
        '''
        gateways = []
        for option in obj.get('options') or []:
            if str(option.get('name', '')).lower() in ROUTER_OPTION_NAMES:
                for value in str(option.get('value', '')).split(','):
                    if value.strip():
                        gateways.append(value.strip())
        return gateways

    def _normalise(self, obj: dict) -> dict:
        '''
        Convert a raw NIOS network into a normalised record

        Parameters:
            obj (dict): raw WAPI object

        Returns:
            dict: normalised subnet record
        '''
        extattrs = obj.get('extattrs') or {}
        return {
            'cidr': str(obj.get('network', '')),
            'site': self._ea_value(extattrs, self.config.site.site_key),
            'class_override': self._ea_value(extattrs, self.config.site.class_key),
            'site_type': self._ea_value(extattrs, self.config.site.site_type_key),
            'name': self._ea_value(extattrs, 'Name'),
            'comment': str(obj.get('comment', '')),
            'gateways': self._gateways(obj),
            'source_id': str(obj.get('_ref', '')),
            'tags': {k: self._ea_value(extattrs, k) for k in extattrs},
        }

    def get_subnets(self) -> list:
        '''
        Retrieve normalised network records from NIOS IPAM

        Returns:
            list: list of normalised subnet record dicts
        '''
        raw = self.get_all('network', NETWORK_FIELDS)
        if self.config.site.include_address_blocks:
            raw.extend(self.get_all('networkcontainer', CONTAINER_FIELDS))

        records = [self._normalise(obj) for obj in raw]
        logger.info('Normalised %d NIOS network record(s)', len(records))
        return records

    def get_keys(self) -> dict:
        '''
        Retrieve the EA names in use on networks with populated counts

        Returns:
            dict: EA name -> count of networks carrying a value
        '''
        raw = self.get_all('network', 'extattrs')
        return count_keys(raw, 'extattrs')

    def get_devices(self, subnets: list = None) -> list:
        '''
        Infer router candidates from the DHCP routers option on each network

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
                    raw={'network': record.get('cidr', '')},
                )
        logger.info('Inferred %d gateway device candidate(s)', len(devices))
        return list(devices.values())

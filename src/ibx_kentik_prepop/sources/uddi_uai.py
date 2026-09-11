#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Universal Asset Insights (UAI) device source adapter

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
from ibx_kentik_prepop.model import Device
from ibx_kentik_prepop.sources.base import describe, match_site, role_from_text
from ibx_kentik_prepop.sources.uddi import UDDI

logger = logging.getLogger(__name__)


# Asset search projection. The API rejects an 'asset.' prefix on field names,
# so bare normalised names are used.
#
# CORE is the set confirmed in production use. EXTRA is everything that makes
# the report richer but is not worth losing the whole search over: a tenant
# that does not know one of these names rejects the request outright, which
# would otherwise look exactly like "this tenant has no devices". The search
# retries with CORE alone if the full projection is refused.
ASSET_FIELDS_CORE = ('name', 'ip_addresses', 'category', 'providers', 'managed')
ASSET_FIELDS_EXTRA = ('type', 'vendor', 'model', 'os_version', 'location',
                      'description', 'comment', 'tags')
ASSET_FIELDS = ASSET_FIELDS_CORE + ASSET_FIELDS_EXTRA
PAGE_SIZE = 1000


class UAI(UDDI):
    '''
    Read discovered asset inventory from Universal Asset Insights
    '''

    name = 'uai'

    def search_assets(self, category: str = '') -> list:
        '''
        Retrieve assets from the Universal Asset Insights search API

        Issues POST /assets/search with a full sync, a FilterEL filter and a
        field projection, following cursor pagination until exhausted. If the
        full projection is refused, the search is retried with the core field
        set, because losing every device to one unrecognised field name is far
        worse than losing the vendor column.

        Parameters:
            category (str): asset category to match, empty for all

        Returns:
            list: raw asset dicts, empty list on failure
        '''
        assets = self._search(category, ASSET_FIELDS)

        if assets is None and self.last_error:
            logger.warning('Retrying the asset search with the core field set '
                           'after: %s', self.last_error)
            self.reduced_fields = True
            assets = self._search(category, ASSET_FIELDS_CORE)
            if assets is not None:
                self.last_error = (
                    f'The asset search refused the full field projection, so '
                    f'vendor, model, OS and description are unavailable '
                    f'({self.first_error})')

        if assets is None:
            assets = []

        logger.info('Retrieved %d UAI asset(s)', len(assets))
        return assets

    def _search(self, category: str, fields: tuple):
        '''
        One asset search, following pagination to the end

        Parameters:
            category (str): asset category to match, empty for all
            fields (tuple): field names to project

        Returns:
            list: raw asset dicts, or None when the request failed
        '''
        assets = []
        url = f'{self.base_url}{self.config.uddi.asset_search}'
        page_token = None

        while True:
            body = {
                'sync': {'mode': 'FULL'},
                'fields': list(fields),
                'page_size': PAGE_SIZE,
            }
            if category:
                body['filter'] = f'category = "{category}"'
            if page_token:
                body['page_token'] = page_token

            logger.debug('POST %s fields=%d category=%r page_token=%s', url,
                         len(fields), category, page_token)
            try:
                response = self.session.post(url, json=body, timeout=self.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                detail = ''
                status = 0
                if getattr(exc, 'response', None) is not None:
                    status = exc.response.status_code
                    detail = exc.response.text[:300].replace('\n', ' ')
                self.last_error = (f'UAI asset search failed: {status or exc} '
                                   f'{detail}').strip()
                if not self.first_error:
                    self.first_error = self.last_error
                logger.error('%s', self.last_error)
                return None

            payload = response.json()
            data = payload.get('data') or []
            assets.extend(item for item in data if isinstance(item, dict))
            pagination = payload.get('pagination') or {}
            page_token = pagination.get('next_page_token')
            if not page_token or not data:
                break

        return assets

    @staticmethod
    def _addresses(asset: dict) -> list:
        '''
        Every address on an asset, tolerating the shapes the API returns

        Parameters:
            asset (dict): raw asset dict

        Returns:
            list: address strings
        '''
        addresses = asset.get('ip_addresses')
        if isinstance(addresses, str):
            addresses = [a.strip(' "[]') for a in addresses.split(',')]
        found = []
        for candidate in addresses or []:
            if isinstance(candidate, dict):
                candidate = candidate.get('address', '')
            if candidate and str(candidate) not in found:
                found.append(str(candidate))
        return found

    @staticmethod
    def _first_ip(asset: dict) -> str:
        '''
        Pick the first usable address from an asset's ip_addresses

        Parameters:
            asset (dict): raw asset dict

        Returns:
            str: address, empty string when the asset has none
        '''
        address = ''
        addresses = asset.get('ip_addresses')
        if isinstance(addresses, str):
            addresses = [a.strip(' "[]') for a in addresses.split(',')]
        if isinstance(addresses, list):
            for candidate in addresses:
                if isinstance(candidate, dict):
                    candidate = candidate.get('address', '')
                if candidate:
                    address = str(candidate)
                    break
        return address

    def get_devices(self, subnets: list = None) -> list:
        '''
        Retrieve network device assets and attribute them to sites

        Parameters:
            subnets (list): normalised subnet records, for site attribution

        Returns:
            list: list of Device objects with origin 'uai'
        '''
        devices = []
        for asset in self.search_assets(self.config.uddi.asset_category):
            addresses = self._addresses(asset)
            address = addresses[0] if addresses else ''
            role = role_from_text(asset.get('type'), asset.get('model'),
                                  asset.get('category'))
            device = Device(
                name=str(asset.get('name', '') or address),
                mgmt_ip=address,
                role=role,
                vendor=str(asset.get('vendor', '')),
                model=str(asset.get('model', '')),
                os_version=str(asset.get('os_version', '')),
                description=describe(asset, self.config.device.description_keys,
                                     asset.get('tags')),
                interfaces=[{'name': '', 'address': a, 'description': '',
                             'speed': '', 'type': ''} for a in addresses],
                origin='uai',
                raw=asset,
            )
            device.site_name, device.site_match = match_site(
                device, subnets or [], str(asset.get('location', '') or ''))
            devices.append(device)

        logger.info('Normalised %d UAI device candidate(s)', len(devices))
        return devices

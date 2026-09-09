#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Kentik API target - sites (Site API v202211) and devices (v5 admin API)

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
from ibx_kentik_prepop.model import CLASSIFICATIONS, CLASS_TO_KENTIK, Device, Site
from ibx_kentik_prepop.summarise import sanitise_device_name, site_match_key

logger = logging.getLogger(__name__)


# Kentik device subtypes. 'router' covers anything exporting flow via
# NetFlow/IPFIX/sFlow, which is what a pre-populated device will be.
ROLE_TO_SUBTYPE = {
    'router': 'router',
    'switch': 'router',
    'firewall': 'router',
    'other': 'router',
}

DEVICE_WRITE_REFUSED = (
    'Kentik device creation is not enabled in this version. Each device '
    'consumes a licensed device slot, requires a plan_id, and needs the flow '
    'exporter source address (which is not necessarily the discovered '
    'management IP). Review the device section of the report, then create the '
    'devices in Kentik with the payloads shown.'
)


def build_device_payload(config, device: Device, site_id: str = '') -> dict:
    '''
    Build the v5 admin API request body for a device create

    Not submitted by this tool - the payload is emitted in the report and the
    export so it can be reviewed before any licensed device slot is consumed.

    Parameters:
        config (ProjectConfig): assembled configuration
        device (Device): device candidate
        site_id (str): Kentik site id the device belongs to

    Returns:
        dict: request body for POST /api/v5/device
    '''
    body = {
        'device_name': sanitise_device_name(device.name),
        'device_subtype': ROLE_TO_SUBTYPE.get(device.role, 'router'),
        'device_description': ' '.join(v for v in (device.vendor, device.model,
                                                   device.os_version) if v),
        'device_sample_rate': config.device.sample_rate,
        'sending_ips': list(device.sending_ips or ()),
        'minimize_snmp': config.device.minimize_snmp,
    }
    if config.device.plan_id:
        body['plan_id'] = config.device.plan_id
    if site_id:
        body['site_id'] = site_id
    if device.mgmt_ip:
        body['device_snmp_ip'] = device.mgmt_ip
    return {'device': body}


class KENTIK:
    '''
    Read and write Kentik sites, and read devices

    Sites use the Site API v202211 on the gRPC-gateway host; devices use the
    v5 admin API. VERIFY the auth header names against the tenant's API
    documentation on first run - every call depends on them.
    '''

    def __init__(self, config) -> None:
        '''
        Initialise an authenticated session against the Kentik API

        Parameters:
            config (ProjectConfig): assembled configuration

        Returns:
            None
        '''
        self.config = config
        self.kentik = config.kentik
        self.session = requests.Session()
        self.session.headers.update({
            self.kentik.auth_email_header: self.kentik.email,
            self.kentik.auth_token_header: self.kentik.token,
            'Content-Type': 'application/json',
        })
        self.session.verify = self.kentik.verify_ssl
        self.timeout = self.kentik.timeout_seconds
        # Detail of the most recent failed request, for reporting to the caller
        self.last_error = {}
        logger.debug('Kentik target initialised (sites via %s, devices via %s)',
                     self.kentik.grpc_base_url, self.kentik.base_url)
        return

    def _request(self, method: str, url: str, body: dict = None) -> dict:
        '''
        Issue a request and return the parsed JSON body

        On failure the status code and response body are kept in last_error so
        the caller can report what Kentik actually said rather than just None.

        Parameters:
            method (str): HTTP method
            url (str): fully qualified URL
            body (dict): optional JSON body

        Returns:
            dict: parsed response, or None on failure
        '''
        result = None
        self.last_error = {}
        logger.debug('%s %s', method, url)
        try:
            response = self.session.request(method, url, json=body, timeout=self.timeout)
            response.raise_for_status()
            result = response.json() if response.content else {}
        except requests.RequestException as exc:
            status = 0
            detail = ''
            if getattr(exc, 'response', None) is not None:
                status = exc.response.status_code
                detail = exc.response.text[:500]
            self.last_error = {'method': method, 'url': url, 'status': status,
                               'message': str(exc), 'body': detail}
            logger.error('Kentik %s %s failed: %s %s', method, url, exc, detail)
        return result

    def error_text(self) -> str:
        '''
        One-line description of the most recent failure

        Returns:
            str: human readable error, empty string when the last call worked
        '''
        text = ''
        if self.last_error:
            status = self.last_error.get('status') or 'no response'
            body = (self.last_error.get('body') or '').strip().replace('\n', ' ')
            text = f"{status}: {body}" if body else f"{status}: {self.last_error.get('message', '')}"
        return text[:300]

    def get_sites(self) -> list:
        '''
        Retrieve every site from Kentik

        Returns:
            list: raw site dicts, empty list on failure
        '''
        url = f'{self.kentik.grpc_base_url}{self.kentik.site_list}'
        payload = self._request('GET', url)
        sites = []
        if isinstance(payload, dict):
            sites = payload.get('sites') or payload.get('site') or []
        logger.info('Retrieved %d existing Kentik site(s)', len(sites))
        return sites

    def site_index(self, sites: list = None) -> dict:
        '''
        Index existing sites by casefolded title

        Parameters:
            sites (list): raw site dicts, fetched when not supplied

        Returns:
            dict: comparison key -> raw site dict
        '''
        if sites is None:
            sites = self.get_sites()
        index = {}
        for site in sites:
            title = str(site.get('title', ''))
            if title:
                index[site_match_key(title)] = site
        return index

    @staticmethod
    def existing_networks(raw_site: dict) -> dict:
        '''
        Extract the address classification network lists from a Kentik site

        Parameters:
            raw_site (dict): raw site dict from the API

        Returns:
            dict: internal classification -> list of CIDR strings
        '''
        classification = raw_site.get('addressClassification') or {}
        networks = {}
        for internal, kentik_key in CLASS_TO_KENTIK.items():
            values = classification.get(kentik_key) or []
            networks[internal] = [str(v) for v in values if v]
        return networks

    @staticmethod
    def site_payload(site: Site, networks: dict, raw_site: dict = None) -> dict:
        '''
        Build the request body for a site create or update

        Parameters:
            site (Site): derived site
            networks (dict): internal classification -> list of CIDR strings
            raw_site (dict): existing site dict, for preserving unmanaged fields

        Returns:
            dict: request body for POST/PUT /sites
        '''
        classification = {}
        for internal in CLASSIFICATIONS:
            classification[CLASS_TO_KENTIK[internal]] = sorted(networks.get(internal, []))

        body = {
            'title': site.name,
            'type': site.site_type,
            'addressClassification': classification,
        }

        if site.lat is not None:
            body['lat'] = site.lat
        if site.lon is not None:
            body['lon'] = site.lon
        if site.postal:
            body['postalAddress'] = site.postal

        if raw_site:
            for preserve in ('id', 'siteMarket', 'architecture'):
                if raw_site.get(preserve) and preserve not in body:
                    body[preserve] = raw_site[preserve]
            if site.lat is None and raw_site.get('lat') is not None:
                body['lat'] = raw_site['lat']
            if site.lon is None and raw_site.get('lon') is not None:
                body['lon'] = raw_site['lon']
            if not site.postal and raw_site.get('postalAddress'):
                body['postalAddress'] = raw_site['postalAddress']

        return {'site': body}

    def create_site(self, site: Site, networks: dict) -> dict:
        '''
        Create a site in Kentik

        Parameters:
            site (Site): derived site
            networks (dict): internal classification -> list of CIDR strings

        Returns:
            dict: created site dict, or None on failure
        '''
        url = f'{self.kentik.grpc_base_url}{self.kentik.site_create}'
        payload = self._request('POST', url, self.site_payload(site, networks))
        created = None
        if isinstance(payload, dict):
            created = payload.get('site', payload)
            logger.info('Created Kentik site %s (id %s)', site.name,
                        created.get('id', 'unknown'))
        return created

    def update_site(self, site_id: str, site: Site, networks: dict,
                    raw_site: dict = None) -> dict:
        '''
        Update an existing Kentik site

        Parameters:
            site_id (str): Kentik site id
            site (Site): derived site
            networks (dict): internal classification -> list of CIDR strings
            raw_site (dict): existing site dict, for preserving unmanaged fields

        Returns:
            dict: updated site dict, or None on failure
        '''
        path = self.kentik.site_update.format(site_id=site_id)
        url = f'{self.kentik.grpc_base_url}{path}'
        payload = self._request('PUT', url,
                                self.site_payload(site, networks, raw_site))
        updated = None
        if isinstance(payload, dict):
            updated = payload.get('site', payload)
            logger.info('Updated Kentik site %s (id %s)', site.name, site_id)
        return updated

    def get_devices(self) -> list:
        '''
        Retrieve every device from Kentik (v5 admin API)

        Returns:
            list: raw device dicts, empty list on failure
        '''
        url = f'{self.kentik.base_url}{self.kentik.device_list}'
        payload = self._request('GET', url)
        devices = []
        if isinstance(payload, dict):
            devices = payload.get('devices') or []
        logger.info('Retrieved %d existing Kentik device(s)', len(devices))
        return devices

    def device_payload(self, device: Device, site_id: str = '') -> dict:
        '''
        Build the v5 admin API request body for a device create

        Parameters:
            device (Device): device candidate
            site_id (str): Kentik site id the device belongs to

        Returns:
            dict: request body for POST /api/v5/device
        '''
        return build_device_payload(self.config, device, site_id)

    def create_device(self, device: Device, site_id: str = '') -> dict:
        '''
        Refuse to create a device

        Device writes are deliberately not enabled in this version.

        Parameters:
            device (Device): device candidate
            site_id (str): Kentik site id

        Returns:
            dict: never returns, always raises
        '''
        logger.error('Refusing to create device %s: %s', device.name, DEVICE_WRITE_REFUSED)
        raise NotImplementedError(DEVICE_WRITE_REFUSED)

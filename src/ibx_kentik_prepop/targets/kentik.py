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
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import requests
from ibx_kentik_prepop.config import AGENT_SNMP_MODES
from ibx_kentik_prepop.model import (CLASSIFICATIONS, CLASS_TO_KENTIK, Device,
                                     MODE_NMS, Site)
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

# Kentik device names accept 4-60 characters of alphanumerics and underscores;
# creation consumes a licensed device slot from the plan, which is why the
# apply checks remaining capacity before writing.
DEVICE_LICENCE_NOTE = (
    'Each device created consumes a licensed device slot on the selected plan. '
    'For flow devices, sending_ips must be the flow exporter source address - '
    'a device whose sending IP never sends flow will sit idle.'
)


# The device API accepts snake_case on input but answers in lowerCamelCase, so
# every read of a device has to tolerate both spellings.
WRITABLE_DEVICE_FIELDS = (
    'device_name', 'device_subtype', 'cdn_attr', 'device_description',
    'sending_ips', 'device_sample_rate', 'plan_id', 'site_id', 'minimize_snmp',
    'device_snmp_ip', 'device_snmp_community', 'device_snmp_v3_conf',
    'device_bgp_type', 'device_bgp_neighbor_ip', 'device_bgp_neighbor_ip6',
    'device_bgp_neighbor_asn', 'device_bgp_password', 'use_bgp_device_id',
    'device_bgp_flowspec', 'nms', 'monitoring_template_id', 'device_alert',
)

# In DeviceConcise but refused by the API with "Expected a value of type
# `never`", so it must never be echoed back on an update either - a device
# configured in the portal can carry a value this tool cannot resend.
UNWRITABLE_DEVICE_FIELDS = ('flow_snmp_credential_name',)


# Statuses worth trying again. 429 and 503 both mean "I did not process this,
# come back later", which is the only promise safe enough to retry a POST on -
# a create that may already have consumed a licensed device slot must never be
# repeated. The rest are ambiguous on a write but harmless on a read or a PUT.
RETRY_STATUSES = (429, 500, 502, 503, 504)
NOT_PROCESSED_STATUSES = (429, 503)
IDEMPOTENT_METHODS = ('GET', 'HEAD', 'PUT', 'DELETE', 'OPTIONS')
# Longest this client will ever sleep between attempts, whatever Retry-After
# asks for - an operator watching a progress stream should not wait minutes.
MAX_RETRY_WAIT = 30.0


def retry_wait(response, attempt: int, backoff: float) -> float:
    '''
    How long to wait before trying a request again

    Kentik's Retry-After leads when it is present, in either of the forms RFC
    9110 allows; otherwise the wait doubles per attempt. Capped either way.

    Parameters:
        response (requests.Response): the failed response, or None
        attempt (int): zero-based attempt number that just failed
        backoff (float): base delay in seconds

    Returns:
        float: seconds to sleep
    '''
    delay = backoff * (2 ** attempt)
    header = ''
    if response is not None:
        header = str(response.headers.get('Retry-After', '')).strip()

    if header:
        try:
            delay = float(header)
        except ValueError:
            try:
                when = parsedate_to_datetime(header)
            except (TypeError, ValueError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                delay = (when - datetime.now(timezone.utc)).total_seconds()

    return max(0.0, min(delay, MAX_RETRY_WAIT))


def should_retry(method: str, exc, attempt: int, retries: int) -> bool:
    '''
    Decide whether a failed request may be tried again

    A write is only repeated when the server said outright that it did not
    process the request. Anything ambiguous - a timeout, a dropped connection,
    a gateway error - is retried on idempotent methods only, because a device
    create that actually landed would otherwise be repeated and burn a second
    licensed slot.

    Parameters:
        method (str): HTTP method
        exc (requests.RequestException): the failure
        attempt (int): zero-based attempt number that just failed
        retries (int): how many retries are allowed in total

    Returns:
        bool: True when the request should be repeated
    '''
    retry = False
    if attempt < retries:
        response = getattr(exc, 'response', None)
        if response is not None:
            status = response.status_code
            if method.upper() in IDEMPOTENT_METHODS:
                retry = status in RETRY_STATUSES
            else:
                retry = status in NOT_PROCESSED_STATUSES
        elif isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            # No response at all: safe to repeat a read, never a write.
            retry = method.upper() in IDEMPOTENT_METHODS
    return retry


def camel(name: str) -> str:
    '''
    lowerCamelCase form of a snake_case field name

    Parameters:
        name (str): snake_case field name

    Returns:
        str: the camelCase spelling the API answers with
    '''
    head, *rest = name.split('_')
    return head + ''.join(part.title() for part in rest)


def read_field(raw: dict, name: str, default=None):
    '''
    Read a device field regardless of which spelling the API used

    Parameters:
        raw (dict): device dict as returned by the API
        name (str): snake_case field name
        default: value to return when the field is absent

    Returns:
        the field value, or the default
    '''
    if not isinstance(raw, dict):
        return default
    if name in raw:
        return raw[name]
    return raw.get(camel(name), default)


def device_site_id(raw: dict) -> str:
    '''
    The site id a device currently carries

    The read model nests the site as an object, so the flat site_id is not
    always present.

    Parameters:
        raw (dict): device dict as returned by the API

    Returns:
        str: site id, empty string when the device has no site
    '''
    site_id = read_field(raw, 'site_id') or ''
    if not site_id:
        site = read_field(raw, 'site') or {}
        site_id = site.get('id', '') if isinstance(site, dict) else ''
    return str(site_id or '')


def sending_ips_for(config, device: Device) -> list:
    '''
    Work out the flow exporter source addresses for a device

    An explicit per-device selection wins; otherwise the policy is either the
    management address alone (the default) or every discovered address.

    Parameters:
        config (ProjectConfig): assembled configuration
        device (Device): device candidate

    Returns:
        list: addresses to submit as sending_ips
    '''
    chosen = config.device.sending_ip_map.get(device.name)
    if chosen is None:
        chosen = config.device.sending_ip_map.get(sanitise_device_name(device.name))

    if chosen is not None:
        addresses = [a for a in chosen if a]
    elif config.device.sending_ips == 'all':
        addresses = device.addresses()
    else:
        addresses = [device.mgmt_ip] if device.mgmt_ip else []

    return list(dict.fromkeys(addresses))


# Kentik does not document a device_description length; 255 is a defensive cap.
# VERIFY against a tenant if long descriptions matter.
DESCRIPTION_MAX = 255


def device_description(device: Device) -> str:
    '''
    Text for the Kentik device_description field

    A description or comment carried by the source data leads, because someone
    wrote it deliberately; the discovered hardware summary is appended so the
    vendor, model and version are not lost.

    Parameters:
        device (Device): device candidate

    Returns:
        str: description, truncated to what the API is assumed to accept
    '''
    hardware = ' '.join(v for v in (device.vendor, device.model,
                                    device.os_version) if v)
    parts = [p for p in (device.description.strip(), hardware) if p]

    # Don't repeat the hardware summary if the description already says it
    if len(parts) == 2 and hardware.casefold() in parts[0].casefold():
        parts = [parts[0]]

    return ' - '.join(parts)[:DESCRIPTION_MAX]


def build_device_payload(config, device: Device, site_id: str = '') -> dict:
    '''
    Build the device API request body for the configured mode

    Flow devices carry the plan, sample rate and sending IPs; NMS devices carry
    the nms block with the agent and SNMP credential. Both go to the same
    endpoint - only the populated fields differ.
    (Verified against kentik/api-schema-public device/v202504beta2.)

    Parameters:
        config (ProjectConfig): assembled configuration
        device (Device): device candidate
        site_id (str): Kentik site id the device belongs to

    Returns:
        dict: request body for POST /device/v202504beta2/device
    '''
    body = {
        'device_name': sanitise_device_name(device.name),
        'device_description': device_description(device),
        # Required by the API even when BGP is not in use, in which case 'none'
        # means "use generic IP/ASN mapping". device_bgp_flowspec is the
        # boolean that accompanies it.
        'device_bgp_type': config.device.bgp_type or 'none',
        'device_bgp_flowspec': bool(config.device.bgp_flowspec),
    }
    if site_id:
        body['site_id'] = int(site_id) if str(site_id).isdigit() else site_id

    if config.device.bgp_type == 'device':
        if config.device.bgp_neighbor_asn:
            body['device_bgp_neighbor_asn'] = str(config.device.bgp_neighbor_asn)
        if config.device.bgp_neighbor_ip:
            body['device_bgp_neighbor_ip'] = config.device.bgp_neighbor_ip
        if config.device.bgp_neighbor_ip6:
            body['device_bgp_neighbor_ip6'] = config.device.bgp_neighbor_ip6
    elif config.device.bgp_type == 'other_device' and config.device.bgp_device_id:
        body['use_bgp_device_id'] = config.device.bgp_device_id

    if config.device.mode != MODE_NMS:
        body['device_subtype'] = ROLE_TO_SUBTYPE.get(device.role,
                                                     config.device.subtype)
        body['device_sample_rate'] = config.device.sample_rate
        body['sending_ips'] = sending_ips_for(config, device)
        body['minimize_snmp'] = config.device.minimize_snmp
        if config.device.plan_id:
            body['plan_id'] = config.device.plan_id

    # SNMP collection is orthogonal to what the device is for: an agent polling
    # a traffic device is the portal's "Agent-based SNMP for Flow Enrichment".
    #
    # device_snmp_ip and device_snmp_community are the LEGACY configuration -
    # Kentik polling the device itself. Sending either of them alongside the
    # agent block makes the portal show the device as using the legacy method,
    # so in agent modes the poll target is nms.ip_address and nothing else.
    if config.device.snmp_mode in AGENT_SNMP_MODES:
        nms = {'ip_address': device.mgmt_ip}
        if config.device.agent_id:
            nms['agent_id'] = config.device.agent_id
        if config.device.credential_name:
            nms['snmp'] = {'credential_name': config.device.credential_name,
                           'port': config.device.snmp_port}
        body['nms'] = nms

        # flow_snmp_credential_name is NOT the field for this. A tenant
        # rejected it outright:
        #   400 At path: request.device.flow_snmp_credential_name --
        #   Expected a value of type `never`, but received: "marrison-test"
        # A `never` type means the write schema forbids it here, so the agent's
        # credential goes in nms.snmp.credential_name and nowhere else. It is
        # only sent if someone sets it deliberately, and it will very likely be
        # rejected if they do.
        if (config.device.flow_snmp_credential_name
                and config.device.send_flow_snmp_credential):
            body['flow_snmp_credential_name'] = \
                config.device.flow_snmp_credential_name
    elif config.device.snmp_mode == 'community':
        if device.mgmt_ip:
            body['device_snmp_ip'] = device.mgmt_ip
        if config.device.snmp_community:
            body['device_snmp_community'] = config.device.snmp_community

    # The monitoring template governs the full metric set, so it only applies
    # when the agent is doing full monitoring rather than flow enrichment.
    if (config.device.snmp_mode == 'agent-full'
            and config.device.monitoring_template_id):
        body['monitoring_template_id'] = config.device.monitoring_template_id

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
        self.retries = max(int(self.kentik.retries), 0)
        self.retry_backoff = max(float(self.kentik.retry_backoff), 0.0)
        # Detail of the most recent failed request, for reporting to the caller
        self.last_error = {}
        logger.debug('Kentik target initialised (sites via %s, devices via %s)',
                     self.kentik.grpc_base_url, self.kentik.base_url)
        return

    def use_config(self, config) -> 'KENTIK':
        '''
        Adopt an updated configuration

        Needed because the plan id is only known after the plan has been
        resolved against the account, and every device payload built by this
        client has to carry it.

        Parameters:
            config (ProjectConfig): the updated configuration

        Returns:
            KENTIK: self, for chaining
        '''
        self.config = config
        self.kentik = config.kentik
        return self

    def _request(self, method: str, url: str, body: dict = None) -> dict:
        '''
        Issue a request and return the parsed JSON body

        Rate limits and transient server errors are retried with a backoff that
        honours Retry-After. Writes are only repeated when Kentik said outright
        that it had not processed them - see should_retry.

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
        attempt = 0

        while True:
            logger.debug('%s %s (attempt %d)', method, url, attempt + 1)
            try:
                response = self.session.request(method, url, json=body,
                                                timeout=self.timeout)
                response.raise_for_status()
                try:
                    result = response.json() if response.content else {}
                except ValueError as exc:
                    # A 200 carrying something that is not JSON is a proxy or a
                    # captive portal, not Kentik - say so rather than crashing.
                    self.last_error = {
                        'method': method, 'url': url,
                        'status': response.status_code,
                        'message': f'response was not JSON: {exc}',
                        'body': response.text[:500]}
                    logger.error('Kentik %s %s returned non-JSON: %s',
                                 method, url, response.text[:200])
                break
            except requests.RequestException as exc:
                if should_retry(method, exc, attempt, self.retries):
                    wait = retry_wait(getattr(exc, 'response', None), attempt,
                                      self.retry_backoff)
                    logger.warning('Kentik %s %s failed (%s), retrying in %.1fs '
                                   '(attempt %d of %d)', method, url, exc, wait,
                                   attempt + 2, self.retries + 1)
                    time.sleep(wait)
                    attempt += 1
                    continue

                status = 0
                detail = ''
                if getattr(exc, 'response', None) is not None:
                    status = exc.response.status_code
                    detail = exc.response.text[:500]
                self.last_error = {'method': method, 'url': url, 'status': status,
                                   'message': str(exc), 'body': detail,
                                   'attempts': attempt + 1}
                logger.error('Kentik %s %s failed after %d attempt(s): %s %s',
                             method, url, attempt + 1, exc, detail)
                break

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
        Retrieve every device from Kentik

        Returns:
            list: raw device dicts, empty list on failure
        '''
        url = f'{self.kentik.grpc_base_url}{self.kentik.device_list}'
        payload = self._request('GET', url)
        devices = []
        if isinstance(payload, dict):
            devices = payload.get('devices') or []
        logger.info('Retrieved %d existing Kentik device(s)', len(devices))
        return devices

    def device_index(self, devices: list = None) -> dict:
        '''
        Index existing devices by their Kentik device name

        Parameters:
            devices (list): raw device dicts, fetched when not supplied

        Returns:
            dict: casefolded device_name -> raw device dict
        '''
        if devices is None:
            devices = self.get_devices()
        index = {}
        for device in devices:
            name = str(read_field(device, 'device_name') or '')
            if name:
                index[name.casefold()] = device
        return index

    def create_device(self, device: Device, site_id: str = '') -> dict:
        '''
        Create a device in Kentik

        Parameters:
            device (Device): device candidate
            site_id (str): Kentik site id the device belongs to

        Returns:
            dict: created device dict, or None on failure
        '''
        url = f'{self.kentik.grpc_base_url}{self.kentik.device_create}'
        body = build_device_payload(self.config, device, site_id)
        payload = self._request('POST', url, body)
        created = None
        if isinstance(payload, dict):
            created = payload.get('device', payload)
            logger.info('Created Kentik device %s (id %s)',
                        body['device']['device_name'], created.get('id', 'unknown'))
        return created

    def get_device(self, device_id: str) -> dict:
        '''
        Read one device from Kentik

        Parameters:
            device_id (str): Kentik device id

        Returns:
            dict: raw device dict, or None on failure
        '''
        path = self.kentik.device_read.format(device_id=device_id)
        url = f'{self.kentik.grpc_base_url}{path}'
        payload = self._request('GET', url)
        device = None
        if isinstance(payload, dict):
            device = payload.get('device', payload)
        return device

    def get_device_by_name(self, device_name: str) -> dict:
        '''
        Read one device from Kentik by its name

        Useful for settling what the portal writes: configure a device by hand,
        then read it back and compare with what this tool would send.

        Parameters:
            device_name (str): the Kentik device name

        Returns:
            dict: raw device dict, or None when not found
        '''
        url = (f'{self.kentik.grpc_base_url}{self.kentik.device_list}'
               f'/name/{device_name}')
        payload = self._request('GET', url)
        device = None
        if isinstance(payload, dict):
            device = payload.get('device', payload)
        return device

    def update_device_placement(self, raw_device: dict, device: Device,
                                site_id: str = '') -> dict:
        '''
        Update only the site and sending IPs of an existing device

        Read-modify-write: the device as Kentik holds it is the base, and just
        the two fields this tool derives are replaced, so monitoring
        configuration it knows nothing about survives the PUT.

        Parameters:
            raw_device (dict): the device as returned by Kentik
            device (Device): the derived device candidate
            site_id (str): Kentik site id the device belongs to

        Returns:
            dict: updated device dict, or None on failure
        '''
        device_id = str(read_field(raw_device, 'id') or '')

        # Only the fields the API accepts on a write are echoed back. A
        # blacklist is not safe here: the read model carries plan, site,
        # interfaces and a huge custom_columns string, none of which belong in
        # an update, and it names them in camelCase.
        body = {}
        for field in WRITABLE_DEVICE_FIELDS:
            value = read_field(raw_device, field)
            if value not in (None, ''):
                body[field] = value
        body['site_id'] = (int(site_id) if str(site_id).isdigit() else site_id) \
            if site_id else device_site_id(raw_device)
        addresses = sending_ips_for(self.config, device)
        if addresses:
            body['sending_ips'] = addresses

        path = self.kentik.device_update.format(device_id=device_id)
        url = f'{self.kentik.grpc_base_url}{path}'
        payload = self._request('PUT', url, {'device': body})
        updated = None
        if isinstance(payload, dict):
            updated = payload.get('device', payload)
            logger.info('Updated Kentik device %s (id %s)',
                        body.get('device_name', device.name), device_id)
        return updated

    def list_plans(self) -> list:
        '''
        Retrieve the licence plans available to this account

        Returns:
            list: raw plan dicts, empty list on failure
        '''
        url = f'{self.kentik.base_url}{self.kentik.plan_list}'
        payload = self._request('GET', url)
        plans = []
        if isinstance(payload, dict):
            plans = payload.get('plans') or []
        elif isinstance(payload, list):
            plans = payload
        logger.info('Retrieved %d Kentik plan(s)', len(plans))
        return plans

    @staticmethod
    def plan_capacity(plan: dict) -> dict:
        '''
        Device capacity of a plan

        Parameters:
            plan (dict): raw plan dict

        Returns:
            dict: id, name, max_devices, used and remaining
        '''
        max_devices = plan.get('max_devices')
        used = len(plan.get('devices') or [])
        remaining = None
        if isinstance(max_devices, int):
            remaining = max(max_devices - used, 0)
        return {'id': plan.get('id'), 'name': plan.get('name', ''),
                'max_devices': max_devices, 'used': used,
                'remaining': remaining, 'active': plan.get('active', True)}

    def resolve_plan(self, name: str = '', plan_id: int = 0,
                     plans: list = None) -> tuple:
        '''
        Find the plan to create flow devices under

        Resolution order: an explicit id, then the configured name matched
        case-insensitively with underscores and spaces treated as equivalent
        (so 'Free Flowpak Plan', 'free_flowpak_plan' and 'Free Flowpak plan'
        all hit), then a plan whose name contains the configured one or vice
        versa (so 'Free Flowpak' still matches), then any active plan whose
        name mentions 'free', then the first active plan. Anything other than
        an exact hit is reported - the plan decides what the devices cost, so a
        silent substitution is not acceptable.

        An id that the API does not list is still honoured: licensing is not
        visible to every service account, and the operator may know the id even
        when this tool cannot enumerate it.

        Parameters:
            name (str): plan name to look for
            plan_id (int): explicit plan id, overrides the name
            plans (list): raw plan dicts, fetched when not supplied

        Returns:
            tuple: (capacity dict or None, warning message)
        '''
        if plans is None:
            plans = self.list_plans()

        def key(value):
            return str(value or '').replace('_', ' ').strip().casefold()

        chosen = None
        warning = ''

        if plan_id:
            chosen = next((p for p in plans if str(p.get('id')) == str(plan_id)), None)
            if chosen is None:
                logger.info('Plan id %s is not listed by the API, using it as given',
                            plan_id)
                return ({'id': plan_id, 'name': f'id {plan_id} (entered)',
                         'max_devices': None, 'used': 0, 'remaining': None,
                         'active': True},
                        'Plan id was entered manually and could not be confirmed '
                        'against the API, so remaining capacity is unknown')
        elif name:
            chosen = next((p for p in plans if key(p.get('name')) == key(name)), None)
            if chosen is None:
                active = [p for p in plans if p.get('active', True)]
                near = [p for p in active
                        if key(p.get('name')) and (key(name) in key(p.get('name'))
                                                   or key(p.get('name')) in key(name))]
                free = [p for p in active if 'free' in key(p.get('name'))]
                if near:
                    chosen = near[0]
                    warning = (f'No plan named exactly {name!r}, using the '
                               f'closest match {chosen.get("name")!r} (id '
                               f'{chosen.get("id")})')
                elif free:
                    chosen = free[0]
                    warning = (f'No plan named {name!r}, using the free plan '
                               f'{chosen.get("name")!r} (id {chosen.get("id")})')
                elif active:
                    chosen = active[0]
                    warning = (f'No plan named {name!r} and no free plan, falling '
                               f'back to {chosen.get("name")!r} (id '
                               f'{chosen.get("id")})')
                else:
                    warning = (f'No plan named {name!r} and no active plan to fall '
                               f'back to')

        capacity = self.plan_capacity(chosen) if chosen else None
        if warning:
            logger.warning('%s', warning)
        return capacity, warning

    def list_agents(self) -> list:
        '''
        Retrieve the NMS agents available to this account

        Returns:
            list: raw agent dicts, empty list on failure
        '''
        url = f'{self.kentik.grpc_base_url}{self.kentik.agent_list}'
        payload = self._request('GET', url)
        agents = []
        if isinstance(payload, dict):
            agents = payload.get('agents') or payload.get('items') or []
        logger.info('Retrieved %d Kentik agent(s)', len(agents))
        return agents

    def list_credentials(self) -> list:
        '''
        Retrieve the SNMP credential groups available to this account

        Returns:
            list: raw credential dicts, empty list on failure
        '''
        url = f'{self.kentik.grpc_base_url}{self.kentik.credential_list}'
        payload = self._request('GET', url)
        credentials = []
        if isinstance(payload, dict):
            credentials = (payload.get('credentials') or payload.get('groups')
                           or payload.get('items') or [])
        logger.info('Retrieved %d Kentik credential group(s)', len(credentials))
        return credentials

    def device_payload(self, device: Device, site_id: str = '') -> dict:
        '''
        Build the device API request body for this device

        Parameters:
            device (Device): device candidate
            site_id (str): Kentik site id the device belongs to

        Returns:
            dict: request body for the device create
        '''
        return build_device_payload(self.config, device, site_id)

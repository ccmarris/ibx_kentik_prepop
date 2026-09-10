#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Offline mock of the Infoblox and Kentik APIs, for end to end testing

 Requirements:
   Python 3.10+

 Author: Chris Marrison

 Date Last Updated: 20260910

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

import argparse
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)


# UDDI IPAM: two sites, a transit /30, an untagged subnet, and address/geo tags
# on LON-DC1 only, so both the complete and the partial address paths are
# exercised.
SUBNETS = [
    {'id': 'ipam/subnet/1', 'address': '10.1.0.0', 'cidr': 25,
     'comment': 'Staff wifi',
     'tags': {'Site': 'LON-DC1', 'Address': '1 Bishopsgate', 'City': 'London',
              'Country': 'United Kingdom', 'Postcode': 'EC2N 3AQ',
              'Latitude': '51.5155', 'Longitude': '-0.0822'},
     'dhcp_options': [{'option_code': 'oc/1', 'option_value': '10.1.0.1'}]},
    {'id': 'ipam/subnet/2', 'address': '10.1.0.128', 'cidr': 25,
     'comment': 'Printers',
     'tags': {'Site': 'LON-DC1', 'Address': '1 Bishopsgate', 'City': 'London',
              'Country': 'United Kingdom'}},
    {'id': 'ipam/subnet/3', 'address': '10.1.4.0', 'cidr': 30,
     'comment': 'WAN transit', 'tags': {'Site': 'lon-dc1 '}},
    {'id': 'ipam/subnet/4', 'address': '10.2.0.0', 'cidr': 24,
     'comment': 'Branch users',
     'tags': {'Site': 'NYC-BR2', 'kentik_class': 'user_access',
              'City': 'New York'},
     'dhcp_options': [{'option_code': 'oc/1', 'option_value': '10.2.0.1'}]},
    {'id': 'ipam/subnet/5', 'address': '10.2.1.0', 'cidr': 24,
     'comment': 'Guest', 'tags': {'Site': 'NYC-BR2'}},
    {'id': 'ipam/subnet/6', 'address': '192.168.99.0', 'cidr': 24,
     'comment': 'No site tag', 'tags': {}},
]

OPTION_CODES = [{'id': 'oc/1', 'name': 'routers'}]

# Universal Asset Insights: six network devices, some multi-homed
ASSETS = [
    {'name': 'lon-rtr-01', 'category': 'network', 'type': 'router',
     'vendor': 'Cisco', 'model': 'ISR4451', 'os_version': '17.6',
     'ip_addresses': ['10.1.0.1', '10.1.4.1']},
    {'name': 'lon-sw-01', 'category': 'network', 'type': 'switch',
     'vendor': 'Cisco', 'model': 'C9300', 'os_version': '17.9',
     'ip_addresses': ['10.1.0.2']},
    {'name': 'lon-sw-02', 'category': 'network', 'type': 'switch',
     'vendor': 'Cisco', 'model': 'C9300', 'ip_addresses': ['10.1.0.3']},
    {'name': 'lon-sw-03', 'category': 'network', 'type': 'switch',
     'vendor': 'Cisco', 'model': 'C9300', 'ip_addresses': ['10.1.0.4']},
    {'name': 'nyc-fw-01', 'category': 'network', 'type': 'firewall',
     'vendor': 'Palo Alto', 'model': 'PA-440', 'os_version': '11.1',
     'ip_addresses': ['10.2.0.1']},
    {'name': 'nyc-rtr-02', 'category': 'network', 'type': 'router',
     'vendor': 'Cisco', 'model': 'ISR4331', 'ip_addresses': ['10.2.0.2']},
]

# Kentik: one pre-existing site with a hand-added prefix and a market, so the
# merge and preserve behaviours are testable. Four device slots on the free
# plan, so the capacity guard can be exercised with six candidates.
SITES = [{'id': '42', 'title': 'LON-DC1', 'type': 'SITE_TYPE_BRANCH',
          'siteMarket': 'EMEA',
          'addressClassification': {'userAccessNetworks': ['10.1.0.0/24',
                                                           '172.16.9.0/24'],
                                    'infrastructureNetworks': [],
                                    'otherNetworks': []}}]

PLANS = [
    {'id': 7, 'name': 'Free Flowpak Plan', 'active': True, 'max_devices': 4,
     'max_fps': 100, 'devices': []},
    {'id': 8, 'name': 'Enterprise', 'active': True, 'max_devices': 500,
     'max_fps': 10000, 'devices': [{'id': '1'}]},
]

AGENTS = [{'id': 'agent-1', 'name': 'lon-collector', 'status': 'ACTIVE'},
          {'id': 'agent-2', 'name': 'nyc-collector', 'status': 'ACTIVE'}]

CREDENTIALS = [{'id': 'c1', 'name': 'snmp-ro'}, {'id': 'c2', 'name': 'snmp-v3'}]

DEVICES = []

# Requests received, so a test can assert on what was actually sent
CALLS = []


def validate_device(device: dict) -> str:
    '''
    Reproduce the device validation the real API performs

    Parameters:
        device (dict): the device object from the request body

    Returns:
        str: an error message, or empty string when the device is acceptable
    '''
    error = ''
    bgp_type = device.get('device_bgp_type')

    if not bgp_type:
        error = ('internal validation (device_bgp_type: The device_bgp_type '
                 'is required)')
    elif bgp_type == 'device':
        has_peer = (device.get('device_bgp_neighbor_ip')
                    or device.get('device_bgp_neighbor_ip6'))
        if not device.get('device_bgp_neighbor_asn') or not has_peer:
            error = ('internal validation (device_bgp_neighbor_asn and a '
                     'peering address are required when device_bgp_type is '
                     '"device")')
    elif bgp_type == 'other_device' and not device.get('use_bgp_device_id'):
        error = ('internal validation (use_bgp_device_id is required when '
                 'device_bgp_type is "other_device")')

    return error


def merge_site(existing: dict, incoming: dict) -> dict:
    '''
    Merge a site PUT the way Kentik does

    Kentik merges the address classification lists rather than replacing them,
    so a prefix added by hand in the portal survives an update.

    Parameters:
        existing (dict): the stored site
        incoming (dict): the site from the request body

    Returns:
        dict: the merged site
    '''
    merged = dict(existing)
    incoming = dict(incoming)
    incoming_class = incoming.pop('addressClassification', {}) or {}
    existing_class = dict(merged.get('addressClassification') or {})

    for bucket, values in incoming_class.items():
        combined = set(existing_class.get(bucket) or [])
        combined.update(values or [])
        existing_class[bucket] = sorted(combined)

    merged.update(incoming)
    merged['addressClassification'] = existing_class
    return merged


class Handler(BaseHTTPRequestHandler):
    '''
    Route the handful of endpoints this tool uses
    '''

    def _send(self, payload, code: int = 200) -> None:
        '''
        Write a JSON response

        Parameters:
            payload: JSON-serialisable body
            code (int): HTTP status code

        Returns:
            None
        '''
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return

    def _body(self) -> dict:
        '''
        Read and parse the request body

        Returns:
            dict: parsed body, empty dict when there is none
        '''
        length = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(length) or b'{}')

    def do_GET(self) -> None:
        '''
        Serve the read endpoints
        '''
        path = self.path.split('?')[0]
        CALLS.append(('GET', path))

        if path == '/api/ddi/v1/ipam/subnet':
            self._send({'results': SUBNETS})
        elif path == '/api/ddi/v1/ipam/address_block':
            self._send({'results': []})
        elif path == '/api/ddi/v1/dhcp/option_code':
            self._send({'results': OPTION_CODES})
        elif path == '/site/v202211/sites':
            self._send({'sites': SITES})
        elif path == '/api/v5/plans':
            self._send({'plans': PLANS})
        elif path == '/kagent/v202401/agents':
            self._send({'agents': AGENTS})
        elif path == '/credential/v202407alpha1/group':
            self._send({'credentials': CREDENTIALS})
        elif path == '/device/v202504beta2/device':
            self._send({'devices': DEVICES})
        elif re.match(r'/device/v202504beta2/device/\w+$', path):
            device_id = path.rsplit('/', 1)[1]
            match = next((d for d in DEVICES if str(d['id']) == device_id), None)
            self._send({'device': match} if match else {'error': 'not found'},
                       200 if match else 404)
        elif path == '/_calls':
            self._send({'calls': CALLS})
        else:
            self._send({'error': 'not found', 'path': path}, 404)
        return

    def do_POST(self) -> None:
        '''
        Serve the create endpoints
        '''
        body = self._body()
        CALLS.append(('POST', self.path, body))

        if self.path == '/api/v1/assets/search':
            self._send({'data': ASSETS, 'pagination': {}})
        elif self.path == '/site/v202211/sites':
            site = dict(body.get('site') or {})
            site['id'] = str(100 + len(SITES))
            SITES.append(site)
            self._send({'site': site})
        elif self.path == '/device/v202504beta2/device':
            device = dict(body.get('device') or {})
            error = validate_device(device)
            if error:
                self._send({'code': 3, 'message': error, 'details': []}, 400)
                return
            device['id'] = str(500 + len(DEVICES))
            DEVICES.append(device)
            plan = next((p for p in PLANS if p['id'] == device.get('plan_id')),
                        None)
            if plan is not None:
                plan['devices'].append({'id': device['id']})
            self._send({'device': device})
        else:
            self._send({'error': 'not found', 'path': self.path}, 404)
        return

    def do_PUT(self) -> None:
        '''
        Serve the update endpoints
        '''
        body = self._body()
        CALLS.append(('PUT', self.path, body))

        device_match = re.match(r'/device/v202504beta2/device/(\w+)', self.path)
        site_match = re.match(r'/site/v202211/sites/(\w+)', self.path)

        if device_match:
            device = dict(body.get('device') or {})
            device['id'] = device_match.group(1)
            for index, existing in enumerate(DEVICES):
                if str(existing['id']) == device['id']:
                    DEVICES[index] = device
            self._send({'device': device})
        elif site_match:
            site_id = site_match.group(1)
            updated = dict(body.get('site') or {})
            updated['id'] = site_id
            for index, existing in enumerate(SITES):
                if existing['id'] == site_id:
                    updated = merge_site(existing, updated)
                    SITES[index] = updated
            self._send({'site': updated})
        else:
            self._send({'error': 'not found', 'path': self.path}, 404)
        return

    def log_message(self, *args) -> None:
        '''
        Keep the default request logging out of the test output
        '''
        return


def parseargs():
    '''
    Parse command line arguments

    Returns:
        Namespace: parsed arguments
    '''
    parser = argparse.ArgumentParser(
        description='Offline mock of the Infoblox and Kentik APIs used by '
                    'ibx_kentik_prepop')
    parser.add_argument('-p', '--port', type=int, default=8899,
                        help='port to listen on (default: 8899)')
    parser.add_argument('--host', default='127.0.0.1',
                        help='address to bind to (default: 127.0.0.1)')
    return parser.parse_args()


def main() -> int:
    '''
    Entry point

    Returns:
        int: exit code
    '''
    args = parseargs()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s: %(message)s')
    logger.info('Mock Infoblox/Kentik API on http://%s:%d', args.host, args.port)
    HTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


### Main ###
if __name__ == '__main__':
    exitcode = main()
    exit(exitcode)

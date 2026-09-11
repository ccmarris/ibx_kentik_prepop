#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    NIOS Network Insight discovered device source adapter

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
from ibx_kentik_prepop.model import Device
from ibx_kentik_prepop.sources.base import describe, match_site, role_from_text
from ibx_kentik_prepop.sources.nios import NIOS

logger = logging.getLogger(__name__)


# VERIFY against the grid's own /wapidoc: the discovery:device field set has
# changed between NIOS releases. The raw payload is kept on each Device so the
# flattening below can be corrected without re-pulling the data.
DEVICE_OBJTYPE = 'discovery:device'
# CORE is what every release exposes; EXTRA is the richer detail. A grid that
# does not know one of the EXTRA names rejects the whole request, so the
# request falls back to CORE rather than reporting no devices at all.
DEVICE_FIELDS_CORE = 'name,address,model,os_version,vendor,type,network_view'
DEVICE_FIELDS = (f'{DEVICE_FIELDS_CORE},description,location,'
                 f'interface_count,extattrs')
INTERFACE_OBJTYPE = 'discovery:deviceinterface'
INTERFACE_FIELDS = ('device,name,ip_address,network_view,type,description,'
                    'speed,admin_status,oper_status')


class NetworkInsight(NIOS):
    '''
    Read discovered device inventory from NIOS Network Insight
    '''

    name = 'network_insight'

    def get_devices(self, subnets: list = None) -> list:
        '''
        Retrieve discovered devices with their interfaces, placed on sites

        Parameters:
            subnets (list): normalised subnet records, for site attribution

        Returns:
            list: list of Device objects with origin 'network_insight'
        '''
        devices = []
        raw = self.get_all(DEVICE_OBJTYPE, DEVICE_FIELDS,
                           fallback_fields=DEVICE_FIELDS_CORE)
        interfaces = self.interfaces_by_device()

        for obj in raw:
            address = str(obj.get('address', ''))
            role = role_from_text(obj.get('type'), obj.get('model'),
                                  obj.get('description'))
            device = Device(
                name=str(obj.get('name', '') or address),
                mgmt_ip=address,
                role=role,
                vendor=str(obj.get('vendor', '')),
                model=str(obj.get('model', '')),
                os_version=str(obj.get('os_version', '')),
                description=describe(obj, self.config.device.description_keys,
                                     obj.get('extattrs')),
                interfaces=interfaces.get(str(obj.get('_ref', '')), []),
                origin='network_insight',
                raw=obj,
            )
            device.site_name, device.site_match = match_site(
                device, subnets or [], str(obj.get('location', '') or ''))
            devices.append(device)

        logger.info('Retrieved %d Network Insight device(s) with %d interface(s)',
                    len(devices), sum(len(v) for v in interfaces.values()))
        return devices

    def interfaces_by_device(self) -> dict:
        '''
        Retrieve discovered interfaces, grouped by their parent device

        Parameters:
            None

        Returns:
            dict: device _ref -> list of normalised interface dicts
        '''
        grouped = {}
        for obj in self.get_all(INTERFACE_OBJTYPE, INTERFACE_FIELDS,
                                fallback_fields='device,name,ip_address'):
            parent = obj.get('device')
            if isinstance(parent, dict):
                parent = parent.get('_ref', '')
            address = obj.get('ip_address', '')
            if isinstance(address, list):
                address = address[0] if address else ''
            grouped.setdefault(str(parent or ''), []).append({
                'name': str(obj.get('name', '')),
                'address': str(address or ''),
                'description': str(obj.get('description', '')),
                'speed': str(obj.get('speed', '')),
                'type': str(obj.get('type', '')),
            })
        return grouped



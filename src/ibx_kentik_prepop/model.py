#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Data model for Infoblox to Kentik site and device pre-population

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
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


# Kentik site type enum values (Site API v202211)
SITE_TYPES = (
    'SITE_TYPE_UNSPECIFIED',
    'SITE_TYPE_DATA_CENTER',
    'SITE_TYPE_CLOUD',
    'SITE_TYPE_BRANCH',
    'SITE_TYPE_CONNECTIVITY',
    'SITE_TYPE_CUSTOMER',
    'SITE_TYPE_OTHER',
)

# Internal classification keys mapped onto the Kentik addressClassification buckets
CLASS_INFRASTRUCTURE = 'infrastructure'
CLASS_USER_ACCESS = 'user_access'
CLASS_OTHER = 'other'
CLASSIFICATIONS = (CLASS_INFRASTRUCTURE, CLASS_USER_ACCESS, CLASS_OTHER)

CLASS_TO_KENTIK = {
    CLASS_INFRASTRUCTURE: 'infrastructureNetworks',
    CLASS_USER_ACCESS: 'userAccessNetworks',
    CLASS_OTHER: 'otherNetworks',
}

# Device roles we recognise from discovery data
ROLE_ROUTER = 'router'
ROLE_SWITCH = 'switch'
ROLE_FIREWALL = 'firewall'
ROLE_OTHER = 'other'

# Plan actions
ACTION_CREATE = 'create'
ACTION_UPDATE = 'update'
ACTION_NO_CHANGE = 'no-change'
ACTION_EXISTS = 'exists'

# What the importer is working on
TASK_SITES = 'sites'
TASK_DEVICES = 'devices'
TASKS = (TASK_SITES, TASK_DEVICES)

# How a device is created in Kentik. Both go through the same device API - the
# mode decides which fields are populated.
MODE_FLOW = 'flow'
MODE_NMS = 'nms'
DEVICE_MODES = (MODE_FLOW, MODE_NMS)


@dataclass
class SiteSubnet:
    '''
    A single (possibly summarised) network attached to a site
    '''
    cidr: str
    classification: str = CLASS_USER_ACCESS
    source_cidrs: tuple = ()
    comment: str = ''

    def as_dict(self) -> dict:
        '''
        Render as a plain dict for JSON/CSV output

        Returns:
            dict: serialisable representation
        '''
        return asdict(self)


@dataclass
class Device:
    '''
    A discovered (or inferred) network device candidate for Kentik
    '''
    name: str
    mgmt_ip: str = ''
    role: str = ROLE_OTHER
    vendor: str = ''
    model: str = ''
    os_version: str = ''
    description: str = ''
    site_name: str = ''
    sending_ips: tuple = ()
    interfaces: list = field(default_factory=list)
    site_match: str = ''
    origin: str = ''
    raw: dict = field(default_factory=dict, repr=False)

    def addresses(self) -> list:
        '''
        Every address known for this device, management address first

        Returns:
            list: de-duplicated addresses
        '''
        found = []
        for address in [self.mgmt_ip] + [i.get('address', '') for i in self.interfaces]:
            if address and address not in found:
                found.append(address)
        return found

    def as_dict(self, include_raw: bool = False) -> dict:
        '''
        Render as a plain dict for JSON/CSV output

        Parameters:
            include_raw (bool): include the untouched source payload

        Returns:
            dict: serialisable representation
        '''
        data = asdict(self)
        data['sending_ips'] = list(self.sending_ips)
        if not include_raw:
            data.pop('raw', None)
        return data


@dataclass
class Site:
    '''
    A Kentik site derived from Infoblox data
    '''
    name: str
    subnets: list = field(default_factory=list)
    site_type: str = 'SITE_TYPE_BRANCH'
    postal: dict = field(default_factory=dict)
    lat: float = None
    lon: float = None
    address_source: dict = field(default_factory=dict)
    devices: list = field(default_factory=list)

    def networks(self, classification: str) -> list:
        '''
        Return the CIDRs for a single classification bucket

        Parameters:
            classification (str): one of CLASSIFICATIONS

        Returns:
            list: list of CIDR strings
        '''
        return [s.cidr for s in self.subnets if s.classification == classification]

    def counts(self) -> dict:
        '''
        Subnet counts per classification bucket

        Returns:
            dict: classification -> count
        '''
        return {c: len(self.networks(c)) for c in CLASSIFICATIONS}

    def source_count(self) -> int:
        '''
        Number of pre-summarisation subnets behind this site

        Returns:
            int: count of source subnets
        '''
        total = 0
        for subnet in self.subnets:
            total += len(subnet.source_cidrs) or 1
        return total

    def as_dict(self) -> dict:
        '''
        Render as a plain dict for JSON output

        Returns:
            dict: serialisable representation
        '''
        return {
            'name': self.name,
            'site_type': self.site_type,
            'postal': self.postal,
            'lat': self.lat,
            'lon': self.lon,
            'address_source': self.address_source,
            'counts': self.counts(),
            'source_count': self.source_count(),
            'subnets': [s.as_dict() for s in self.subnets],
            'devices': [d.as_dict() for d in self.devices],
        }


@dataclass
class SitePlan:
    '''
    The intended outcome for one site
    '''
    site: Site
    action: str = ACTION_CREATE
    kentik_id: str = ''
    added: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    merged: dict = field(default_factory=dict)
    raw_site: dict = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict:
        '''
        Render as a plain dict for JSON output

        Returns:
            dict: serialisable representation
        '''
        data = self.site.as_dict()
        data['action'] = self.action
        data['kentik_id'] = self.kentik_id
        data['added'] = self.added
        data['extra'] = self.extra
        data['merged'] = self.merged
        return data


@dataclass
class DevicePlan:
    '''
    The intended outcome for one device
    '''
    device: Device
    action: str = ACTION_CREATE
    kentik_id: str = ''
    site_id: str = ''
    excluded: bool = False
    exclude_reason: str = ''
    mismatch: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        '''
        Render as a plain dict for JSON output

        Returns:
            dict: serialisable representation
        '''
        from ibx_kentik_prepop.summarise import sanitise_device_name
        data = self.device.as_dict()
        data['kentik_name'] = sanitise_device_name(self.device.name)
        data['action'] = self.action
        data['kentik_id'] = self.kentik_id
        data['site_id'] = self.site_id
        data['excluded'] = self.excluded
        data['exclude_reason'] = self.exclude_reason
        data['mismatch'] = self.mismatch
        return data


@dataclass
class Warning:
    '''
    A data quality or safety finding raised while building the plan
    '''
    category: str
    message: str
    detail: str = ''

    def as_dict(self) -> dict:
        '''
        Render as a plain dict for JSON/CSV output

        Returns:
            dict: serialisable representation
        '''
        return asdict(self)


@dataclass
class Plan:
    '''
    The complete desired state, ready to report or apply
    '''
    source: str = ''
    site_key: str = ''
    generated: str = ''
    task: str = TASK_SITES
    entries: list = field(default_factory=list)
    devices: list = field(default_factory=list)
    device_entries: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    device_mode: str = MODE_FLOW
    snmp_mode: str = 'none'
    agent_id: str = ''
    plan_id: int = 0
    plan_name: str = ''
    capacity: dict = field(default_factory=dict)
    address_keys: dict = field(default_factory=dict)

    def add_warning(self, category: str, message: str, detail: str = '') -> None:
        '''
        Record a warning against the plan

        Parameters:
            category (str): short warning category
            message (str): human readable message
            detail (str): optional supporting detail

        Returns:
            None
        '''
        logger.warning('%s: %s %s', category, message, detail)
        self.warnings.append(Warning(category=category, message=message, detail=detail))
        return

    def included_devices(self) -> list:
        '''
        Device plan entries that are not excluded

        Returns:
            list: list of DevicePlan
        '''
        return [e for e in self.device_entries if not e.excluded]

    def stats(self) -> dict:
        '''
        Summary counters for the plan

        Returns:
            dict: counts by action plus subnet and device totals
        '''
        actions = {ACTION_CREATE: 0, ACTION_UPDATE: 0, ACTION_NO_CHANGE: 0}
        subnets = 0
        sources = 0
        for entry in self.entries:
            actions[entry.action] = actions.get(entry.action, 0) + 1
            subnets += len(entry.site.subnets)
            sources += entry.site.source_count()

        device_actions = {ACTION_CREATE: 0, ACTION_EXISTS: 0, ACTION_UPDATE: 0}
        for entry in self.device_entries:
            if entry.excluded:
                continue
            device_actions[entry.action] = device_actions.get(entry.action, 0) + 1

        return {
            'task': self.task,
            'sites': len(self.entries),
            'actions': actions,
            'summarised_subnets': subnets,
            'source_subnets': sources,
            'devices': len(self.devices),
            'device_actions': device_actions,
            'device_entries': len(self.device_entries),
            'excluded_devices': sum(1 for e in self.device_entries if e.excluded),
            'warnings': len(self.warnings),
        }

    def as_dict(self) -> dict:
        '''
        Render the whole plan as a plain dict for JSON output

        Returns:
            dict: serialisable representation
        '''
        return {
            'source': self.source,
            'site_key': self.site_key,
            'generated': self.generated,
            'task': self.task,
            'device_mode': self.device_mode,
            'snmp_mode': self.snmp_mode,
            'agent_id': self.agent_id,
            'plan_id': self.plan_id,
            'plan_name': self.plan_name,
            'capacity': self.capacity,
            'address_keys': self.address_keys,
            'stats': self.stats(),
            'sites': [e.as_dict() for e in self.entries],
            'devices': [d.as_dict() for d in self.devices],
            'device_entries': [e.as_dict() for e in self.device_entries],
            'warnings': [w.as_dict() for w in self.warnings],
        }

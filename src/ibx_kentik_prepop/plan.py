#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Build the desired-state plan from Infoblox data

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
from datetime import datetime, timezone
from ibx_kentik_prepop.model import (ACTION_CREATE, ACTION_NO_CHANGE,
                                     ACTION_UPDATE, CLASSIFICATIONS, Plan,
                                     SitePlan)
from ibx_kentik_prepop.sources.nios import NIOS
from ibx_kentik_prepop.sources.nios_insight import NetworkInsight
from ibx_kentik_prepop.sources.uddi import UDDI
from ibx_kentik_prepop.sources.uddi_uai import UAI
from ibx_kentik_prepop.summarise import build_sites, site_match_key

logger = logging.getLogger(__name__)


def get_source(config):
    '''
    Instantiate the IPAM source adapter for the selected platform

    Parameters:
        config (ProjectConfig): assembled configuration

    Returns:
        SiteSource: NIOS or UDDI adapter
    '''
    if config.source == 'nios':
        source = NIOS(config)
    else:
        source = UDDI(config)
    logger.debug('Using %s as the IPAM source', source.name)
    return source


def get_device_source(config, ipam_source, plan: Plan):
    '''
    Choose the device data source, honouring platform constraints

    Parameters:
        config (ProjectConfig): assembled configuration
        ipam_source (SiteSource): the IPAM adapter already in use
        plan (Plan): plan to record warnings against

    Returns:
        SiteSource: adapter to call get_devices() on, or None
    '''
    source = None

    if config.device.use_insight:
        if config.source == 'nios':
            source = NetworkInsight(config)
        else:
            plan.add_warning('device_source',
                             'Network Insight is only available with --source nios',
                             'falling back to default gateway inference')
    elif config.device.use_uai:
        if config.source == 'uddi':
            source = UAI(config)
        else:
            plan.add_warning('device_source',
                             'Universal Asset Insights is only available with '
                             '--source uddi',
                             'falling back to default gateway inference')

    if source is None:
        source = ipam_source
        if not config.device.use_gateways:
            plan.add_warning('device_source',
                             'No discovery source selected, inferring routers from '
                             'the DHCP routers option',
                             'these are inferred, not discovered')

    logger.debug('Using %s as the device source', source.name)
    return source


def attach_devices(sites: list, devices: list, plan: Plan) -> None:
    '''
    Attach device candidates to their sites

    Parameters:
        sites (list): Site objects
        devices (list): Device objects
        plan (Plan): plan to record warnings against

    Returns:
        None
    '''
    index = {site_match_key(site.name): site for site in sites}
    orphans = []

    for device in devices:
        site = index.get(site_match_key(device.site_name))
        if site is None:
            orphans.append(device.name or device.mgmt_ip)
            continue
        site.devices.append(device)

    if orphans:
        plan.add_warning('devices_without_site',
                         f'{len(orphans)} device(s) could not be attributed to a site',
                         ', '.join(orphans[:20]))
    return


def merge_networks(derived: dict, existing: dict, replace: bool) -> tuple:
    '''
    Work out the network lists to submit and what changes

    By default the derived prefixes are added to whatever Kentik already holds,
    so prefixes added by hand in the portal survive. With replace set, the
    derived lists become authoritative.

    Parameters:
        derived (dict): classification -> list of derived CIDRs
        existing (dict): classification -> list of CIDRs already in Kentik
        replace (bool): treat the derived lists as authoritative

    Returns:
        tuple: (merged dict, added dict, removed dict)
    '''
    merged = {}
    added = {}
    removed = {}

    for classification in CLASSIFICATIONS:
        derived_set = set(derived.get(classification, []))
        existing_set = set(existing.get(classification, []))
        if replace:
            final = derived_set
        else:
            final = existing_set | derived_set
        merged[classification] = sorted(final)
        new = sorted(final - existing_set)
        gone = sorted(existing_set - final)
        if new:
            added[classification] = new
        if gone:
            removed[classification] = gone

    return merged, added, removed


def build_plan(config, kentik=None) -> Plan:
    '''
    Build the complete desired-state plan

    Parameters:
        config (ProjectConfig): assembled configuration
        kentik (KENTIK): Kentik target for diffing against live state, or None
            to plan every site as a create

    Returns:
        Plan: the plan, including warnings
    '''
    plan = Plan(source=config.source,
                site_key=config.site.site_key,
                generated=datetime.now(timezone.utc).isoformat(timespec='seconds'))

    ipam_source = get_source(config)
    records = ipam_source.get_subnets()
    if not records:
        plan.add_warning('no_subnets',
                         'The source returned no subnets',
                         'check credentials, network view / IP space and filters')

    sites, warnings = build_sites(records, config)
    for category, message, detail in warnings:
        plan.add_warning(category, message, detail)

    if config.device.enabled:
        device_source = get_device_source(config, ipam_source, plan)
        devices = device_source.get_devices(records)
        wanted = tuple(r.lower() for r in config.device.roles)
        kept = [d for d in devices if not wanted or d.role in wanted]
        if len(kept) != len(devices):
            logger.info('Filtered %d device(s) not in roles %s',
                        len(devices) - len(kept), ', '.join(wanted))
        attach_devices(sites, kept, plan)
        plan.devices = kept

    index = {}
    if kentik is not None:
        index = kentik.site_index()

    for site in sites:
        derived = {c: site.networks(c) for c in CLASSIFICATIONS}
        raw_site = index.get(site_match_key(site.name))

        if raw_site is None:
            entry = SitePlan(site=site, action=ACTION_CREATE,
                             added={c: v for c, v in derived.items() if v},
                             merged=derived)
        else:
            existing = kentik.existing_networks(raw_site)
            merged, added, removed = merge_networks(derived, existing,
                                                    config.site.replace_networks)
            action = ACTION_UPDATE if (added or removed) else ACTION_NO_CHANGE
            entry = SitePlan(site=site, action=action,
                             kentik_id=str(raw_site.get('id', '')),
                             added=added, removed=removed, merged=merged,
                             raw_site=raw_site)
        plan.entries.append(entry)

    stats = plan.stats()
    logger.info('Plan: %d site(s) - %d create, %d update, %d unchanged, %d warning(s)',
                stats['sites'], stats['actions'][ACTION_CREATE],
                stats['actions'][ACTION_UPDATE], stats['actions'][ACTION_NO_CHANGE],
                stats['warnings'])
    return plan

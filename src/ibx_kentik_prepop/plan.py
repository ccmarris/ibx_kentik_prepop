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

import hashlib
import json
import logging
from datetime import datetime, timezone
from dataclasses import replace
from ibx_kentik_prepop.config import AGENT_SNMP_MODES
from ibx_kentik_prepop.model import (ACTION_CREATE, ACTION_EXISTS,
                                     ACTION_NO_CHANGE, ACTION_UPDATE,
                                     CLASSIFICATIONS, DevicePlan, MODE_FLOW,
                                     MODE_NMS, Plan, SitePlan, TASK_DEVICES,
                                     TASK_SITES)
from ibx_kentik_prepop.sources.base import match_site
from ibx_kentik_prepop.sources.nios import NIOS
from ibx_kentik_prepop.sources.nios_insight import NetworkInsight
from ibx_kentik_prepop.sources.uddi import UDDI
from ibx_kentik_prepop.sources.uddi_uai import UAI
from ibx_kentik_prepop.summarise import (build_sites, sanitise_device_name,
                                          site_match_key)
from ibx_kentik_prepop.targets.kentik import (device_site_id, read_field,
                                              sending_ips_for)

logger = logging.getLogger(__name__)


def plan_fingerprint(plan) -> str:
    '''
    Fingerprint the changes a plan would make

    Used to prove that the plan being applied is the same one the operator
    reviewed. It covers the site name, action, target id and the final network
    lists - not the timestamp - so an unrelated re-run produces the same value
    while any change to the outcome produces a different one.

    Parameters:
        plan (Plan): the plan to fingerprint

    Returns:
        str: short hex digest
    '''
    payload = {'task': plan.task, 'sites': [], 'devices': [],
               'device_mode': plan.device_mode, 'snmp_mode': plan.snmp_mode,
               'agent_id': plan.agent_id, 'plan_id': plan.plan_id}

    for entry in sorted(plan.entries, key=lambda e: e.site.name.casefold()):
        payload['sites'].append({
            'site': entry.site.name,
            'action': entry.action,
            'kentik_id': entry.kentik_id,
            'networks': {c: sorted(v) for c, v in sorted((entry.merged or {}).items())},
            'postal': dict(sorted((entry.site.postal or {}).items())),
            'lat': entry.site.lat,
            'lon': entry.site.lon,
        })

    for entry in sorted(plan.device_entries, key=lambda e: e.device.name.casefold()):
        payload['devices'].append({
            'device': entry.device.name,
            'action': entry.action,
            'kentik_id': entry.kentik_id,
            'site_id': entry.site_id,
            'excluded': entry.excluded,
            'sending_ips': sorted(entry.device.sending_ips or ()),
        })

    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8'))
    return digest.hexdigest()[:16]


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


def merge_networks(derived: dict, existing: dict) -> tuple:
    '''
    Work out the network lists to submit, and what Kentik holds that we do not

    Kentik MERGES the address classification lists on a site PUT rather than
    replacing them, and the Site API offers no field mask and no PATCH - so
    nothing this tool submits can ever remove a prefix from a site. The lists
    sent are therefore always the union of what Kentik holds and what was
    derived, and prefixes Kentik holds that the Infoblox data does not account
    for are reported as extras to be removed in the portal if unwanted.

    Parameters:
        derived (dict): classification -> list of derived CIDRs
        existing (dict): classification -> list of CIDRs already in Kentik

    Returns:
        tuple: (merged dict, added dict, extra dict)
    '''
    merged = {}
    added = {}
    extra = {}

    for classification in CLASSIFICATIONS:
        derived_set = set(derived.get(classification, []))
        existing_set = set(existing.get(classification, []))
        final = existing_set | derived_set

        merged[classification] = sorted(final)
        new = sorted(derived_set - existing_set)
        unaccounted = sorted(existing_set - derived_set)
        if new:
            added[classification] = new
        if unaccounted:
            extra[classification] = unaccounted

    return merged, added, extra


def is_excluded(device, patterns) -> tuple:
    '''
    Test a device against the exclusion list

    A pattern matches the discovered name, the sanitised Kentik name or the
    management address, case-insensitively.

    Parameters:
        device (Device): device candidate
        patterns: exclusion patterns

    Returns:
        tuple: (excluded bool, the pattern that matched)
    '''
    excluded = False
    matched = ''
    candidates = {str(device.name).casefold(),
                  sanitise_device_name(device.name).casefold(),
                  str(device.mgmt_ip).casefold()}
    for pattern in patterns or ():
        if str(pattern).strip().casefold() in candidates:
            excluded = True
            matched = str(pattern)
            break
    return excluded, matched


def device_mismatch(raw_device: dict, device, site_id: str, config) -> dict:
    '''
    Differences between a derived device and the one Kentik already holds

    Only the two fields this tool derives are compared, because they are the
    only ones it would ever change.

    Parameters:
        raw_device (dict): the device as returned by Kentik
        device (Device): the derived device candidate
        site_id (str): the site id this device should carry
        config (ProjectConfig): assembled configuration

    Returns:
        dict: field -> {kentik, derived}, empty when they agree
    '''
    mismatch = {}

    current_site = device_site_id(raw_device)
    if site_id and current_site != str(site_id):
        mismatch['site_id'] = {'kentik': current_site, 'derived': str(site_id)}

    derived_ips = sending_ips_for(config, device)
    current_ips = sorted(str(a) for a in (read_field(raw_device, 'sending_ips')
                                          or []))
    if derived_ips and current_ips != sorted(derived_ips):
        mismatch['sending_ips'] = {'kentik': current_ips, 'derived': derived_ips}

    return mismatch


def build_device_plan(config, kentik=None) -> tuple:
    '''
    Build the device desired state

    IPAM subnets are still read, because they are what places a device on a
    site, and existing Kentik sites are read to resolve the site ids devices
    need. Devices are only ever created or (opt in) re-placed - never deleted.

    Parameters:
        config (ProjectConfig): assembled configuration
        kentik (KENTIK): Kentik target, or None to plan everything as a create

    Returns:
        tuple: (Plan, ProjectConfig) - the config carries the resolved plan id
    '''
    plan = Plan(source=config.source,
                site_key=config.site.site_key,
                task=TASK_DEVICES,
                device_mode=config.device.mode,
                snmp_mode=config.device.snmp_mode,
                agent_id=config.device.agent_id,
                plan_name=config.device.plan_name,
                generated=datetime.now(timezone.utc).isoformat(timespec='seconds'))

    ipam_source = get_source(config)
    records = ipam_source.get_subnets()
    if not records:
        plan.add_warning('no_subnets',
                         'The source returned no subnets, so devices cannot be '
                         'placed on sites',
                         'check credentials, network view / IP space and filters')

    sites, warnings = build_sites(records, config)
    for category, message, detail in warnings:
        plan.add_warning(category, message, detail)

    # The site key now has a default, so a key that matches nothing has to be
    # said out loud rather than looking like an empty source.
    if records and not any(r.get('site') for r in records):
        plan.add_warning('site_key_not_found',
                         f'No subnet carries the site key '
                         f'{config.site.site_key!r}, so no sites could be '
                         f'derived',
                         'run with --list-keys to see which EA/tag keys are '
                         'populated, then pass the right one with --site-key')

    device_source = get_device_source(config, ipam_source, plan)
    devices = device_source.get_devices(records)
    wanted = tuple(r.lower() for r in config.device.roles)
    devices = [d for d in devices if not wanted or d.role in wanted]
    plan.devices = devices

    # Resolve the plan first: the id goes into every flow device payload.
    if config.device.mode == MODE_FLOW:
        capacity = None
        warning = ''
        if kentik is not None:
            plans = kentik.list_plans()
            if not plans:
                plan.add_warning('plan_unavailable',
                                 'The Kentik plans API returned nothing, so the '
                                 'licence plan could not be determined',
                                 kentik.error_text() or 'enter a plan id manually')
            capacity, warning = kentik.resolve_plan(config.device.plan_name,
                                                    config.device.plan_id, plans)
        elif config.device.plan_id:
            capacity = {'id': config.device.plan_id,
                        'name': f'id {config.device.plan_id} (entered)',
                        'max_devices': None, 'used': 0, 'remaining': None,
                        'active': True}

        if warning:
            plan.add_warning('plan_selection', warning, '')

        if capacity:
            plan.capacity = capacity
            plan.plan_id = int(capacity['id']) if capacity['id'] is not None else 0
            plan.plan_name = capacity['name']
            if plan.plan_id != config.device.plan_id:
                config = replace(config, device=replace(config.device,
                                                        plan_id=plan.plan_id))
                if kentik is not None:
                    kentik.use_config(config)
        else:
            plan.add_warning('plan_selection',
                             'No licence plan could be resolved, so flow devices '
                             'cannot be created',
                             'enter a plan id, or switch to NMS mode')

    if config.device.snmp_mode in AGENT_SNMP_MODES and not config.device.agent_id:
        plan.add_warning('snmp_agent',
                         'Agent-based SNMP needs a Universal Agent - a device '
                         'created without one is never polled',
                         'pick an agent before applying')
    if (config.device.snmp_mode in AGENT_SNMP_MODES and config.device.agent_id
            and not config.device.credential_name):
        plan.add_warning('snmp_credential',
                         'No SNMP credential selected, so the agent has nothing '
                         'to poll with',
                         'pick a credential from the Kentik credential vault')
    if (config.device.snmp_mode == 'agent-full'
            and not config.device.monitoring_template_id):
        plan.add_warning('monitoring_template',
                         'Full monitoring with no monitoring template - Kentik '
                         'will apply its own default',
                         'set a template id to choose the polling targets')

    # Canonical spelling per site, so a device matched via a subnet tagged with
    # a variant spelling still reports (and resolves) the same site as the site
    # task created.
    canonical = {site_match_key(site.name): site.name for site in sites}

    site_ids = {}
    if kentik is not None:
        for key, raw_site in kentik.site_index().items():
            site_ids[key] = str(raw_site.get('id', ''))

    device_index = kentik.device_index() if kentik is not None else {}

    unplaced = []
    missing_sites = set()
    for device in devices:
        # The adapters place devices as they read them, because only they see
        # the source's own location attribute. Anything still unplaced is
        # matched here so every device source behaves the same way.
        if not device.site_name:
            device.site_name, device.site_match = match_site(device, records)
        device.site_name = canonical.get(site_match_key(device.site_name),
                                         device.site_name)
        device.sending_ips = tuple(sending_ips_for(config, device))
        site_id = site_ids.get(site_match_key(device.site_name), '')
        if device.site_name and not site_id and kentik is not None:
            missing_sites.add(device.site_name)
        if not device.site_name:
            unplaced.append(device.name or device.mgmt_ip)

        entry = DevicePlan(device=device, site_id=site_id)
        excluded, pattern = is_excluded(device, config.device.exclude)
        if excluded:
            entry.excluded = True
            entry.exclude_reason = f'excluded by {pattern!r}'

        raw_device = device_index.get(sanitise_device_name(device.name).casefold())
        if raw_device is not None:
            entry.kentik_id = str(read_field(raw_device, 'id') or '')
            entry.mismatch = device_mismatch(raw_device, device, site_id, config)
            if entry.mismatch and config.device.update_existing:
                entry.action = ACTION_UPDATE
            else:
                entry.action = ACTION_EXISTS
        else:
            entry.action = ACTION_CREATE

        plan.device_entries.append(entry)

    if unplaced:
        plan.add_warning('devices_without_site',
                         f'{len(unplaced)} device(s) could not be placed on a site',
                         ', '.join(unplaced[:20]))
    if missing_sites:
        plan.add_warning('sites_not_in_kentik',
                         f'{len(missing_sites)} site(s) do not exist in Kentik yet, '
                         f'so those devices would be created without a site',
                         ', '.join(sorted(missing_sites)[:20]))

    creates = sum(1 for e in plan.device_entries
                  if not e.excluded and e.action == ACTION_CREATE)
    remaining = (plan.capacity or {}).get('remaining')
    if remaining is not None and creates > remaining:
        plan.add_warning('plan_capacity',
                         f'{creates} device(s) to create exceeds the {remaining} '
                         f'slot(s) left on plan {plan.plan_name!r}',
                         'exclude devices, pick another plan, or allow over capacity')

    stats = plan.stats()
    logger.info('Device plan: %d candidate(s) - %d create, %d exists, %d update, '
                '%d excluded, %d warning(s)',
                stats['device_entries'], stats['device_actions'][ACTION_CREATE],
                stats['device_actions'][ACTION_EXISTS],
                stats['device_actions'][ACTION_UPDATE],
                stats['excluded_devices'], stats['warnings'])
    return plan, config


def device_apply_problems(config, plan) -> list:
    '''
    Reasons a device apply must not proceed

    Parameters:
        config (ProjectConfig): assembled configuration
        plan (Plan): the device plan

    Returns:
        list: blocking problems, empty when the apply may run
    '''
    problems = []
    creates = sum(1 for e in plan.included_devices() if e.action == ACTION_CREATE)

    if config.device.snmp_mode in AGENT_SNMP_MODES and not config.device.agent_id:
        problems.append('Agent-based SNMP needs a Universal Agent to be '
                        'selected - a device with no agent is never polled')

    if creates and config.device.bgp_type == 'device':
        if not config.device.bgp_neighbor_asn:
            problems.append("BGP type 'device' requires your ASN")
        if not (config.device.bgp_neighbor_ip or config.device.bgp_neighbor_ip6):
            problems.append("BGP type 'device' requires an IPv4 and/or IPv6 "
                            'peering address')
    if (creates and config.device.bgp_type == 'other_device'
            and not config.device.bgp_device_id):
        problems.append("BGP type 'other_device' requires the id of the device "
                        'whose BGP table is shared')

    if config.device.mode == MODE_FLOW and creates and not plan.plan_id:
        problems.append('No licence plan id - the API did not return one, so '
                        'enter the plan id manually')

    remaining = (plan.capacity or {}).get('remaining')
    if (creates and remaining is not None and creates > remaining
            and not config.device.allow_over_capacity):
        problems.append(f'{creates} device(s) to create exceeds the {remaining} '
                        f'slot(s) left on plan {plan.plan_name!r} - exclude some '
                        f'devices or allow over capacity to proceed')

    for problem in problems:
        logger.error('Device apply blocked: %s', problem)
    return problems


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
                task=TASK_SITES,
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

    # The site key now has a default, so a key that matches nothing has to be
    # said out loud rather than looking like an empty source.
    if records and not any(r.get('site') for r in records):
        plan.add_warning('site_key_not_found',
                         f'No subnet carries the site key '
                         f'{config.site.site_key!r}, so no sites could be '
                         f'derived',
                         'run with --list-keys to see which EA/tag keys are '
                         'populated, then pass the right one with --site-key')

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

    for site in sites:
        for field_name, key in site.address_source.items():
            plan.address_keys.setdefault(field_name, key)
    if plan.address_keys:
        logger.info('Address/geo data taken from: %s',
                    ', '.join(f'{k}<-{v}' for k, v in sorted(plan.address_keys.items())))

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
            merged, added, extra = merge_networks(derived, existing)
            action = ACTION_UPDATE if added else ACTION_NO_CHANGE
            entry = SitePlan(site=site, action=action,
                             kentik_id=str(raw_site.get('id', '')),
                             added=added, extra=extra, merged=merged,
                             raw_site=raw_site)
        plan.entries.append(entry)

    stats = plan.stats()
    logger.info('Plan: %d site(s) - %d create, %d update, %d unchanged, %d warning(s)',
                stats['sites'], stats['actions'][ACTION_CREATE],
                stats['actions'][ACTION_UPDATE], stats['actions'][ACTION_NO_CHANGE],
                stats['warnings'])
    return plan

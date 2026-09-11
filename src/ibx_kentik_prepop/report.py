#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Dry run report rendering - table, CSV and JSON

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

import csv
import io
import json
import logging
from pathlib import Path
from ibx_kentik_prepop.model import CLASSIFICATIONS, TASK_DEVICES

logger = logging.getLogger(__name__)


SITE_HEADERS = ('site', 'action', 'type', 'infra', 'user_access', 'other',
                'source_subnets', 'summarised', 'adding', 'extra_in_kentik',
                'geo', 'address', 'devices')
SUBNET_HEADERS = ('site', 'cidr', 'classification', 'source_cidrs', 'comment')
DEVICE_HEADERS = ('name', 'kentik_name', 'action', 'role', 'mgmt_ip', 'ifaces',
                  'sending_ips', 'site', 'site_match', 'description', 'origin',
                  'detail')
WARNING_HEADERS = ('category', 'message', 'detail')

DEVICE_FOOTER = (
    'Each device created consumes a licensed device slot. For flow devices the '
    'sending IPs must be the flow exporter source addresses - a device whose '
    'sending IP never sends flow will sit idle. Excluded devices are skipped by '
    'both the apply and the export.'
)


def site_rows(plan) -> list:
    '''
    Build the site section rows

    Parameters:
        plan (Plan): the plan to render

    Returns:
        list: list of row dicts
    '''
    rows = []
    for entry in plan.entries:
        counts = entry.site.counts()
        rows.append({
            'site': entry.site.name,
            'action': entry.action,
            'type': entry.site.site_type.replace('SITE_TYPE_', ''),
            'infra': counts['infrastructure'],
            'user_access': counts['user_access'],
            'other': counts['other'],
            'source_subnets': entry.site.source_count(),
            'summarised': len(entry.site.subnets),
            'adding': sum(len(v) for v in (entry.added or {}).values()),
            'extra_in_kentik': sum(len(v) for v in (entry.extra or {}).values()),
            'geo': ('' if entry.site.lat is None
                    else f'{entry.site.lat:g},{entry.site.lon:g}'),
            'address': ', '.join(v for v in (entry.site.postal.get('city'),
                                             entry.site.postal.get('country'))
                                 if v),
            'devices': len(entry.site.devices),
        })
    return rows


def subnet_rows(plan) -> list:
    '''
    Build the subnet section rows

    Parameters:
        plan (Plan): the plan to render

    Returns:
        list: list of row dicts
    '''
    rows = []
    for entry in plan.entries:
        for classification in CLASSIFICATIONS:
            for subnet in entry.site.subnets:
                if subnet.classification != classification:
                    continue
                rows.append({
                    'site': entry.site.name,
                    'cidr': subnet.cidr,
                    'classification': subnet.classification,
                    'source_cidrs': ' '.join(subnet.source_cidrs),
                    'comment': subnet.comment,
                })
    return rows


def device_rows(plan) -> list:
    '''
    Build the device section rows

    Uses the device plan entries when the plan has them, so the action,
    exclusions and any mismatch against Kentik are visible; falls back to the
    bare candidate list on a site run.

    Parameters:
        plan (Plan): the plan to render

    Returns:
        list: list of row dicts
    '''
    from ibx_kentik_prepop.summarise import sanitise_device_name
    from ibx_kentik_prepop.targets.kentik import device_description

    def row(device, action='', kentik_id='', excluded=False, reason='',
            mismatch=None):
        detail = reason
        if not detail and mismatch:
            detail = '; '.join(f'{k}: kentik={v["kentik"]} derived={v["derived"]}'
                               for k, v in sorted(mismatch.items()))
        elif not detail and kentik_id:
            detail = f'id {kentik_id}'
        return {
            'name': ('[excluded] ' if excluded else '') + device.name,
            'kentik_name': sanitise_device_name(device.name),
            'action': 'excluded' if excluded else action,
            'role': device.role,
            'mgmt_ip': device.mgmt_ip,
            'ifaces': len(device.interfaces or []),
            'sending_ips': ' '.join(device.sending_ips or ()),
            'site': device.site_name,
            'site_match': device.site_match,
            'description': device_description(device),
            'origin': device.origin,
            'detail': detail,
        }

    if plan.device_entries:
        rows = [row(e.device, e.action, e.kentik_id, e.excluded,
                    e.exclude_reason, e.mismatch) for e in plan.device_entries]
    else:
        rows = [row(device) for device in plan.devices]
    return rows


def warning_rows(plan) -> list:
    '''
    Build the warning section rows

    Parameters:
        plan (Plan): the plan to render

    Returns:
        list: list of row dicts
    '''
    return [w.as_dict() for w in plan.warnings]


def render_rows(headers: tuple, rows: list) -> str:
    '''
    Render rows as an aligned plain text table

    Parameters:
        headers (tuple): column keys, in order
        rows (list): list of row dicts

    Returns:
        str: the rendered table
    '''
    widths = {h: len(h) for h in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(str(row.get(header, ''))))

    lines = [' '.join(h.ljust(widths[h]) for h in headers).rstrip(),
             ' '.join('-' * widths[h] for h in headers)]
    for row in rows:
        lines.append(' '.join(str(row.get(h, '')).ljust(widths[h])
                              for h in headers).rstrip())
    return '\n'.join(lines)


def render_table(plan, dry_run: bool = True) -> str:
    '''
    Render the whole plan as plain text sections

    Parameters:
        plan (Plan): the plan to render
        dry_run (bool): label the report as a dry run

    Returns:
        str: the report
    '''
    stats = plan.stats()
    out = io.StringIO()

    mode = 'DRY RUN' if dry_run else 'APPLYING'
    task = 'devices' if plan.task == TASK_DEVICES else 'sites'
    out.write(f'Infoblox to Kentik pre-population plan - {task} ({mode})\n')
    out.write(f"Source: {plan.source}   Site key: {plan.site_key}   "
              f"Generated: {plan.generated}\n")

    if plan.task == TASK_DEVICES:
        capacity = plan.capacity or {}
        slots = ('' if capacity.get('remaining') is None
                 else f" ({capacity['remaining']} of {capacity['max_devices']} "
                      f"slot(s) left)")
        out.write(f"Mode: {plan.device_mode}   "
                  f"SNMP: {plan.snmp_mode}"
                  f"{f' via agent {plan.agent_id}' if plan.agent_id else ''}   "
                  f"Plan: {plan.plan_name or 'none'}"
                  f"{'' if not plan.plan_id else f' (id {plan.plan_id})'}{slots}\n")
        if plan.device_sources:
            out.write(f"Device sources: {', '.join(plan.device_sources)}\n")
        out.write(f"Devices: {stats['device_entries']} "
                  f"({stats['device_actions']['create']} create, "
                  f"{stats['device_actions']['exists']} already in Kentik, "
                  f"{stats['device_actions']['update']} update, "
                  f"{stats['excluded_devices']} excluded)   "
                  f"Warnings: {stats['warnings']}\n")
    else:
        out.write(f"Sites: {stats['sites']} "
                  f"({stats['actions']['create']} create, "
                  f"{stats['actions']['update']} update, "
                  f"{stats['actions']['no-change']} unchanged)   "
                  f"Subnets: {stats['source_subnets']} source -> "
                  f"{stats['summarised_subnets']} summarised   "
                  f"Devices: {stats['devices']}   "
                  f"Warnings: {stats['warnings']}\n")

    if plan.address_keys and plan.task != TASK_DEVICES:
        mapping = ', '.join(f'{k} <- {v}' for k, v in sorted(plan.address_keys.items()))
        out.write(f'Address/geo EAs or tags in use: {mapping}\n')

    warnings = warning_rows(plan)
    if warnings:
        out.write('\n== Warnings ==\n')
        out.write(render_rows(WARNING_HEADERS, warnings))
        out.write('\n')

    if plan.task != TASK_DEVICES:
        out.write('\n== Sites ==\n')
        out.write(render_rows(SITE_HEADERS, site_rows(plan)))
        out.write('\n')

        out.write('\n== Subnets ==\n')
        out.write(render_rows(SUBNET_HEADERS, subnet_rows(plan)))
        out.write('\n')

    devices = device_rows(plan)
    if devices:
        heading = ('Devices' if plan.task == TASK_DEVICES
                   else 'Devices (candidates - switch the task to devices to '
                        'create them)')
        out.write(f'\n== {heading} ==\n')
        out.write(render_rows(DEVICE_HEADERS, devices))
        out.write(f'\n\n{DEVICE_FOOTER}\n')

    return out.getvalue()


def write_csv(headers: tuple, rows: list, handle) -> None:
    '''
    Write one CSV section to an open file handle

    Parameters:
        headers (tuple): column keys, in order
        rows (list): list of row dicts
        handle: open text file handle

    Returns:
        None
    '''
    writer = csv.DictWriter(handle, fieldnames=list(headers), extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return


def render_csv(plan, outfile: str = '') -> str:
    '''
    Render the plan as CSV

    With an outfile, one file per section is written alongside it
    (<stem>-sites.csv, -subnets.csv, -devices.csv, -warnings.csv) and the
    returned string lists the files. Without one, the sections are returned
    concatenated with a header line between them.

    Parameters:
        plan (Plan): the plan to render
        outfile (str): base output path, empty for a single string

    Returns:
        str: file list or the concatenated CSV sections
    '''
    sections = [
        ('sites', SITE_HEADERS, site_rows(plan)),
        ('subnets', SUBNET_HEADERS, subnet_rows(plan)),
        ('devices', DEVICE_HEADERS, device_rows(plan)),
        ('warnings', WARNING_HEADERS, warning_rows(plan)),
    ]
    if plan.task == TASK_DEVICES:
        sections = [s for s in sections if s[0] in ('devices', 'warnings')]

    if outfile:
        base = Path(outfile)
        written = []
        for name, headers, rows in sections:
            if not rows:
                continue
            path = base.with_name(f'{base.stem}-{name}{base.suffix or ".csv"}')
            with path.open('w', newline='', encoding='utf-8') as handle:
                write_csv(headers, rows, handle)
            written.append(str(path))
            logger.info('Wrote %d %s row(s) to %s', len(rows), name, path)
        result = '\n'.join(written)
    else:
        out = io.StringIO()
        for name, headers, rows in sections:
            if not rows:
                continue
            out.write(f'# {name}\n')
            write_csv(headers, rows, out)
            out.write('\n')
        result = out.getvalue()

    return result


def render_json(plan) -> str:
    '''
    Render the plan as JSON

    Parameters:
        plan (Plan): the plan to render

    Returns:
        str: JSON document
    '''
    return json.dumps(plan.as_dict(), indent=2, sort_keys=False)


def render(plan, output: str = 'table', outfile: str = '',
           dry_run: bool = True) -> str:
    '''
    Render the plan in the requested format and optionally write it out

    Parameters:
        plan (Plan): the plan to render
        output (str): 'table', 'csv' or 'json'
        outfile (str): output path, empty to return the rendered report
        dry_run (bool): label a table report as a dry run

    Returns:
        str: the rendered report, or the list of files written for CSV
    '''
    if output == 'json':
        text = render_json(plan)
    elif output == 'csv':
        text = render_csv(plan, outfile)
    else:
        text = render_table(plan, dry_run)

    if outfile and output != 'csv':
        Path(outfile).write_text(text, encoding='utf-8')
        logger.info('Wrote %s report to %s', output, outfile)

    return text

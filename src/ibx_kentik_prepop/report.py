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
from ibx_kentik_prepop.model import CLASSIFICATIONS

logger = logging.getLogger(__name__)


SITE_HEADERS = ('site', 'action', 'type', 'infra', 'user_access', 'other',
                'source_subnets', 'summarised', 'devices')
SUBNET_HEADERS = ('site', 'cidr', 'classification', 'source_cidrs', 'comment')
DEVICE_HEADERS = ('name', 'kentik_name', 'role', 'mgmt_ip', 'vendor', 'model',
                  'site', 'origin', 'sending_ips')
WARNING_HEADERS = ('category', 'message', 'detail')

DEVICE_FOOTER = (
    'Devices are reported only. Creating them in Kentik consumes a licensed '
    'device slot, needs a plan_id, and needs the flow exporter source address '
    '- which is not necessarily the discovered management IP shown above.'
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


def device_rows(plan, kentik=None) -> list:
    '''
    Build the device section rows

    Parameters:
        plan (Plan): the plan to render
        kentik (KENTIK): target used to render the would-be device name

    Returns:
        list: list of row dicts
    '''
    from ibx_kentik_prepop.summarise import sanitise_device_name
    rows = []
    for device in plan.devices:
        rows.append({
            'name': device.name,
            'kentik_name': sanitise_device_name(device.name),
            'role': device.role,
            'mgmt_ip': device.mgmt_ip,
            'vendor': device.vendor,
            'model': device.model,
            'site': device.site_name,
            'origin': device.origin,
            'sending_ips': ' '.join(device.sending_ips or ()),
        })
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
    out.write(f'Infoblox to Kentik pre-population plan ({mode})\n')
    out.write(f"Source: {plan.source}   Site key: {plan.site_key}   "
              f"Generated: {plan.generated}\n")
    out.write(f"Sites: {stats['sites']} "
              f"({stats['actions']['create']} create, "
              f"{stats['actions']['update']} update, "
              f"{stats['actions']['no-change']} unchanged)   "
              f"Subnets: {stats['source_subnets']} source -> "
              f"{stats['summarised_subnets']} summarised   "
              f"Devices: {stats['devices']}   "
              f"Warnings: {stats['warnings']}\n")

    warnings = warning_rows(plan)
    if warnings:
        out.write('\n== Warnings ==\n')
        out.write(render_rows(WARNING_HEADERS, warnings))
        out.write('\n')

    out.write('\n== Sites ==\n')
    out.write(render_rows(SITE_HEADERS, site_rows(plan)))
    out.write('\n')

    out.write('\n== Subnets ==\n')
    out.write(render_rows(SUBNET_HEADERS, subnet_rows(plan)))
    out.write('\n')

    devices = device_rows(plan)
    if devices:
        out.write('\n== Devices (report only) ==\n')
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
    sections = (
        ('sites', SITE_HEADERS, site_rows(plan)),
        ('subnets', SUBNET_HEADERS, subnet_rows(plan)),
        ('devices', DEVICE_HEADERS, device_rows(plan)),
        ('warnings', WARNING_HEADERS, warning_rows(plan)),
    )

    if outfile:
        base = Path(outfile)
        written = []
        for name, headers, rows in sections:
            if not rows and name in ('devices', 'warnings'):
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
            if not rows and name in ('devices', 'warnings'):
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

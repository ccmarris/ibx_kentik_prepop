#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Export the plan as Kentik-shaped import artefacts

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
from ibx_kentik_prepop.model import (ACTION_NO_CHANGE, CLASSIFICATIONS,
                                     CLASS_TO_KENTIK)
from ibx_kentik_prepop.targets.kentik import KENTIK, build_device_payload

logger = logging.getLogger(__name__)


# Site API v202211. Kentik has no CSV site import - the portal's Sites page
# exports CSV but does not ingest it - so the JSON request bodies are the
# artefact that can actually be imported, and the CSV is for review/exchange.
SITE_PATH_CREATE = '/site/v202211/sites'
SITE_PATH_UPDATE = '/site/v202211/sites/{site_id}'
DEVICE_PATH_CREATE = '/api/v5/device'

SITE_CSV_HEADERS = ('title', 'type', 'action', 'kentik_id',
                    'infrastructure_networks', 'user_access_networks',
                    'other_networks')

# Columns read by Kentik's own kentik_add_device.py loader
ADD_DEVICE_CSV_HEADERS = ('siteid', 'devicename', 'devicedescription',
                          'sendingips', 'v6add', 'asn', 'devicesnmpcommunity',
                          'devicesamplerate', 'planid')

# Columns accepted by the portal's NMS bulk device import
NMS_CSV_HEADERS = ('name', 'address', 'agent_id')

FORMATS = ('sites-json', 'sites-csv', 'devices-json', 'devices-add-csv',
           'devices-nms-csv')

FORMAT_NOTES = {
    'sites-json': 'Site API v202211 request bodies, ready to POST/PUT',
    'sites-csv': 'flat site table for review - Kentik does not import site CSV',
    'devices-json': 'v5 admin API device request bodies, ready to POST',
    'devices-add-csv': "columns for Kentik's kentik_add_device.py loader",
    'devices-nms-csv': 'columns for the portal NMS bulk device import',
}


def site_requests(plan, include_unchanged: bool = False) -> list:
    '''
    Build the Kentik site API requests the plan represents

    Parameters:
        plan (Plan): the plan to export
        include_unchanged (bool): also emit sites needing no change

    Returns:
        list: dicts with method, path and body keys
    '''
    requests = []
    for entry in plan.entries:
        if entry.action == ACTION_NO_CHANGE and not include_unchanged:
            continue

        networks = entry.merged or {c: entry.site.networks(c)
                                    for c in CLASSIFICATIONS}
        payload = KENTIK.site_payload(entry.site, networks, entry.raw_site)

        if entry.kentik_id:
            method = 'PUT'
            path = SITE_PATH_UPDATE.format(site_id=entry.kentik_id)
        else:
            method = 'POST'
            path = SITE_PATH_CREATE

        requests.append({'method': method, 'path': path, 'body': payload})

    logger.debug('Built %d site request(s)', len(requests))
    return requests


def device_requests(plan, config) -> list:
    '''
    Build the Kentik device API requests for the reported device candidates

    Parameters:
        plan (Plan): the plan to export
        config (ProjectConfig): assembled configuration

    Returns:
        list: dicts with method, path and body keys
    '''
    site_ids = {}
    for entry in plan.entries:
        if entry.kentik_id:
            site_ids[entry.site.name] = entry.kentik_id

    requests = []
    for device in plan.devices:
        body = build_device_payload(config, device,
                                    site_ids.get(device.site_name, ''))
        requests.append({'method': 'POST', 'path': DEVICE_PATH_CREATE,
                         'body': body})

    logger.debug('Built %d device request(s)', len(requests))
    return requests


def site_csv_rows(plan, include_unchanged: bool = False) -> list:
    '''
    Build the flat site rows

    Parameters:
        plan (Plan): the plan to export
        include_unchanged (bool): also emit sites needing no change

    Returns:
        list: list of row dicts keyed by SITE_CSV_HEADERS
    '''
    rows = []
    for entry in plan.entries:
        if entry.action == ACTION_NO_CHANGE and not include_unchanged:
            continue
        networks = entry.merged or {c: entry.site.networks(c)
                                    for c in CLASSIFICATIONS}
        row = {'title': entry.site.name,
               'type': entry.site.site_type,
               'action': entry.action,
               'kentik_id': entry.kentik_id}
        for classification in CLASSIFICATIONS:
            column = CLASS_TO_KENTIK[classification]
            key = {'infrastructureNetworks': 'infrastructure_networks',
                   'userAccessNetworks': 'user_access_networks',
                   'otherNetworks': 'other_networks'}[column]
            row[key] = ','.join(sorted(networks.get(classification, [])))
        rows.append(row)
    return rows


def add_device_csv_rows(plan, config) -> list:
    '''
    Build rows in the column order Kentik's kentik_add_device.py expects

    Parameters:
        plan (Plan): the plan to export
        config (ProjectConfig): assembled configuration

    Returns:
        list: list of row dicts keyed by ADD_DEVICE_CSV_HEADERS
    '''
    rows = []
    for request in device_requests(plan, config):
        device = request['body']['device']
        rows.append({
            'siteid': device.get('site_id', ''),
            'devicename': device.get('device_name', ''),
            'devicedescription': device.get('device_description', ''),
            'sendingips': ','.join(device.get('sending_ips', [])),
            'v6add': '',
            'asn': '',
            'devicesnmpcommunity': '',
            'devicesamplerate': device.get('device_sample_rate', ''),
            'planid': device.get('plan_id', ''),
        })
    return rows


def nms_device_csv_rows(plan) -> list:
    '''
    Build rows for the portal's NMS bulk device import

    agent_id is left empty: the portal uses the first available agent when it
    is not specified.

    Parameters:
        plan (Plan): the plan to export

    Returns:
        list: list of row dicts keyed by NMS_CSV_HEADERS
    '''
    from ibx_kentik_prepop.summarise import sanitise_device_name
    rows = []
    for device in plan.devices:
        rows.append({'name': sanitise_device_name(device.name),
                     'address': device.mgmt_ip,
                     'agent_id': ''})
    return rows


def _csv_text(headers: tuple, rows: list) -> str:
    '''
    Render rows as CSV text

    Parameters:
        headers (tuple): column names, in order
        rows (list): list of row dicts

    Returns:
        str: CSV document
    '''
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(headers), extrasaction='ignore',
                            lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def applicable_formats(plan, include_unchanged: bool = False) -> list:
    '''
    Work out which artefacts have anything in them for this plan

    An empty artefact is worse than a missing one - a site JSON containing []
    or a CSV with only a header row looks like a successful export of nothing.

    Parameters:
        plan (Plan): the plan to export
        include_unchanged (bool): also emit sites needing no change

    Returns:
        list: format names from FORMATS, in order
    '''
    has_sites = bool(site_requests(plan, include_unchanged))
    has_devices = bool(plan.devices)
    formats = []
    for export_format in FORMATS:
        wanted = has_devices if export_format.startswith('devices') else has_sites
        if wanted:
            formats.append(export_format)
    return formats


def render(plan, config, export_format: str, include_unchanged: bool = False) -> str:
    '''
    Render one Kentik import artefact

    Parameters:
        plan (Plan): the plan to export
        config (ProjectConfig): assembled configuration
        export_format (str): one of FORMATS
        include_unchanged (bool): also emit sites needing no change

    Returns:
        str: the rendered document
    '''
    if export_format == 'sites-json':
        text = json.dumps(site_requests(plan, include_unchanged), indent=2)
    elif export_format == 'sites-csv':
        text = _csv_text(SITE_CSV_HEADERS, site_csv_rows(plan, include_unchanged))
    elif export_format == 'devices-json':
        text = json.dumps(device_requests(plan, config), indent=2)
    elif export_format == 'devices-add-csv':
        text = _csv_text(ADD_DEVICE_CSV_HEADERS, add_device_csv_rows(plan, config))
    elif export_format == 'devices-nms-csv':
        text = _csv_text(NMS_CSV_HEADERS, nms_device_csv_rows(plan))
    else:
        raise ValueError(f'Unknown export format {export_format!r}, '
                         f"expected one of {', '.join(FORMATS)}")
    return text


def suffix_for(export_format: str) -> str:
    '''
    File suffix for an export format

    Parameters:
        export_format (str): one of FORMATS

    Returns:
        str: filename suffix, e.g. '-sites.json'
    '''
    name, extension = export_format.rsplit('-', 1)
    return f'-{name}.{extension}'


def export(plan, config, prefix: str, include_unchanged: bool = False) -> list:
    '''
    Write every applicable Kentik import artefact alongside a path prefix

    Device artefacts are only written when the plan carries device candidates.

    Parameters:
        plan (Plan): the plan to export
        config (ProjectConfig): assembled configuration
        prefix (str): output path prefix, e.g. 'exports/tenant-a'
        include_unchanged (bool): also emit sites needing no change

    Returns:
        list: (path, note) tuples for the files written
    '''
    base = Path(prefix)
    if base.suffix:
        base = base.with_suffix('')

    written = []
    for export_format in applicable_formats(plan, include_unchanged):
        if base.parent and not base.parent.exists():
            base.parent.mkdir(parents=True, exist_ok=True)
        text = render(plan, config, export_format, include_unchanged)
        if not text.endswith('\n'):
            text += '\n'
        path = Path(f'{base}{suffix_for(export_format)}')
        path.write_text(text, encoding='utf-8')
        written.append((str(path), FORMAT_NOTES[export_format]))
        logger.info('Wrote %s (%s)', path, FORMAT_NOTES[export_format])

    return written

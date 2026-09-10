#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Command line interface for ibx_kentik_prepop

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

import argparse
import json
import logging
import sys
from ibx_kentik_prepop import export, report
from ibx_kentik_prepop.apply import apply_device_plan, apply_plan
from ibx_kentik_prepop.config import (DEFAULT_INI_FILE, build_config,
                                      validate_kentik_credentials,
                                      validate_source_credentials)
from ibx_kentik_prepop.model import TASK_DEVICES
from ibx_kentik_prepop.plan import (build_device_plan, build_plan,
                                    device_apply_problems, get_source)
from ibx_kentik_prepop.targets.kentik import KENTIK

logger = logging.getLogger(__name__)


EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FAILED = 3


def parseargs():
    '''
    Parse command line arguments

    Returns:
        Namespace: parsed arguments
    '''
    parser = argparse.ArgumentParser(
        description='Pre-populate Kentik sites and report device candidates '
                    'from Infoblox NIOS or Universal DDI data')
    parser.add_argument('--task', choices=['sites', 'devices'], default='sites',
                        help='what to import (default: sites). Sites must exist '
                             'before devices can be attached to them')
    parser.add_argument('--source', choices=['nios', 'uddi'], default='uddi',
                        help='Infoblox platform to read from (default: uddi)')
    parser.add_argument('--site-key', help='EA (NIOS) or tag (UDDI) key holding '
                                           'the site name')
    parser.add_argument('--class-key', help='optional EA/tag key overriding the '
                                            'Kentik address classification')
    parser.add_argument('--site-type-key', help='optional EA/tag key holding the '
                                                'Kentik site type')
    parser.add_argument('--list-keys', action='store_true',
                        help='list the EA/tag keys in use with counts and exit')
    parser.add_argument('--show-device', metavar='NAME', default=None,
                        help='read one device back from Kentik and print it, to '
                             'see exactly what the portal stored')
    parser.add_argument('--network-view', help='NIOS network view to restrict to')
    parser.add_argument('--ip-space', help='UDDI IP space id to restrict to')
    parser.add_argument('--site-filter', help='glob restricting which sites are planned')
    parser.add_argument('--include-address-blocks', action='store_true',
                        help='also read address blocks / network containers')
    parser.add_argument('--max-prefix-len', type=int,
                        help='aggregate summarised prefixes up to this length')
    parser.add_argument('--devices', action='store_true',
                        help='include the device candidate section on a site run')
    parser.add_argument('--use-insight', action='store_true',
                        help='use NIOS Network Insight discovered devices')
    parser.add_argument('--use-uai', action='store_true',
                        help='use Universal Asset Insights discovered assets')
    parser.add_argument('--use-gateways', action='store_true',
                        help='infer routers from the DHCP routers option')
    parser.add_argument('--device-mode', choices=['flow', 'nms'], default=None,
                        help='create devices for flow (default) or for NMS')
    parser.add_argument('--sending-ips', choices=['mgmt', 'all'], default=None,
                        help='flow exporter source addresses: the management IP '
                             '(default) or every discovered interface address')
    parser.add_argument('--snmp-mode',
                        choices=['none', 'community', 'agent-flow', 'agent-full'],
                        default=None,
                        help='how SNMP is collected: none (default), community '
                             '(Kentik polls with a community string from the '
                             'YAML), agent-flow (a Universal Agent polls to '
                             'enrich this flow device) or agent-full (the agent '
                             'also collects the full NMS metric set). NMS mode '
                             'implies agent-full')
    parser.add_argument('--sample-rate', type=int, default=None,
                        help='device_sample_rate for flow devices (default: 1)')
    parser.add_argument('--plan-name', default=None,
                        help='Kentik plan to create flow devices under '
                             "(default: 'Free Flowpak Plan')")
    parser.add_argument('--plan-id', type=int, default=None,
                        help='Kentik plan id, overriding --plan-name')
    parser.add_argument('--bgp-type', choices=['none', 'device', 'other_device'],
                        default=None,
                        help="device_bgp_type, required by Kentik (default: "
                             "none, meaning generic IP/ASN mapping)")
    parser.add_argument('--bgp-flowspec', action='store_true',
                        help='set device_bgp_flowspec (default: false)')
    parser.add_argument('--bgp-neighbor-asn', default=None,
                        help="your ASN, required when --bgp-type is 'device'")
    parser.add_argument('--bgp-neighbor-ip', default=None,
                        help="your IPv4 peering address, for --bgp-type 'device'")
    parser.add_argument('--bgp-neighbor-ip6', default=None,
                        help="your IPv6 peering address, for --bgp-type 'device'")
    parser.add_argument('--bgp-device-id', default=None,
                        help="id of the device to share a BGP table with, "
                             "required when --bgp-type is 'other_device'")
    parser.add_argument('--agent-id', default=None,
                        help='Kentik agent id for NMS devices (required for NMS)')
    parser.add_argument('--credential-name', default=None,
                        help='SNMP credential name for NMS devices')
    parser.add_argument('--monitoring-template-id', type=int, default=None,
                        help='NMS monitoring template id')
    parser.add_argument('--exclude-device', action='append', default=None,
                        metavar='NAME',
                        help='exclude a device by name or management IP '
                             '(repeatable)')
    parser.add_argument('--exclude-file', default=None,
                        help='file of device names/IPs to exclude, one per line')
    parser.add_argument('--update-devices', action='store_true',
                        help='update the site and sending IPs of devices that '
                             'already exist in Kentik')
    parser.add_argument('--allow-over-capacity', action='store_true',
                        help='proceed even when the plan has too few device '
                             'slots left')
    parser.add_argument('-o', '--output', choices=['table', 'csv', 'json'],
                        default='table', help='report format (default: table)')
    parser.add_argument('--outfile', help='write the report to this file')
    parser.add_argument('--export-kentik', metavar='PREFIX',
                        help='also write Kentik-shaped import artefacts using '
                             'this path prefix, e.g. exports/tenant-a')
    parser.add_argument('--export-include-unchanged', action='store_true',
                        help='include sites needing no change in the export')
    parser.add_argument('--go', action='store_true',
                        help='apply the plan to Kentik (default is a dry run)')
    parser.add_argument('-c', '--config', default=DEFAULT_INI_FILE,
                        help=f'credentials ini file (default: {DEFAULT_INI_FILE})')
    parser.add_argument('-y', '--yaml', help='optional YAML behaviour config')
    parser.add_argument('--gm', help='NIOS grid master address override')
    parser.add_argument('-d', '--debug', action='store_true', help='enable debug logging')
    parser.add_argument('-V', '--version', action='store_true', help='show version and exit')
    return parser.parse_args()


def setup_logging(debug: bool = False) -> None:
    '''
    Configure logging for the process

    Parameters:
        debug (bool): enable debug level logging

    Returns:
        None
    '''
    if debug:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)s %(levelname)s: %(message)s')
    else:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s: %(message)s')
    return


def list_keys(config) -> int:
    '''
    Print the EA/tag keys in use on the source platform

    Parameters:
        config (ProjectConfig): assembled configuration

    Returns:
        int: exit code
    '''
    source = get_source(config)
    keys = source.get_keys()
    if keys:
        print(f'{"key":40} objects')
        print(f'{"-" * 40} -------')
        for key, count in keys.items():
            print(f'{key:40} {count}')
    else:
        print('No metadata keys found - check credentials and permissions')
    return EXIT_OK


def show_device(config, name: str) -> int:
    '''
    Print a device as Kentik holds it

    The way to settle what the portal writes for a given setting: configure one
    device by hand, read it back with this, and compare against what the tool
    sends. The SNMP and agent fields are listed first because they are the ones
    that decide the collection method.

    Parameters:
        config (ProjectConfig): assembled configuration
        name (str): Kentik device name

    Returns:
        int: exit code
    '''
    exitcode = EXIT_OK
    kentik = KENTIK(config)
    device = kentik.get_device_by_name(name)

    if not device:
        print(f'Device {name!r} was not found in Kentik. '
              f'{kentik.error_text()}'.strip(), file=sys.stderr)
        exitcode = EXIT_FAILED
    else:
        interesting = ('device_name', 'id', 'device_subtype', 'device_flow_type',
                       'device_agent_type', 'snmp_enabled',
                       'snmp_disabled_reason', 'device_snmp_ip',
                       'device_snmp_community', 'device_snmp_v3_conf_enabled',
                       'minimize_snmp', 'flow_snmp_credential_name',
                       'monitoring_template_id', 'nms')
        print(f'SNMP and agent configuration of {name!r}:')
        for field in interesting:
            if field in device:
                print(f'  {field:32} {json.dumps(device[field])}')
        print('\nFull device as Kentik holds it:')
        print(json.dumps(device, indent=2, sort_keys=True))

    return exitcode


def emit(text: str, output: str, outfile: str) -> None:
    '''
    Print the rendered report, or confirm the files written

    Parameters:
        text (str): rendered report or file list
        output (str): report format
        outfile (str): output path

    Returns:
        None
    '''
    if outfile and output == 'csv':
        print(f'Wrote:\n{text}')
    elif outfile:
        print(f'Wrote {output} report to {outfile}')
    else:
        print(text)
    return


def main() -> int:
    '''
    Entry point

    Returns:
        int: exit code
    '''
    exitcode = EXIT_OK
    args = parseargs()
    setup_logging(args.debug)

    if args.version:
        print(f'ibx_kentik_prepop {__version__}')
        return exitcode

    config = build_config(args, ini_file=args.config, yaml_file=args.yaml)

    if args.list_keys:
        problems = [p for p in validate_source_credentials(config)
                    if 'site EA/tag' not in p]
        if problems:
            for problem in problems:
                print(f'ERROR: {problem}', file=sys.stderr)
            return EXIT_CONFIG
        return list_keys(config)

    if args.show_device:
        problems = validate_kentik_credentials(config)
        if problems:
            for problem in problems:
                print(f'ERROR: {problem}', file=sys.stderr)
            return EXIT_CONFIG
        return show_device(config, args.show_device)

    problems = validate_source_credentials(config)
    if problems:
        for problem in problems:
            print(f'ERROR: {problem}', file=sys.stderr)
        return EXIT_CONFIG

    kentik = None
    kentik_problems = validate_kentik_credentials(config)
    if kentik_problems:
        if args.go:
            for problem in kentik_problems:
                print(f'ERROR: {problem}', file=sys.stderr)
            return EXIT_CONFIG
        for problem in kentik_problems:
            logger.warning('%s - every site will be planned as a create', problem)
    else:
        kentik = KENTIK(config)

    if config.task == TASK_DEVICES:
        plan, config = build_device_plan(config, kentik)
    else:
        plan = build_plan(config, kentik)

    text = report.render(plan, args.output, args.outfile or '', dry_run=not args.go)
    emit(text, args.output, args.outfile or '')

    if args.export_kentik:
        written = export.export(plan, config, args.export_kentik,
                                args.export_include_unchanged)
        if written:
            print('\nKentik import artefacts:')
            for path, note in written:
                print(f'  {path}  ({note})')
        else:
            print('\nNothing to export - no sites need creating or updating.')

    if args.go and config.task == TASK_DEVICES:
        problems = device_apply_problems(config, plan)
        if problems:
            for problem in problems:
                print(f'ERROR: {problem}', file=sys.stderr)
            return EXIT_CONFIG

        results = apply_device_plan(config, plan, kentik)
        print(f"\nApplied: {len(results['created'])} created, "
              f"{len(results['updated'])} updated, "
              f"{len(results['existing'])} already existed, "
              f"{len(results['excluded'])} excluded, "
              f"{len(results['failed'])} failed")
        if results['failed']:
            print('Failed devices: ' + ', '.join(results['failed']), file=sys.stderr)
            exitcode = EXIT_FAILED
    elif args.go:
        results = apply_plan(config, plan, kentik)
        print(f"\nApplied: {len(results['created'])} created, "
              f"{len(results['updated'])} updated, "
              f"{len(results['unchanged'])} unchanged, "
              f"{len(results['failed'])} failed")
        for note in results['notes']:
            print(f'\nNOTE: {note}')
        if results['failed']:
            print('Failed sites: ' + ', '.join(results['failed']), file=sys.stderr)
            exitcode = EXIT_FAILED
    else:
        print('\nDry run only - no changes made. Re-run with --go to apply.')

    return exitcode

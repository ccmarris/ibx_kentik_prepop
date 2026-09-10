#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Flask web interface for ibx_kentik_prepop

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
import queue
import threading
from argparse import Namespace
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from ibx_kentik_prepop import export, report
from ibx_kentik_prepop.apply import apply_device_plan, apply_plan
from ibx_kentik_prepop.config import (DEFAULT_INI_FILE, INI_SECTIONS,
                                      build_config, read_ini,
                                      validate_kentik_credentials,
                                      validate_source_credentials)
from ibx_kentik_prepop.model import TASK_DEVICES
from ibx_kentik_prepop.plan import (build_device_plan, build_plan,
                                    device_apply_problems, get_source,
                                    plan_fingerprint)
from ibx_kentik_prepop.targets.kentik import KENTIK

logger = logging.getLogger(__name__)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]

# How long a streamed apply may run before the generator gives up waiting
APPLY_TIMEOUT_SECONDS = 900

# Resolved at startup from the command line
CONFIG_FILE = DEFAULT_INI_FILE
YAML_FILE = ''
# When locked, the ini chosen at startup cannot be overridden per request
LOCK_CONFIG = False

# Directories scanned for candidate ini files offered in the UI picker
EXTRA_INI_DIRS = (Path.home() / 'configs',)
MAX_INI_CANDIDATES = 200

app = Flask(__name__,
            static_folder=str(SCRIPT_DIR / 'static'),
            static_url_path='/static')

# Boolean form fields, mapped to their CLI flag
FLAG_FIELDS = ('include_address_blocks', 'devices',
               'use_insight', 'use_uai', 'use_gateways', 'update_devices',
               'allow_over_capacity', 'bgp_flowspec')

# String/int form fields, mapped to their CLI option
VALUE_FIELDS = ('task', 'source', 'site_key', 'class_key', 'site_type_key',
                'network_view', 'ip_space', 'site_filter', 'max_prefix_len',
                'device_mode', 'sending_ips', 'sample_rate', 'plan_name',
                'plan_id',
                'agent_id', 'credential_name', 'monitoring_template_id',
                'bgp_type', 'bgp_neighbor_asn', 'bgp_neighbor_ip',
                'bgp_neighbor_ip6', 'bgp_device_id')


def resolve_config_file(requested: str) -> tuple:
    '''
    Resolve the credentials ini file for one request

    An override is only accepted when the server was not started with
    --lock-config, and only when the file exists and actually looks like a
    credentials ini for this tool. Values are never returned to the client.

    Parameters:
        requested (str): path supplied by the client, empty for the default

    Returns:
        tuple: (resolved path str, error message str) - one of the two is empty
    '''
    path = CONFIG_FILE
    error = ''
    wanted = str(requested or '').strip()

    if wanted and wanted != CONFIG_FILE:
        if LOCK_CONFIG:
            error = ('This server was started with --lock-config, so the '
                     'credentials file cannot be changed from the UI')
        else:
            candidate = Path(wanted).expanduser()
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT / candidate)
            candidate = candidate.resolve()
            if not candidate.is_file():
                error = f'{candidate} is not a readable file'
            elif not any(read_ini(str(candidate)).get(s)
                         for s in INI_SECTIONS):
                error = (f'{candidate} has no usable [NIOS], [UDDI] or '
                         f'[KENTIK] section')
            else:
                path = str(candidate)
                logger.info('Using credentials file %s for this request', path)

    if error:
        logger.error('Credentials file rejected: %s', error)

    return path, error


def candidate_inis() -> list:
    '''
    Find ini files that could be used as credentials for this tool

    Only file paths and the section names present are reported - never any
    values.

    Parameters:
        None

    Returns:
        list: dicts with path, name and sections keys
    '''
    directories = [Path(CONFIG_FILE).expanduser().resolve().parent, PROJECT_ROOT]
    directories.extend(EXTRA_INI_DIRS)

    seen = set()
    candidates = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob('*.ini')):
            resolved = str(path.resolve())
            if resolved in seen or len(candidates) >= MAX_INI_CANDIDATES:
                continue
            seen.add(resolved)
            creds = read_ini(resolved)
            sections = [s for s in INI_SECTIONS if creds.get(s)]
            if sections:
                candidates.append({'path': resolved,
                                   'name': path.name,
                                   'sections': sections})

    logger.debug('Found %d candidate credentials file(s)', len(candidates))
    return candidates


def form_namespace(body: dict) -> Namespace:
    '''
    Turn a posted JSON body into the namespace build_config expects

    Parameters:
        body (dict): request JSON

    Returns:
        Namespace: argparse-like namespace
    '''
    fields = {}
    for field in VALUE_FIELDS:
        value = body.get(field)
        fields[field] = value if value not in (None, '') else None
    for field in FLAG_FIELDS:
        fields[field] = bool(body.get(field))
    fields['gm'] = body.get('gm') or None
    for numeric in ('max_prefix_len', 'plan_id', 'monitoring_template_id',
                    'sample_rate'):
        if fields.get(numeric):
            fields[numeric] = int(fields[numeric])
    fields['exclude_device'] = [str(v) for v in (body.get('exclude_device') or [])]
    fields['exclude_file'] = None
    fields['sending_ip_map'] = {str(k): [str(a) for a in v]
                                for k, v in (body.get('sending_ip_map') or {}).items()}
    return Namespace(**fields)


def make_plan(config, kentik):
    '''
    Build the plan for the configured task

    Parameters:
        config (ProjectConfig): assembled configuration
        kentik (KENTIK): Kentik target, or None

    Returns:
        tuple: (Plan, ProjectConfig) - the config may carry a resolved plan id
    '''
    if config.task == TASK_DEVICES:
        plan, config = build_device_plan(config, kentik)
    else:
        plan = build_plan(config, kentik)
    return plan, config


@app.route('/')
def index():
    '''
    Serve the web UI

    Returns:
        Response: index.html
    '''
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/health', methods=['GET'])
def health():
    '''
    Health check

    Returns:
        Response: JSON status
    '''
    return jsonify({'status': 'ok', 'version': __version__})


@app.route('/api/config', methods=['GET'])
def get_config():
    '''
    Report the non-secret configuration the server resolved

    Accepts an optional config_file query parameter to preview a different
    credentials ini without restarting the server.

    Returns:
        Response: JSON config summary
    '''
    ini_file, error = resolve_config_file(request.args.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    config = build_config(form_namespace({}), ini_file=ini_file, yaml_file=YAML_FILE)
    return jsonify({
        'ini_file': ini_file,
        'default_ini_file': CONFIG_FILE,
        'lock_config': LOCK_CONFIG,
        'yaml_file': YAML_FILE,
        'nios': {'gm': config.nios.gm,
                 'credentials': bool(config.nios.gm and config.nios.password),
                 'wapi_version': config.nios.wapi_version},
        'uddi': {'base_url': config.uddi.base_url,
                 'credentials': bool(config.uddi.api_key)},
        'kentik': {'base_url': config.kentik.base_url,
                   'grpc_base_url': config.kentik.grpc_base_url,
                   'credentials': not validate_kentik_credentials(config)},
        'defaults': {'source': config.source,
                     'site_key': config.site.site_key,
                     'class_key': config.site.class_key,
                     'max_prefix_len': config.site.max_prefix_len},
        'device_writes_enabled': False,
    })


@app.route('/api/inis', methods=['GET'])
def get_inis():
    '''
    List the credentials ini files the operator can choose between

    Returns:
        Response: JSON list of candidates
    '''
    return jsonify({'default': CONFIG_FILE,
                    'lock_config': LOCK_CONFIG,
                    'candidates': candidate_inis()})


@app.route('/api/keys', methods=['GET'])
def get_keys():
    '''
    List the EA/tag keys in use on the selected source with counts

    Returns:
        Response: JSON key -> count, or an error
    '''
    ini_file, error = resolve_config_file(request.args.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    body = {'source': request.args.get('source', 'uddi'),
            'network_view': request.args.get('network_view', ''),
            'ip_space': request.args.get('ip_space', ''),
            'site_key': 'placeholder'}
    config = build_config(form_namespace(body), ini_file=ini_file, yaml_file=YAML_FILE)

    problems = [p for p in validate_source_credentials(config) if 'site EA/tag' not in p]
    if problems:
        response = jsonify({'error': '; '.join(problems)}), 400
    else:
        keys = get_source(config).get_keys()
        response = jsonify({'keys': keys})
    return response


@app.route('/api/kentik-check', methods=['GET'])
def kentik_check():
    '''
    Read-only probe of the Kentik API, so credentials can be proven before an
    apply is attempted

    Issues GET /sites and reports what came back. Nothing is written.

    Returns:
        Response: JSON with ok, sites, base_url and any error detail
    '''
    ini_file, error = resolve_config_file(request.args.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    config = build_config(form_namespace({}), ini_file=ini_file, yaml_file=YAML_FILE)
    problems = validate_kentik_credentials(config)
    if problems:
        return jsonify({'ok': False, 'error': '; '.join(problems),
                        'base_url': config.kentik.grpc_base_url}), 200

    kentik = KENTIK(config)
    sites = kentik.get_sites()
    ok = not kentik.last_error

    return jsonify({'ok': ok,
                    'sites': len(sites),
                    'base_url': config.kentik.grpc_base_url,
                    'error': kentik.error_text()})


@app.route('/api/kentik-plans', methods=['GET'])
def kentik_plans():
    '''
    List the Kentik licence plans with their remaining device capacity

    Returns:
        Response: JSON list of plans
    '''
    ini_file, error = resolve_config_file(request.args.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    config = build_config(form_namespace({}), ini_file=ini_file, yaml_file=YAML_FILE)
    problems = validate_kentik_credentials(config)
    if problems:
        return jsonify({'error': '; '.join(problems)}), 400

    kentik = KENTIK(config)
    plans = [kentik.plan_capacity(p) for p in kentik.list_plans()]
    return jsonify({'plans': plans, 'default': config.device.plan_name,
                    'error': kentik.error_text()})


@app.route('/api/kentik-nms', methods=['GET'])
def kentik_nms():
    '''
    List the NMS agents and SNMP credential groups an NMS device can use

    Returns:
        Response: JSON with agents and credentials
    '''
    ini_file, error = resolve_config_file(request.args.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    config = build_config(form_namespace({}), ini_file=ini_file, yaml_file=YAML_FILE)
    problems = validate_kentik_credentials(config)
    if problems:
        return jsonify({'error': '; '.join(problems)}), 400

    kentik = KENTIK(config)
    agents = []
    for agent in kentik.list_agents():
        agents.append({'id': str(agent.get('id', '')),
                       'name': str(agent.get('name')
                                   or agent.get('alias')
                                   or agent.get('hostname') or ''),
                       'status': str(agent.get('status', ''))})
    credentials = []
    for credential in kentik.list_credentials():
        credentials.append({'name': str(credential.get('name', '')),
                            'id': str(credential.get('id', ''))})
    return jsonify({'agents': agents, 'credentials': credentials})


@app.route('/api/plan', methods=['POST'])
def post_plan():
    '''
    Run a dry run and return the plan as JSON

    Returns:
        Response: JSON plan, or an error
    '''
    body = request.get_json(silent=True) or {}
    ini_file, error = resolve_config_file(body.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    config = build_config(form_namespace(body), ini_file=ini_file, yaml_file=YAML_FILE)

    problems = validate_source_credentials(config)
    if problems:
        return jsonify({'error': '; '.join(problems)}), 400

    kentik = None
    kentik_problems = validate_kentik_credentials(config)
    if not kentik_problems:
        kentik = KENTIK(config)

    plan, config = make_plan(config, kentik)
    payload = plan.as_dict()
    payload['ini_file'] = ini_file
    payload['fingerprint'] = plan_fingerprint(plan)
    if config.task == TASK_DEVICES:
        payload['apply_problems'] = device_apply_problems(config, plan)
    payload['kentik_available'] = kentik is not None
    payload['kentik_problems'] = kentik_problems
    payload['table'] = report.render_table(plan)
    return jsonify(payload)


@app.route('/api/export', methods=['POST'])
def post_export():
    '''
    Build the plan and return every Kentik import artefact in one response

    The plan is rebuilt server-side from the posted form, so the artefacts
    always match the data as it is now rather than a cached dry run.

    Returns:
        Response: JSON with a files list of filename/note/content
    '''
    body = request.get_json(silent=True) or {}
    ini_file, error = resolve_config_file(body.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    config = build_config(form_namespace(body), ini_file=ini_file, yaml_file=YAML_FILE)
    problems = validate_source_credentials(config)
    if problems:
        return jsonify({'error': '; '.join(problems)}), 400

    kentik = None
    if not validate_kentik_credentials(config):
        kentik = KENTIK(config)

    plan, config = make_plan(config, kentik)
    include_unchanged = bool(body.get('export_include_unchanged'))
    prefix = str(body.get('export_prefix') or 'kentik-import')

    files = []
    for export_format in export.applicable_formats(plan, include_unchanged):
        files.append({
            'format': export_format,
            'filename': f'{prefix}{export.suffix_for(export_format)}',
            'note': export.FORMAT_NOTES[export_format],
            'content': export.render(plan, config, export_format, include_unchanged),
        })

    logger.info('Export built %d artefact(s) for %s', len(files), prefix)
    return jsonify({'prefix': prefix,
                    'stats': plan.stats(),
                    'kentik_available': kentik is not None,
                    'files': files})


@app.route('/api/apply', methods=['POST'])
def post_apply():
    '''
    Apply the plan to Kentik in-process, streaming a result per site

    The request must carry confirm: true and the fingerprint of the dry run the
    operator reviewed. The plan is rebuilt here and refused if its fingerprint
    no longer matches, so what gets written is always what was on screen.

    Events are SSE frames holding one JSON object each, with a type of start,
    site, note, error or done.

    Returns:
        Response: text/event-stream, or a JSON error
    '''
    body = request.get_json(silent=True) or {}

    if not body.get('confirm'):
        return jsonify({'error': 'confirm must be true to apply the plan'}), 400

    expected = str(body.get('fingerprint') or '')
    if not expected:
        return jsonify({'error': 'Run a dry run first - the apply needs the '
                                 'fingerprint of the plan you reviewed'}), 400

    ini_file, error = resolve_config_file(body.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    config = build_config(form_namespace(body), ini_file=ini_file, yaml_file=YAML_FILE)

    problems = validate_source_credentials(config) + validate_kentik_credentials(config)
    if problems:
        return jsonify({'error': '; '.join(problems)}), 400

    kentik = KENTIK(config)
    plan, config = make_plan(config, kentik)
    fingerprint = plan_fingerprint(plan)

    if fingerprint != expected:
        logger.warning('Refusing apply: plan fingerprint %s does not match the '
                       'reviewed %s', fingerprint, expected)
        return jsonify({'error': 'The plan changed since your dry run, so it '
                                 'was not applied. Run the dry run again and '
                                 'review the new diff.',
                        'fingerprint': fingerprint,
                        'expected': expected}), 409

    if config.task == TASK_DEVICES:
        blocked = device_apply_problems(config, plan)
        if blocked:
            return jsonify({'error': '; '.join(blocked)}), 400

    logger.info('Applying %s plan %s from the web interface', config.task,
                fingerprint)
    runner = apply_device_plan if config.task == TASK_DEVICES else apply_plan

    def generate():
        '''
        Run the apply on a worker thread and stream its events as they arrive
        '''
        events = queue.Queue()

        def run():
            try:
                runner(config, plan, kentik, on_event=events.put)
            except Exception as exc:
                logger.exception('Apply failed')
                events.put({'type': 'error', 'message': str(exc)})
            finally:
                events.put(None)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()

        while True:
            try:
                event = events.get(timeout=APPLY_TIMEOUT_SECONDS)
            except queue.Empty:
                yield _frame({'type': 'error',
                              'message': f'Apply produced no progress for '
                                         f'{APPLY_TIMEOUT_SECONDS}s, giving up '
                                         f'on the stream'})
                break
            if event is None:
                break
            yield _frame(event)

    return Response(stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


def _frame(event: dict) -> str:
    '''
    Render one event as an SSE frame

    Parameters:
        event (dict): event payload

    Returns:
        str: SSE frame text
    '''
    return f'data: {json.dumps(event)}\n\n'


def parseargs():
    '''
    Parse command line arguments

    Returns:
        Namespace: parsed arguments
    '''
    parser = argparse.ArgumentParser(description='Web interface for ibx_kentik_prepop')
    parser.add_argument('-c', '--config', default=DEFAULT_INI_FILE,
                        help=f'credentials ini file (default: {DEFAULT_INI_FILE})')
    parser.add_argument('-y', '--yaml', help='optional YAML behaviour config')
    parser.add_argument('--host', default='127.0.0.1',
                        help='address to bind to (default: 127.0.0.1)')
    parser.add_argument('-p', '--port', type=int, default=5000,
                        help='port to listen on (default: 5000)')
    parser.add_argument('--lock-config', action='store_true',
                        help='refuse credentials ini overrides from the UI')
    parser.add_argument('--debug-flask', action='store_true',
                        help='enable Flask debug mode and auto-reload')
    parser.add_argument('-d', '--debug', action='store_true', help='enable debug logging')
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


def main() -> int:
    '''
    Entry point

    Returns:
        int: exit code
    '''
    global CONFIG_FILE, YAML_FILE, LOCK_CONFIG
    args = parseargs()
    setup_logging(args.debug)

    CONFIG_FILE = args.config
    YAML_FILE = args.yaml or ''
    LOCK_CONFIG = args.lock_config

    logger.info('Credentials come from %s - operators of this UI act as that '
                'identity', CONFIG_FILE)
    if LOCK_CONFIG:
        logger.info('Credentials file overrides from the UI are disabled')
    else:
        logger.info('Operators may point the UI at another readable ini file; '
                    'use --lock-config to prevent that')
    logger.info('Serving on http://%s:%d', args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug_flask, threaded=True)
    return 0

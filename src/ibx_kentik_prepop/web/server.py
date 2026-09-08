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
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from ibx_kentik_prepop import export, report
from ibx_kentik_prepop.config import (DEFAULT_INI_FILE, INI_SECTIONS,
                                      build_config, read_ini,
                                      validate_kentik_credentials,
                                      validate_source_credentials)
from ibx_kentik_prepop.plan import build_plan, get_source
from ibx_kentik_prepop.targets.kentik import KENTIK

logger = logging.getLogger(__name__)


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]
CLI_SCRIPT = PROJECT_ROOT / 'ibx_kentik_prepop.py'

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
FLAG_FIELDS = {
    'include_address_blocks': '--include-address-blocks',
    'replace_networks': '--replace-networks',
    'devices': '--devices',
    'use_insight': '--use-insight',
    'use_uai': '--use-uai',
    'use_gateways': '--use-gateways',
}

# String/int form fields, mapped to their CLI option
VALUE_FIELDS = {
    'source': '--source',
    'site_key': '--site-key',
    'class_key': '--class-key',
    'site_type_key': '--site-type-key',
    'network_view': '--network-view',
    'ip_space': '--ip-space',
    'site_filter': '--site-filter',
    'max_prefix_len': '--max-prefix-len',
}


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
    if fields.get('max_prefix_len'):
        fields['max_prefix_len'] = int(fields['max_prefix_len'])
    return Namespace(**fields)


def cli_command(body: dict, config_file: str = '', extra: list = None) -> list:
    '''
    Build the CLI command line matching a posted form

    Parameters:
        body (dict): request JSON
        config_file (str): resolved credentials ini file
        extra (list): additional arguments to append

    Returns:
        list: argv list for subprocess
    '''
    command = [sys.executable, str(CLI_SCRIPT), '-c', config_file or CONFIG_FILE]
    if YAML_FILE:
        command.extend(['-y', YAML_FILE])
    for field, option in VALUE_FIELDS.items():
        value = body.get(field)
        if value not in (None, ''):
            command.extend([option, str(value)])
    for field, option in FLAG_FIELDS.items():
        if body.get(field):
            command.append(option)
    command.extend(extra or [])
    return command


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

    plan = build_plan(config, kentik)
    payload = plan.as_dict()
    payload['ini_file'] = ini_file
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

    plan = build_plan(config, kentik)
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
    Stream an apply run as Server-Sent Events

    The request must carry confirm: true. The final event is always
    data: [EXIT:<returncode>]

    Returns:
        Response: text/event-stream
    '''
    body = request.get_json(silent=True) or {}

    if not body.get('confirm'):
        return jsonify({'error': 'confirm must be true to apply the plan'}), 400

    ini_file, error = resolve_config_file(body.get('config_file', ''))
    if error:
        return jsonify({'error': error}), 400

    command = cli_command(body, ini_file, ['--go'])
    logger.info('Apply requested: %s', ' '.join(command))

    def generate():
        '''
        Yield SSE events from the CLI subprocess
        '''
        yield f"data: $ {' '.join(command)}\n\n"
        process = subprocess.Popen(command, cwd=str(PROJECT_ROOT),
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   text=True, bufsize=1,
                                   env=dict(os.environ))
        for line in process.stdout:
            yield f"data: {line.rstrip()}\n\n"
        process.wait()
        yield f'data: [EXIT:{process.returncode}]\n\n'

    return Response(stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


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

#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''

 Description:

    Apply the plan - create and update Kentik sites

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

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from ibx_kentik_prepop.model import ACTION_CREATE, ACTION_NO_CHANGE, ACTION_UPDATE
from ibx_kentik_prepop.targets.kentik import DEVICE_WRITE_REFUSED

logger = logging.getLogger(__name__)


def load_state(path: str) -> dict:
    '''
    Load the state file recording what this tool has created

    Parameters:
        path (str): path to the state file

    Returns:
        dict: state document, a fresh one when the file is missing
    '''
    state = {'sites': {}, 'runs': []}
    state_path = Path(path)
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                state = loaded
                state.setdefault('sites', {})
                state.setdefault('runs', [])
                logger.debug('Loaded state for %d site(s) from %s',
                             len(state['sites']), path)
        except (OSError, ValueError) as exc:
            logger.error('Could not read state file %s: %s', path, exc)
    return state


def save_state(path: str, state: dict) -> bool:
    '''
    Write the state file

    Parameters:
        path (str): path to the state file
        state (dict): state document

    Returns:
        bool: True on success
    '''
    written = False
    try:
        Path(path).write_text(json.dumps(state, indent=2), encoding='utf-8')
        written = True
        logger.debug('Wrote state file %s', path)
    except OSError as exc:
        logger.error('Could not write state file %s: %s', path, exc)
    return written


def apply_plan(config, plan, kentik, on_event=None) -> dict:
    '''
    Create and update Kentik sites according to the plan

    Sites are never deleted. Device writes are refused: the device section of
    the report is informational in this version.

    Parameters:
        config (ProjectConfig): assembled configuration
        plan (Plan): the plan to apply
        kentik (KENTIK): initialised Kentik target
        on_event (callable): optional callback taking one event dict, called as
            each site is processed so a caller can report progress live

    Returns:
        dict: results with created, updated, unchanged, failed and notes
    '''
    results = {'created': [], 'updated': [], 'unchanged': [], 'failed': [],
               'notes': [], 'errors': {}}

    def emit(event: dict) -> None:
        '''
        Hand an event to the caller's callback, if it supplied one
        '''
        if on_event is not None:
            on_event(event)
        return
    state = load_state(config.state_file)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')

    total = sum(1 for e in plan.entries if e.action != ACTION_NO_CHANGE)
    emit({'type': 'start', 'sites': len(plan.entries), 'to_change': total,
          'stats': plan.stats()})

    for entry in plan.entries:
        name = entry.site.name
        networks = entry.merged or {c: entry.site.networks(c)
                                    for c in entry.site.counts()}

        if entry.action == ACTION_NO_CHANGE:
            results['unchanged'].append(name)
            logger.info('Site %s is already up to date', name)
            emit({'type': 'site', 'site': name, 'action': entry.action,
                  'status': 'unchanged', 'kentik_id': entry.kentik_id})
            continue

        if entry.action == ACTION_CREATE:
            created = kentik.create_site(entry.site, networks)
            if created is None:
                error = kentik.error_text()
                results['failed'].append(name)
                results['errors'][name] = error
                emit({'type': 'site', 'site': name, 'action': entry.action,
                      'status': 'failed', 'error': error})
            else:
                site_id = str(created.get('id', ''))
                results['created'].append(name)
                state['sites'][name] = {'id': site_id, 'action': ACTION_CREATE,
                                        'applied': now,
                                        'site_key': plan.site_key,
                                        'source': plan.source}
                emit({'type': 'site', 'site': name, 'action': entry.action,
                      'status': 'created', 'kentik_id': site_id,
                      'added': entry.added})
        elif entry.action == ACTION_UPDATE:
            updated = kentik.update_site(entry.kentik_id, entry.site, networks,
                                         entry.raw_site)
            if updated is None:
                error = kentik.error_text()
                results['failed'].append(name)
                results['errors'][name] = error
                emit({'type': 'site', 'site': name, 'action': entry.action,
                      'status': 'failed', 'error': error})
            else:
                results['updated'].append(name)
                record = state['sites'].get(name, {})
                record.update({'id': entry.kentik_id, 'action': ACTION_UPDATE,
                               'applied': now, 'site_key': plan.site_key,
                               'source': plan.source})
                state['sites'][name] = record
                emit({'type': 'site', 'site': name, 'action': entry.action,
                      'status': 'updated', 'kentik_id': entry.kentik_id,
                      'added': entry.added, 'removed': entry.removed})

    if plan.devices:
        results['notes'].append(DEVICE_WRITE_REFUSED)
        logger.warning('%d device candidate(s) reported but not created. %s',
                       len(plan.devices), DEVICE_WRITE_REFUSED)
        emit({'type': 'note', 'message': DEVICE_WRITE_REFUSED,
              'devices': len(plan.devices)})

    state['runs'].append({
        'applied': now,
        'source': plan.source,
        'site_key': plan.site_key,
        'created': len(results['created']),
        'updated': len(results['updated']),
        'unchanged': len(results['unchanged']),
        'failed': len(results['failed']),
    })
    state['runs'] = state['runs'][-50:]
    save_state(config.state_file, state)

    logger.info('Apply complete: %d created, %d updated, %d unchanged, %d failed',
                len(results['created']), len(results['updated']),
                len(results['unchanged']), len(results['failed']))
    emit({'type': 'done',
          'created': len(results['created']),
          'updated': len(results['updated']),
          'unchanged': len(results['unchanged']),
          'failed': len(results['failed']),
          'errors': results['errors']})
    return results

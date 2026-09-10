'use strict';

const VALUE_FIELDS = ['config_file', 'source', 'site_key', 'class_key',
                      'site_type_key', 'network_view', 'ip_space', 'site_filter',
                      'max_prefix_len', 'export_prefix', 'device_mode',
                      'snmp_mode', 'sending_ips', 'sample_rate', 'plan_id',
                      'agent_id', 'credential_name', 'monitoring_template_id',
                      'bgp_type', 'bgp_neighbor_asn', 'bgp_neighbor_ip',
                      'bgp_neighbor_ip6', 'bgp_device_id'];
const FLAG_FIELDS = ['include_address_blocks', 'devices',
                     'use_insight', 'use_uai', 'use_gateways',
                     'export_include_unchanged', 'update_devices',
                     'allow_over_capacity', 'bgp_flowspec'];

let currentPlan = null;
let currentTask = 'sites';
let excluded = {};
let sendingIps = {};

function el(id) {
  return document.getElementById(id);
}

function formBody() {
  const body = { task: currentTask };
  VALUE_FIELDS.forEach(function (field) { body[field] = el(field).value.trim(); });
  FLAG_FIELDS.forEach(function (field) { body[field] = el(field).checked; });
  body.exclude_device = Object.keys(excluded).filter(function (name) {
    return excluded[name];
  });
  body.sending_ip_map = sendingIps;
  return body;
}

function setTask(task) {
  currentTask = task;
  currentPlan = null;
  el('task_sites').classList.toggle('active', task === 'sites');
  el('task_devices').classList.toggle('active', task === 'devices');
  el('site_fields').classList.toggle('hidden', task !== 'sites');
  el('scope_fields').classList.toggle('hidden', task !== 'sites');
  el('devices_row').classList.toggle('hidden', task !== 'sites');
  el('device_fields').classList.toggle('hidden', task !== 'devices');
  el('run_apply').textContent = task === 'devices'
    ? 'Apply devices to Kentik' : 'Apply to Kentik';
  ['stats_card', 'warnings_card', 'sites_card', 'subnets_card', 'devices_card',
   'device_card', 'export_card', 'apply_card'].forEach(function (id) {
    show(id, false);
  });
  el('run_apply').disabled = true;
  el('apply_hint').textContent =
    'Apply is enabled once a dry run finds ' +
    (task === 'devices' ? 'devices to create.' : 'sites to create or update.');
  setStatus(task === 'devices'
    ? 'Device data: sites must already exist in Kentik for devices to be attached to them.'
    : 'Sites data.', false);
}

function updateDeviceMode() {
  const nms = el('device_mode').value === 'nms';
  el('flow_fields').classList.toggle('hidden', nms);
  // An NMS device is fully monitored by an agent by definition.
  if (nms && el('snmp_mode').value.indexOf('agent') !== 0) {
    el('snmp_mode').value = 'agent-full';
  }
  updateSnmpMode();
}

function updateSnmpMode() {
  const agent = el('snmp_mode').value.indexOf('agent') === 0;
  el('agent_fields').classList.toggle('hidden', !agent);
  if (agent && !el('agent_id').options.length) { loadNms(); }
}

function updateBgpType() {
  const value = el('bgp_type').value;
  el('bgp_device_fields').classList.toggle('hidden', value !== 'device');
  el('bgp_other_fields').classList.toggle('hidden', value !== 'other_device');
}

function deviceDetail(entry) {
  if (entry.exclude_reason) { return entry.exclude_reason; }
  if (entry.mismatch && Object.keys(entry.mismatch).length) {
    return Object.keys(entry.mismatch).map(function (field) {
      return field + ': kentik=' + entry.mismatch[field].kentik +
             ' derived=' + entry.mismatch[field].derived;
    }).join('; ');
  }
  return entry.kentik_id ? 'id ' + entry.kentik_id : '';
}

function renderDevicePlan(plan) {
  const rows = (plan.device_entries || []).map(function (entry) {
    const name = entry.name;
    const interfaces = (entry.interfaces || []).map(function (iface) {
      return iface.address;
    }).filter(function (address) { return address; });
    const selected = sendingIps[name] || entry.sending_ips || [];
    const options = interfaces.map(function (address) {
      return '<option value="' + escapeHtml(address) + '"' +
        (selected.indexOf(address) >= 0 ? ' selected' : '') + '>' +
        escapeHtml(address) + '</option>';
    }).join('');
    const picker = interfaces.length
      ? '<select multiple size="' + Math.min(interfaces.length, 3) +
        '" data-device="' + escapeHtml(name) + '">' + options + '</select>'
      : '<span class="muted">none</span>';

    return '<tr data-device="' + escapeHtml(name) + '"' +
      (entry.excluded ? ' class="excluded"' : '') + '>' +
      '<td class="keep"><input type="checkbox" data-include="' + escapeHtml(name) +
      '"' + (entry.excluded ? '' : ' checked') + '></td>' +
      '<td>' + escapeHtml(name) + '</td>' +
      '<td>' + escapeHtml(entry.kentik_name || '') + '</td>' +
      '<td><span class="chip ' + escapeHtml(entry.excluded ? 'excluded' : entry.action) +
      '">' + escapeHtml(entry.excluded ? 'excluded' : entry.action) + '</span></td>' +
      '<td>' + escapeHtml(entry.role) + '</td>' +
      '<td>' + escapeHtml(entry.mgmt_ip) + '</td>' +
      '<td class="keep">' + picker + '</td>' +
      '<td>' + escapeHtml(entry.site_name || '') + '</td>' +
      '<td>' + escapeHtml(entry.site_match || '') + '</td>' +
      '<td>' + escapeHtml(entry.description || '') + '</td>' +
      '<td>' + escapeHtml(entry.origin) + '</td>' +
      '<td>' + escapeHtml(deviceDetail(entry)) + '</td></tr>';
  }).join('');

  el('device_plan_table').innerHTML =
    '<thead><tr><th>use</th><th>device</th><th>kentik name</th><th>action</th>' +
    '<th>role</th><th>mgmt ip</th><th>sending ips</th><th>site</th>' +
    '<th>matched by</th><th>description</th><th>origin</th><th>detail</th>' +
    '</tr></thead><tbody>' +
    rows + '</tbody>';

  el('device_plan_table').querySelectorAll('input[data-include]').forEach(function (box) {
    box.addEventListener('change', function () {
      excluded[box.dataset.include] = !box.checked;
      box.closest('tr').classList.toggle('excluded', !box.checked);
      invalidatePlan('Selection changed');
    });
  });
  el('device_plan_table').querySelectorAll('select[data-device]').forEach(function (picker) {
    picker.addEventListener('change', function () {
      sendingIps[picker.dataset.device] = Array.from(picker.selectedOptions)
        .map(function (option) { return option.value; });
      invalidatePlan('Sending IPs changed');
    });
  });

  const capacity = plan.capacity || {};
  const slots = (capacity.remaining === undefined || capacity.remaining === null)
    ? '' : capacity.remaining + ' of ' + capacity.max_devices + ' slot(s) left';
  el('device_summary').textContent =
    plan.stats.device_actions.create + ' to create, ' +
    plan.stats.device_actions.exists + ' already in Kentik, ' +
    plan.stats.device_actions.update + ' to update, ' +
    plan.stats.excluded_devices + ' excluded. Mode: ' + plan.device_mode +
    (plan.plan_name ? '. Plan: ' + plan.plan_name : '') +
    (slots ? ' (' + slots + ')' : '');
  show('device_card', true);
}

let refreshTimer = null;
let planInFlight = false;
let refreshQueued = false;

function invalidatePlan(message) {
  currentPlan = null;
  el('run_apply').disabled = true;
  el('apply_hint').textContent = message + ' - refreshing the plan...';
  schedulePlanRefresh();
}

function schedulePlanRefresh() {
  // Changing a selection only ever shrinks or re-points the change set, so
  // rather than dead-ending on a stale plan the dry run is re-run and the
  // fingerprint stays in step with what is on screen.
  if (refreshTimer) { clearTimeout(refreshTimer); }
  refreshTimer = setTimeout(function () {
    refreshTimer = null;
    if (planInFlight) {
      refreshQueued = true;
      return;
    }
    runPlan({ quiet: true });
  }, 600);
}

function blockingReasons(plan) {
  const reasons = (plan.apply_problems || []).slice();
  if (!plan.kentik_available) {
    reasons.push('Kentik credentials are not configured in ' +
                 (plan.ini_file || 'the credentials file'));
  }
  return reasons;
}

function renderApplyBlockers(reasons) {
  const node = el('apply_blockers');
  if (!node) { return; }
  if (!reasons.length) {
    node.innerHTML = '';
    node.classList.add('hidden');
    return;
  }
  node.innerHTML = '<strong>Apply is disabled</strong><ul>' +
    reasons.map(function (reason) {
      return '<li>' + escapeHtml(reason) + '</li>';
    }).join('') + '</ul>';
  node.classList.remove('hidden');
}

async function loadPlans(event) {
  if (event) { event.preventDefault(); }
  const params = new URLSearchParams({ config_file: el('config_file').value.trim() });
  try {
    const response = await fetch('/api/kentik-plans?' + params.toString());
    const data = await response.json();
    if (data.error) { setStatus(data.error, true); return; }
    const plans = data.plans || [];
    if (!plans.length) {
      el('plan_select').innerHTML =
        '<option value="">-- none returned by the API --</option>';
      setStatus('The plans API returned nothing - licensing may not be visible ' +
                'to this service account. Type the plan id instead.', true);
      return;
    }

    const wanted = String(data.default || '').replace(/_/g, ' ').toLowerCase();
    let preferred = plans.filter(function (plan) {
      return String(plan.name).replace(/_/g, ' ').toLowerCase() === wanted;
    })[0];
    if (!preferred) {
      preferred = plans.filter(function (plan) {
        return String(plan.name).toLowerCase().indexOf('free') >= 0;
      })[0];
    }
    if (!preferred) { preferred = plans[0]; }

    el('plan_select').innerHTML = plans.map(function (plan) {
      const slots = plan.remaining === null ? 'capacity unknown'
        : plan.remaining + '/' + plan.max_devices + ' free';
      return '<option value="' + plan.id + '"' +
        (plan.id === preferred.id ? ' selected' : '') + '>' +
        escapeHtml(plan.name) + ' (id ' + plan.id + ', ' + slots + ')</option>';
    }).join('');
    el('plan_id').value = preferred.id;
    setStatus(plans.length + ' plan(s) loaded, using ' + preferred.name + '.', false);
  } catch (error) {
    setStatus('Could not load plans: ' + error, true);
  }
}

async function loadNms(event) {
  if (event) { event.preventDefault(); }
  const params = new URLSearchParams({ config_file: el('config_file').value.trim() });
  try {
    const response = await fetch('/api/kentik-nms?' + params.toString());
    const data = await response.json();
    if (data.error) { setStatus(data.error, true); return; }
    el('agent_id').innerHTML = '<option value="">-- select an agent --</option>' +
      (data.agents || []).map(function (agent) {
        // The id is what gets submitted; the name is appended so the operator
        // can tell the agents apart.
        const label = [agent.id, agent.name].filter(function (part) {
          return part;
        }).join(' - ') + (agent.status ? ' (' + agent.status + ')' : '');
        return '<option value="' + escapeHtml(agent.id) + '">' +
          escapeHtml(label) + '</option>';
      }).join('');
    el('credential_name').innerHTML = '<option value="">-- none --</option>' +
      (data.credentials || []).map(function (credential) {
        return '<option value="' + escapeHtml(credential.name) + '">' +
          escapeHtml(credential.name) + '</option>';
      }).join('');
    setStatus((data.agents || []).length + ' agent(s) and ' +
              (data.credentials || []).length + ' credential(s) loaded.', false);
  } catch (error) {
    setStatus('Could not load agents: ' + error, true);
  }
}

function setStatus(message, isError) {
  const node = el('status');
  node.textContent = message;
  node.className = isError ? 'status error' : 'status';
  node.classList.remove('hidden');
}

function show(id, visible) {
  el(id).classList.toggle('hidden', !visible);
}

function renderTable(target, headers, rows, cellFn) {
  const table = el(target);
  const head = headers.map(function (h) { return '<th>' + h.replace(/_/g, ' ') + '</th>'; }).join('');
  const body = rows.map(function (row) {
    return '<tr>' + headers.map(function (h) {
      return '<td>' + (cellFn ? cellFn(h, row) : escapeHtml(row[h])) + '</td>';
    }).join('') + '</tr>';
  }).join('');
  table.innerHTML = '<thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody>';
}

function escapeHtml(value) {
  if (value === null || value === undefined) { return ''; }
  return String(value).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}

function actionTag(action) {
  const cls = action === 'create' ? 'create' : (action === 'update' ? 'update' : 'nochange');
  return '<span class="tag ' + cls + '">' + escapeHtml(action) + '</span>';
}

function renderStats(stats) {
  const items = [
    ['sites', stats.sites],
    ['create', stats.actions.create],
    ['update', stats.actions.update],
    ['unchanged', stats.actions['no-change']],
    ['source subnets', stats.source_subnets],
    ['summarised', stats.summarised_subnets],
    ['devices', stats.devices],
    ['warnings', stats.warnings]
  ];
  el('stats').innerHTML = items.map(function (item) {
    return '<div class="stat"><span class="value">' + item[1] +
           '</span><span class="name">' + item[0] + '</span></div>';
  }).join('');
  show('stats_card', true);
}

function renderPlan(plan) {
  currentPlan = plan;
  renderStats(plan.stats);

  if (plan.task === 'devices') {
    renderDevicePlan(plan);
    renderTable('warnings_table', ['category', 'message', 'detail'],
                plan.warnings || []);
    show('warnings_card', (plan.warnings || []).length > 0);
    ['sites_card', 'subnets_card', 'devices_card', 'apply_card'].forEach(function (id) {
      show(id, false);
    });

    const changes = plan.stats.device_actions.create +
      plan.stats.device_actions.update;
    const reasons = blockingReasons(plan);
    if (!changes) {
      reasons.push('no devices are selected that need creating or updating');
    }

    renderApplyBlockers(reasons);
    el('run_apply').disabled = reasons.length > 0;
    el('run_apply').title = reasons.join('. ');
    el('apply_hint').textContent = reasons.length
      ? 'Apply is disabled: ' + reasons.join('. ') + '.'
      : changes + ' device(s) will change. You will see them listed before anything is written.';
    setStatus('Device dry run complete. ' + el('device_summary').textContent, false);
    return;
  }

  const warnings = plan.warnings || [];
  renderTable('warnings_table', ['category', 'message', 'detail'], warnings);
  show('warnings_card', warnings.length > 0);

  const siteRows = plan.sites.map(function (site) {
    return {
      site: site.name,
      action: site.action,
      type: site.site_type.replace('SITE_TYPE_', ''),
      infra: site.counts.infrastructure,
      user_access: site.counts.user_access,
      other: site.counts.other,
      source_subnets: site.source_count,
      summarised: site.subnets.length,
      devices: site.devices.length
    };
  });
  renderTable('sites_table',
              ['site', 'action', 'type', 'infra', 'user_access', 'other',
               'source_subnets', 'summarised', 'devices'],
              siteRows,
              function (header, row) {
                return header === 'action' ? actionTag(row.action) : escapeHtml(row[header]);
              });
  show('sites_card', true);

  const subnetRows = [];
  plan.sites.forEach(function (site) {
    site.subnets.forEach(function (subnet) {
      subnetRows.push({
        site: site.name,
        cidr: subnet.cidr,
        classification: subnet.classification,
        source_cidrs: (subnet.source_cidrs || []).join(' '),
        comment: subnet.comment
      });
    });
  });
  renderTable('subnets_table',
              ['site', 'cidr', 'classification', 'source_cidrs', 'comment'],
              subnetRows);
  show('subnets_card', subnetRows.length > 0);

  const devices = plan.devices || [];
  renderTable('devices_table',
              ['name', 'role', 'mgmt_ip', 'vendor', 'model', 'site_name', 'origin'],
              devices);
  show('devices_card', devices.length > 0);

  show('apply_card', false);
  const changes = plan.stats.actions.create + plan.stats.actions.update;
  const reasons = [];
  if (!plan.kentik_available) {
    reasons.push('Kentik credentials are not configured in ' +
                 (plan.ini_file || 'the credentials file') +
                 ((plan.kentik_problems || []).length
                   ? ' (' + plan.kentik_problems.join('; ') + ')' : ''));
  }
  if (!changes) {
    reasons.push('every site is already up to date, so there is nothing to apply');
  }
  renderApplyBlockers(reasons);
  const applyable = reasons.length === 0;
  el('run_apply').disabled = !applyable;
  el('run_apply').title = applyable ? '' : reasons.join('. ');
  el('apply_hint').textContent = applyable
    ? changes + ' site(s) will change. You will see the per-site diff before anything is written.'
    : 'Apply is disabled: ' + reasons.join('. ') + '.';

  let message = 'Dry run complete. ' + plan.stats.sites + ' site(s), ' +
        plan.stats.actions.create + ' to create, ' +
        plan.stats.actions.update + ' to update.';
  if (!plan.kentik_available) {
    message += ' Kentik credentials are not configured, so nothing was compared ' +
      'against live state and apply is disabled.';
  } else if (!applyable) {
    message += ' Nothing to apply.';
  }
  setStatus(message, false);
}

async function loadConfig(path) {
  const params = path ? '?config_file=' + encodeURIComponent(path) : '';
  try {
    const response = await fetch('/api/config' + params);
    const data = await response.json();
    if (data.error) {
      el('env').textContent = data.error;
      setStatus(data.error, true);
      return;
    }
    if (!path) { el('config_file').value = data.ini_file; }
    if (data.lock_config) {
      el('config_file').disabled = true;
      el('config_hint').textContent =
        'The server was started with --lock-config, so the credentials file is fixed.';
    }
    const lines = [
      'Credentials file: ' + escapeHtml(data.ini_file),
      'NIOS: ' + (data.nios.credentials ? escapeHtml(data.nios.gm) : 'not configured'),
      'UDDI: ' + (data.uddi.credentials ? escapeHtml(data.uddi.base_url) : 'not configured'),
      'Kentik: ' + (data.kentik.credentials ? escapeHtml(data.kentik.grpc_base_url) : 'not configured'),
      'Device writes: disabled in this version'
    ];
    el('env').innerHTML = lines.join('<br>');
    setStatus('Reading ' + data.ini_file + '.', false);
    if (data.defaults.source) { el('source').value = data.defaults.source; }
    if (data.defaults.site_key) { el('site_key').value = data.defaults.site_key; }
    if (data.defaults.class_key) { el('class_key').value = data.defaults.class_key; }
  } catch (error) {
    el('env').textContent = 'Could not read the server configuration: ' + error;
  }
}

async function loadInis() {
  try {
    const response = await fetch('/api/inis');
    const data = await response.json();
    el('ini_list').innerHTML = (data.candidates || []).map(function (item) {
      return '<option value="' + escapeHtml(item.path) + '">' +
             escapeHtml(item.name) + ' [' + item.sections.join(', ') + ']</option>';
    }).join('');
  } catch (error) {
    el('config_hint').textContent = 'Could not list candidate ini files: ' + error;
  }
}

async function checkKentik(event) {
  event.preventDefault();
  const params = new URLSearchParams({
    config_file: el('config_file').value.trim()
  });
  el('kentik_status').textContent = 'Checking...';
  try {
    const response = await fetch('/api/kentik-check?' + params.toString());
    const data = await response.json();
    if (data.ok) {
      el('kentik_status').innerHTML = '<strong>Kentik reachable</strong> at ' +
        escapeHtml(data.base_url) + ' — ' + data.sites + ' site(s) visible.';
    } else {
      el('kentik_status').textContent = 'Kentik check failed: ' +
        (data.error || 'unknown error');
    }
  } catch (error) {
    el('kentik_status').textContent = 'Kentik check failed: ' + error;
  }
}

async function loadKeys(event) {
  event.preventDefault();
  const params = new URLSearchParams({
    config_file: el('config_file').value.trim(),
    source: el('source').value,
    network_view: el('network_view').value.trim(),
    ip_space: el('ip_space').value.trim()
  });
  setStatus('Reading the EA/tag keys in use...', false);
  try {
    const response = await fetch('/api/keys?' + params.toString());
    const data = await response.json();
    if (data.error) {
      setStatus(data.error, true);
      return;
    }
    const keys = data.keys || {};
    el('key_list').innerHTML = Object.keys(keys).map(function (key) {
      return '<option value="' + escapeHtml(key) + '">' + escapeHtml(key) +
             ' (' + keys[key] + ' objects)</option>';
    }).join('');
    setStatus(Object.keys(keys).length + ' key(s) found. Pick one in the site key field.', false);
  } catch (error) {
    setStatus('Key lookup failed: ' + error, true);
  }
}

async function runPlan(options) {
  const quiet = Boolean(options && options.quiet);
  const body = formBody();
  el('run_plan').disabled = true;
  planInFlight = true;
  setStatus(quiet ? 'Refreshing the plan for your selection...'
                  : 'Building the plan...', false);
  try {
    const response = await fetch('/api/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok || data.error) {
      setStatus(data.error || 'Plan failed', true);
      return;
    }
    try {
      renderPlan(data);
    } catch (error) {
      setStatus('The plan came back but could not be displayed: ' + error, true);
      return;
    }
  } catch (error) {
    setStatus('Plan failed: ' + error, true);
  } finally {
    el('run_plan').disabled = false;
    planInFlight = false;
    if (refreshQueued) {
      refreshQueued = false;
      schedulePlanRefresh();
    }
  }
}

function downloadFile(filename, content) {
  const blob = new Blob([content], { type: 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function renderExport(data) {
  const rows = (data.files || []).map(function (file) {
    return '<tr><td>' + escapeHtml(file.filename) + '</td><td>' +
      escapeHtml(file.note) + '</td><td>' +
      (file.content.split('\n').length - 1) + '</td>' +
      '<td><button class="secondary" data-format="' + escapeHtml(file.format) +
      '">Download</button></td></tr>';
  }).join('');
  el('export_table').innerHTML =
    '<thead><tr><th>file</th><th>what it is for</th><th>lines</th><th></th></tr>' +
    '</thead><tbody>' + rows + '</tbody>';

  el('export_table').querySelectorAll('button').forEach(function (button) {
    button.addEventListener('click', function () {
      const file = data.files.filter(function (candidate) {
        return candidate.format === button.dataset.format;
      })[0];
      if (file) { downloadFile(file.filename, file.content); }
    });
  });
  show('export_card', true);
}

async function runExport() {
  const body = formBody();
  el('run_export').disabled = true;
  setStatus('Building the export...', false);
  try {
    const response = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok || data.error) {
      setStatus(data.error || 'Export failed', true);
      return;
    }
    if (!data.files.length) {
      show('export_card', false);
      setStatus('Nothing to export - no sites need creating or updating. ' +
                'Tick "Include sites needing no change" to export them anyway.', false);
      return;
    }
    renderExport(data);
    setStatus(data.files.length + ' artefact(s) ready to download.' +
              (data.kentik_available ? '' :
               ' Kentik credentials are not configured, so every site is ' +
               'exported as a create and no site ids are filled in.'), false);
  } catch (error) {
    setStatus('Export failed: ' + error, true);
  } finally {
    el('run_export').disabled = false;
  }
}

function changingEntries(plan) {
  return (plan.sites || []).filter(function (site) {
    return site.action === 'create' || site.action === 'update';
  });
}

function diffHtml(site) {
  const lines = [];
  ['infrastructure', 'user_access', 'other'].forEach(function (bucket) {
    ((site.added || {})[bucket] || []).forEach(function (cidr) {
      lines.push('<li class="add">' + escapeHtml(cidr) + '  <span class="muted">' +
                 bucket.replace('_', ' ') + '</span></li>');
    });
    ((site.extra || {})[bucket] || []).forEach(function (cidr) {
      lines.push('<li class="extra">' + escapeHtml(cidr) +
                 '  <span class="muted">' + bucket.replace('_', ' ') +
                 ' - already in Kentik, not in Infoblox; the API cannot remove it' +
                 '</span></li>');
    });
  });
  if (!lines.length) {
    lines.push('<li class="muted">no prefix changes</li>');
  }
  return '<p class="diff"><span class="site">' + escapeHtml(site.name) +
    '</span> <span class="action">' + escapeHtml(site.action) +
    (site.kentik_id ? ' &middot; id ' + escapeHtml(site.kentik_id) : '') +
    ' &middot; ' + escapeHtml(site.site_type.replace('SITE_TYPE_', '')) +
    '</span><ul>' + lines.join('') + '</ul></p>';
}

function deviceDiffHtml(entry) {
  const lines = [];
  (entry.sending_ips || []).forEach(function (address) {
    lines.push('<li class="add">' + escapeHtml(address) +
               '  <span class="muted">sending ip</span></li>');
  });
  if (!lines.length) {
    lines.push('<li class="muted">no sending IPs selected</li>');
  }
  return '<p class="diff"><span class="site">' + escapeHtml(entry.name) +
    '</span> <span class="action">' + escapeHtml(entry.action) + ' &middot; ' +
    escapeHtml(entry.role) + ' &middot; ' +
    escapeHtml(entry.site_name || 'no site') +
    (entry.site_id ? ' (id ' + escapeHtml(entry.site_id) + ')' : '') +
    '</span><ul>' + lines.join('') + '</ul></p>';
}

function confirmApply(plan) {
  if (plan.task === 'devices') {
    const entries = (plan.device_entries || []).filter(function (entry) {
      return !entry.excluded &&
        (entry.action === 'create' || entry.action === 'update');
    });
    const creates = entries.filter(function (e) { return e.action === 'create'; }).length;
    const capacity = plan.capacity || {};
    el('confirm_summary').textContent =
      creates + ' device(s) will be created and ' + (entries.length - creates) +
      ' re-placed in Kentik, in ' + plan.device_mode + ' mode' +
      (plan.plan_name ? ' on plan ' + plan.plan_name : '') + '. ' +
      (capacity.remaining === undefined || capacity.remaining === null ? '' :
       capacity.remaining + ' licensed slot(s) available. ') +
      'Each device created consumes a slot.';
    el('confirm_body').innerHTML = entries.map(deviceDiffHtml).join('');
    show('confirm_modal', true);
  } else {
    confirmSites(plan);
  }

  return new Promise(function (resolve) {
    function cleanup(answer) {
      show('confirm_modal', false);
      el('confirm_yes').removeEventListener('click', yes);
      el('confirm_no').removeEventListener('click', no);
      resolve(answer);
    }
    function yes() { cleanup(true); }
    function no() { cleanup(false); }
    el('confirm_yes').addEventListener('click', yes);
    el('confirm_no').addEventListener('click', no);
  });
}

function confirmSites(plan) {
  const entries = changingEntries(plan);
  const creates = entries.filter(function (s) { return s.action === 'create'; }).length;
  el('confirm_summary').textContent =
    creates + ' site(s) will be created and ' + (entries.length - creates) +
    ' updated in Kentik. Kentik merges site subnet lists, so this only ever ' +
    'adds prefixes - nothing is removed or deleted.';
  el('confirm_body').innerHTML = entries.map(diffHtml).join('');
  show('confirm_modal', true);
}

function startApplyTable(plan) {
  const isDevices = plan.task === 'devices';
  const items = isDevices
    ? (plan.device_entries || []).filter(function (entry) {
        return !entry.excluded &&
          (entry.action === 'create' || entry.action === 'update');
      })
    : changingEntries(plan);
  const rows = items.map(function (item) {
    return '<tr data-row="' + escapeHtml(item.name) + '"><td>' +
      escapeHtml(item.name) + '</td><td>' + escapeHtml(item.action) +
      '</td><td class="status"><span class="chip pending">pending</span></td>' +
      '<td class="detail"></td></tr>';
  }).join('');
  el('apply_table').innerHTML =
    '<thead><tr><th>' + (isDevices ? 'device' : 'site') +
    '</th><th>action</th><th>result</th><th>detail</th></tr>' +
    '</thead><tbody>' + rows + '</tbody>';
  el('apply_note').textContent = '';
  show('apply_card', true);
}

function applyEvent(event) {
  if (event.type === 'site' || event.type === 'device') {
    const name = event.site || event.device;
    const row = el('apply_table').querySelector('tr[data-row="' +
      (window.CSS && CSS.escape ? CSS.escape(name) : name) + '"]');
    if (!row) { return; }
    row.querySelector('.status').innerHTML =
      '<span class="chip ' + escapeHtml(event.status) + '">' +
      escapeHtml(event.status) + '</span>';
    row.querySelector('.detail').textContent = event.error ||
      (event.kentik_id ? 'id ' + event.kentik_id : '');
  } else if (event.type === 'note') {
    el('apply_note').textContent = event.message;
  } else if (event.type === 'error') {
    setStatus(event.message, true);
  }
}

async function runApply() {
  if (!currentPlan) { return; }
  const approved = await confirmApply(currentPlan);
  if (!approved) {
    setStatus('Apply cancelled. Nothing was written.', false);
    return;
  }

  const body = formBody();
  body.confirm = true;
  body.fingerprint = currentPlan.fingerprint;
  el('run_apply').disabled = true;
  startApplyTable(currentPlan);
  setStatus('Applying...', false);

  try {
    const response = await fetch('/api/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!response.ok) {
      const data = await response.json();
      show('apply_card', false);
      setStatus(data.error || 'Apply failed', true);
      if (response.status === 409) { currentPlan = null; }
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let summary = null;

    while (true) {
      const chunk = await reader.read();
      if (chunk.done) { break; }
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();
      parts.forEach(function (part) {
        const line = part.replace(/^data: ?/, '').trim();
        if (!line) { return; }
        let event;
        try {
          event = JSON.parse(line);
        } catch (error) {
          return;
        }
        if (event.type === 'done') {
          summary = event;
        } else {
          applyEvent(event);
        }
      });
    }

    if (summary) {
      const middle = summary.unchanged !== undefined
        ? summary.unchanged + ' unchanged'
        : summary.existing + ' already existed, ' + summary.excluded + ' excluded';
      const message = summary.created + ' created, ' + summary.updated +
        ' updated, ' + middle + ', ' + summary.failed + ' failed.';
      setStatus(summary.failed ? 'Apply finished with errors: ' + message
                               : 'Apply finished: ' + message,
                summary.failed > 0);
    } else {
      setStatus('Apply ended without a summary - check the results table.', true);
    }
    currentPlan = null;
  } catch (error) {
    setStatus('Apply failed: ' + error, true);
  }
}

el('config_file').addEventListener('change', function () {
  const path = el('config_file').value.trim();
  el('key_list').innerHTML = '';
  el('kentik_status').textContent = '';
  el('run_apply').disabled = true;
  currentPlan = null;
  loadConfig(path);
});
el('task_sites').addEventListener('click', function () { setTask('sites'); });
el('task_devices').addEventListener('click', function () {
  setTask('devices');
  if (!el('plan_select').options.length) { loadPlans(); }
});
el('device_mode').addEventListener('change', function () {
  updateDeviceMode();
  invalidatePlan('Mode changed');
});
el('snmp_mode').addEventListener('change', function () {
  updateSnmpMode();
  invalidatePlan('SNMP collection changed');
});
['agent_id', 'credential_name', 'monitoring_template_id'].forEach(function (field) {
  el(field).addEventListener('change', function () {
    invalidatePlan('Agent settings changed');
  });
});
el('bgp_type').addEventListener('change', function () {
  updateBgpType();
  invalidatePlan('BGP type changed');
});
['bgp_neighbor_asn', 'bgp_neighbor_ip', 'bgp_neighbor_ip6',
 'bgp_device_id', 'bgp_flowspec', 'sample_rate'].forEach(function (field) {
  el(field).addEventListener('change', function () {
    invalidatePlan('BGP settings changed');
  });
});
el('load_plans').addEventListener('click', loadPlans);
el('plan_select').addEventListener('change', function () {
  if (el('plan_select').value) { el('plan_id').value = el('plan_select').value; }
  invalidatePlan('Plan changed');
});
el('plan_id').addEventListener('change', function () {
  invalidatePlan('Plan id changed');
});
el('load_nms').addEventListener('click', loadNms);
el('include_all').addEventListener('click', function (event) {
  event.preventDefault();
  excluded = {};
  invalidatePlan('All devices included');
  el('device_plan_table').querySelectorAll('input[data-include]').forEach(function (box) {
    box.checked = true;
    box.closest('tr').classList.remove('excluded');
  });
});
el('exclude_all').addEventListener('click', function (event) {
  event.preventDefault();
  el('device_plan_table').querySelectorAll('input[data-include]').forEach(function (box) {
    box.checked = false;
    excluded[box.dataset.include] = true;
    box.closest('tr').classList.add('excluded');
  });
  invalidatePlan('All devices excluded');
});
el('load_keys').addEventListener('click', loadKeys);
el('check_kentik').addEventListener('click', checkKentik);
el('run_plan').addEventListener('click', runPlan);
el('run_export').addEventListener('click', runExport);
el('run_apply').addEventListener('click', runApply);
loadConfig();
loadInis();
updateDeviceMode();
updateSnmpMode();
updateBgpType();
setTask('sites');

'use strict';

const VALUE_FIELDS = ['source', 'site_key', 'class_key', 'site_type_key',
                      'network_view', 'ip_space', 'site_filter', 'max_prefix_len'];
const FLAG_FIELDS = ['include_address_blocks', 'replace_networks', 'devices',
                     'use_insight', 'use_uai', 'use_gateways'];

let currentPlan = null;

function el(id) {
  return document.getElementById(id);
}

function formBody() {
  const body = {};
  VALUE_FIELDS.forEach(function (field) { body[field] = el(field).value.trim(); });
  FLAG_FIELDS.forEach(function (field) { body[field] = el(field).checked; });
  return body;
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

  const applyable = plan.kentik_available &&
        (plan.stats.actions.create > 0 || plan.stats.actions.update > 0);
  el('run_apply').disabled = !applyable;

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

async function loadConfig() {
  try {
    const response = await fetch('/api/config');
    const data = await response.json();
    const lines = [
      'Credentials file: ' + escapeHtml(data.ini_file),
      'NIOS: ' + (data.nios.credentials ? escapeHtml(data.nios.gm) : 'not configured'),
      'UDDI: ' + (data.uddi.credentials ? escapeHtml(data.uddi.base_url) : 'not configured'),
      'Kentik: ' + (data.kentik.credentials ? escapeHtml(data.kentik.grpc_base_url) : 'not configured'),
      'Device writes: disabled in this version'
    ];
    el('env').innerHTML = lines.join('<br>');
    if (data.defaults.source) { el('source').value = data.defaults.source; }
    if (data.defaults.site_key) { el('site_key').value = data.defaults.site_key; }
    if (data.defaults.class_key) { el('class_key').value = data.defaults.class_key; }
  } catch (error) {
    el('env').textContent = 'Could not read the server configuration: ' + error;
  }
}

async function loadKeys(event) {
  event.preventDefault();
  const params = new URLSearchParams({
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

async function runPlan() {
  const body = formBody();
  if (!body.site_key) {
    setStatus('A site EA/tag key is required.', true);
    return;
  }
  el('run_plan').disabled = true;
  setStatus('Building the plan...', false);
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
    renderPlan(data);
  } catch (error) {
    setStatus('Plan failed: ' + error, true);
  } finally {
    el('run_plan').disabled = false;
  }
}

async function runApply() {
  if (!currentPlan) { return; }
  const summary = currentPlan.stats.actions.create + ' site(s) will be created and ' +
        currentPlan.stats.actions.update + ' updated in Kentik. Continue?';
  if (!window.confirm(summary)) { return; }

  const body = formBody();
  body.confirm = true;
  el('run_apply').disabled = true;
  el('log').textContent = '';
  show('log_card', true);
  setStatus('Applying...', false);

  try {
    const response = await fetch('/api/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!response.ok) {
      const data = await response.json();
      setStatus(data.error || 'Apply failed', true);
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let exitCode = null;

    while (true) {
      const chunk = await reader.read();
      if (chunk.done) { break; }
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();
      parts.forEach(function (part) {
        const line = part.replace(/^data: ?/, '');
        const match = line.match(/^\[EXIT:(-?\d+)\]$/);
        if (match) {
          exitCode = parseInt(match[1], 10);
        } else {
          el('log').textContent += line + '\n';
          el('log').scrollTop = el('log').scrollHeight;
        }
      });
    }
    if (exitCode === 0) {
      setStatus('Apply finished successfully. Re-run the dry run to confirm the new state.', false);
    } else {
      setStatus('Apply exited with code ' + exitCode + '. Check the log.', true);
    }
  } catch (error) {
    setStatus('Apply failed: ' + error, true);
  } finally {
    el('run_apply').disabled = false;
  }
}

el('load_keys').addEventListener('click', loadKeys);
el('run_plan').addEventListener('click', runPlan);
el('run_apply').addEventListener('click', runApply);
loadConfig();

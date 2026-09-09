'use strict';

const VALUE_FIELDS = ['config_file', 'source', 'site_key', 'class_key',
                      'site_type_key', 'network_view', 'ip_space', 'site_filter',
                      'max_prefix_len', 'export_prefix'];
const FLAG_FIELDS = ['include_address_blocks', 'replace_networks', 'devices',
                     'use_insight', 'use_uai', 'use_gateways',
                     'export_include_unchanged'];

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

  show('apply_card', false);
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
  if (!body.site_key) {
    setStatus('A site EA/tag key is required.', true);
    return;
  }
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
    ((site.removed || {})[bucket] || []).forEach(function (cidr) {
      lines.push('<li class="remove">' + escapeHtml(cidr) + '  <span class="muted">' +
                 bucket.replace('_', ' ') + '</span></li>');
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

function confirmApply(plan) {
  const entries = changingEntries(plan);
  const creates = entries.filter(function (s) { return s.action === 'create'; }).length;
  el('confirm_summary').textContent =
    creates + ' site(s) will be created and ' + (entries.length - creates) +
    ' updated in Kentik. Sites are never deleted.';
  el('confirm_body').innerHTML = entries.map(diffHtml).join('');
  show('confirm_modal', true);

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

function startApplyTable(plan) {
  const rows = changingEntries(plan).map(function (site) {
    return '<tr data-site="' + escapeHtml(site.name) + '"><td>' +
      escapeHtml(site.name) + '</td><td>' + escapeHtml(site.action) +
      '</td><td class="status"><span class="chip pending">pending</span></td>' +
      '<td class="detail"></td></tr>';
  }).join('');
  el('apply_table').innerHTML =
    '<thead><tr><th>site</th><th>action</th><th>result</th><th>detail</th></tr>' +
    '</thead><tbody>' + rows + '</tbody>';
  el('apply_note').textContent = '';
  show('apply_card', true);
}

function applyEvent(event) {
  if (event.type === 'site') {
    const row = el('apply_table').querySelector('tr[data-site="' +
      (window.CSS && CSS.escape ? CSS.escape(event.site) : event.site) + '"]');
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
      const message = summary.created + ' created, ' + summary.updated +
        ' updated, ' + summary.unchanged + ' unchanged, ' + summary.failed +
        ' failed.';
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
  el('run_apply').disabled = true;
  currentPlan = null;
  loadConfig(path);
});
el('load_keys').addEventListener('click', loadKeys);
el('run_plan').addEventListener('click', runPlan);
el('run_export').addEventListener('click', runExport);
el('run_apply').addEventListener('click', runApply);
loadConfig();
loadInis();

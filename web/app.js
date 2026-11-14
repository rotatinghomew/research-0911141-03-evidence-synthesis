const state = {context: null, current: null, expanded: new Set()};
const element = identifier => document.getElementById(identifier);

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function message(value, error = false) {
  element('status').textContent = value;
  element('status').className = error ? 'error' : '';
}

async function guarded(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

function labelFor(key, value) {
  const labels = (state.context.product.labels || {})[key];
  return labels && labels[value] ? labels[value] : value;
}

function selected() {
  const selection = {};
  for (const control of state.context.product.options) {
    selection[control[0]] = element('select-' + control[0]).value;
  }
  const from = element('week-from').value.trim();
  const to = element('week-to').value.trim();
  if (from !== '') selection.week_from = Number(from);
  if (to !== '') selection.week_to = Number(to);
  const rho = element('rho').value.trim();
  if (rho !== '') selection.rho = Number(rho);
  return {revision: element('revision').value, selection};
}

function renderControls() {
  const revision = element('revision').value || state.context.default_revision;
  element('revision').replaceChildren();
  for (const dataset of state.context.datasets) {
    const option = document.createElement('option');
    option.value = dataset.revision;
    option.textContent = dataset.label + ' · ' + dataset.revision.slice(0, 9);
    element('revision').append(option);
  }
  element('revision').value = revision;
  if (!element('revision').value) element('revision').value = state.context.default_revision;
  if (element('controls').children.length) return;
  for (const [key, labelText, choices] of state.context.product.options) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const select = document.createElement('select');
    select.id = 'select-' + key;
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = labelFor(key, choice);
      select.append(option);
    }
    select.value = state.context.product.selection[key];
    label.append(select);
    element('controls').append(label);
  }
}

function displayValue(value) {
  if (value === null || value === undefined) return '未观测';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (Array.isArray(value)) return '[' + value.map(displayValue).join(', ') + ']';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function interval(ci) {
  return Array.isArray(ci) ? '[' + ci.map(value => displayValue(value)).join(', ') + ']' : '—';
}

function cell(text, tag = 'td') {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function table(fields, rows, onRow) {
  const wrapper = document.createElement('div');
  wrapper.className = 'table-wrap';
  const node = document.createElement('table');
  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const field of fields) headRow.append(cell(field[1], 'th'));
  head.append(headRow);
  const body = document.createElement('tbody');
  for (const row of rows) {
    const line = document.createElement('tr');
    for (const [key] of fields) line.append(cell(displayValue(row[key])));
    if (onRow) {
      line.tabIndex = 0;
      line.onclick = () => onRow(row);
      line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    }
    body.append(line);
  }
  node.append(head, body);
  wrapper.append(node);
  return wrapper;
}

function drawChart(series) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const values = series.filter(point => typeof point.y === 'number' && Number.isFinite(point.y));
  context.font = '12px sans-serif';
  if (!values.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...values.map(point => point.y));
  const high = Math.max(1, ...values.map(point => point.y));
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  context.fillStyle = '#0a7168';
  const scatter = values.every(point => typeof point.x === 'number');
  const minimumX = scatter ? Math.min(...values.map(point => point.x)) : 0;
  const maximumX = scatter ? Math.max(...values.map(point => point.x)) : 1;
  values.forEach((point, index) => {
    const position = scatter ? 60 + (point.x - minimumX) / (maximumX - minimumX || 1) * 850 : 55 + index * 875 / values.length;
    if (scatter) {
      context.beginPath(); context.arc(position, scale(point.y), 3.5, 0, Math.PI * 2); context.fill();
    } else {
      context.fillRect(position, Math.min(scale(point.y), scale(0)), Math.max(3, 800 / values.length), Math.max(1, Math.abs(scale(point.y) - scale(0))));
    }
    if (values.length < 12) context.fillText(point.label, position, 275);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  context.fillText(scatter ? '沿用方法均值 → 候选方法均值（原始定量配对）' : '按表中顺序展示描述值；缺测不绘制为零', 50, 20);
}

function armBlock(title, arm) {
  const box = document.createElement('div');
  box.className = 'arm';
  const heading = document.createElement('strong'); heading.textContent = title;
  box.append(heading);
  const lines = [
    ['组别', arm.arm_id], ['队列', arm.cohort_id], ['样本量', arm.n],
    ['用于合并的均值', arm.mean], ['标准差', arm.sd === null ? '未报告' : arm.sd],
  ];
  for (const [key, value] of lines) {
    const line = document.createElement('div');
    line.textContent = key + '：' + displayValue(value);
    box.append(line);
  }
  const weeks = document.createElement('div');
  weeks.textContent = '按时间点均值：' + Object.entries(arm.means_by_week)
    .map(([week, value]) => '第' + week + '周=' + displayValue(value)).join('，');
  box.append(weeks);
  return box;
}

function renderGroup(group) {
  const card = document.createElement('article');
  card.className = 'group';
  const heading = document.createElement('h3');
  heading.textContent = '量表 ' + group.scale_id + ' · ' + (group.poolable ? '可合并' : '不可合并');
  card.append(heading);
  const pooled = group.pooled;
  if (group.poolable && pooled) {
    const overall = document.createElement('p');
    overall.className = 'estimate';
    overall.textContent = (pooled.model === 'random' ? '随机效应' : '固定效应') + '总体效应 '
      + displayValue(pooled.estimate) + '，95% 区间 ' + interval(pooled.ci)
      + '，p = ' + displayValue(pooled.p);
    card.append(overall);
    const details = document.createElement('ul');
    details.className = 'facts';
    const heterogeneity = pooled.heterogeneity;
    const facts = [
      ['纳入研究', pooled.k + ' 项'],
      ['固定效应', displayValue(pooled.fixed.estimate) + ' ' + interval(pooled.fixed.ci)],
      ['随机效应', displayValue(pooled.random.estimate) + ' ' + interval(pooled.random.ci)],
      ['研究间差异 Q', heterogeneity ? displayValue(heterogeneity.q) + '（df ' + heterogeneity.df + '，p ' + displayValue(heterogeneity.p) + '）' : '研究数不足2，无法估计'],
      ['I²', heterogeneity ? displayValue(heterogeneity.i2_percent) + '%' : '不可用'],
      ['tau', heterogeneity ? displayValue(heterogeneity.tau) : '不可用'],
      ['研究间预测区间', pooled.random.prediction_interval ? interval(pooled.random.prediction_interval) : (pooled.random.prediction_note || '不可用')],
    ];
    for (const [key, value] of facts) {
      const item = document.createElement('li');
      item.textContent = key + '：' + value;
      details.append(item);
    }
    card.append(details);
  } else {
    const reason = document.createElement('p');
    reason.className = 'not-poolable';
    reason.textContent = '该量表可合并研究不足2项；只并列呈现各项研究，不给出总体平均数。';
    card.append(reason);
  }
  const applicability = group.applicability;
  const scope = document.createElement('p');
  scope.className = 'scope';
  scope.textContent = '适用范围：' + applicability.study_count + ' 项研究、'
    + applicability.cohort_count + ' 个队列、' + applicability.participants + ' 名学员，'
    + '时间点 ' + (applicability.weeks.length ? applicability.weeks.join('、') : '—')
    + '。' + applicability.direction_note + '。' + applicability.statement;
  card.append(scope);

  if (group.contributions.length) {
    const toggle = document.createElement('button');
    toggle.className = 'toggle';
    toggle.textContent = '展开每项贡献与原始组别（' + group.contributions.length + ' 项）';
    const panel = document.createElement('div');
    panel.hidden = true;
    toggle.onclick = () => {
      panel.hidden = !panel.hidden;
      toggle.textContent = (panel.hidden ? '展开' : '收起') + '每项贡献与原始组别（' + group.contributions.length + ' 项）';
    };
    for (const contribution of group.contributions) {
      const row = document.createElement('details');
      row.className = 'contribution';
      const summary = document.createElement('summary');
      summary.textContent = contribution.study_id + ' · ' + contribution.comparison_id
        + ' · 效应 ' + displayValue(contribution.effect)
        + ' · 权重 ' + displayValue(contribution.weight_percent) + '%';
      row.append(summary);
      const meta = document.createElement('p');
      meta.textContent = '时间点 ' + contribution.weeks.join('、')
        + '；标准误 ' + displayValue(contribution.se)
        + (contribution.assumed_rho === null ? '' : '；假设重复测量相关 rho=' + contribution.assumed_rho)
        + (contribution.reporting_complete ? '；该研究在所选范围完整报告' : '；该研究有未报告位置')
        + (contribution.shared_control_with.length ? '；与 ' + contribution.shared_control_with.join('、') + ' 共用对照' : '');
      row.append(meta, armBlock('干预组（' + contribution.treatment_arm.arm_id + '）', contribution.treatment_arm),
        armBlock('对照组（' + contribution.control_arm.arm_id + '）', contribution.control_arm));
      panel.append(row);
    }
    card.append(toggle, panel);
  }
  if (group.excluded.length) {
    const headingSmall = document.createElement('h4');
    headingSmall.textContent = '未进入合并的研究';
    card.append(headingSmall, table(
      [['study_id', '研究'], ['comparison_id', '比较'], ['status', '状态'], ['reason', '原因']],
      group.excluded.map(item => ({
        ...item,
        reason: item.overlap_with && item.overlap_with.length
          ? item.reason + '（与 ' + item.overlap_with.map(other => other.study_id).join('、') + ' 重叠）'
          : item.reason,
      }))));
  }
  if (group.robustness && group.robustness.available) {
    const robust = document.createElement('p');
    robust.className = 'robust';
    robust.textContent = '敏感性：' + group.robustness.variant_count + ' 个变体，估计范围 '
      + interval(group.robustness.estimate_range) + '。' + group.robustness.note + '。';
    card.append(robust);
  }
  return card;
}

function renderSensitivity(synthesis) {
  const container = element('synthesis');
  const rows = synthesis.sensitivity.rows.filter(row => row.k > 0);
  if (!rows.length) return;
  const section = document.createElement('details');
  section.className = 'sensitivity';
  const summary = document.createElement('summary');
  summary.textContent = '敏感性分析：可疑重复报告、单个研究、报告选择、相关假设（' + rows.length + ' 行）';
  section.append(summary);
  section.append(table(
    [['analysis', '分析'], ['detail', '变动'], ['scale_id', '量表'], ['k', '研究数'],
     ['estimate', '效应'], ['ci', '95% 区间'], ['p', 'p'], ['note', '说明']], rows));
  element('synthesis').append(section);
}

function renderReporting(synthesis) {
  const section = document.createElement('details');
  section.className = 'reporting';
  const summary = document.createElement('summary');
  const totals = synthesis.reporting.totals;
  summary.textContent = '报告完整性：已报告 ' + totals.reported + '，已声明未报告 '
    + totals.not_reported + '，缺记录 ' + totals.missing_record;
  section.append(summary);
  section.append(table(
    [['study_id', '研究'], ['reported', '已报告'], ['not_reported', '已声明未报告'],
     ['missing_record', '缺记录'], ['complete', '完整']],
    synthesis.reporting.by_study.map(study => ({
      study_id: study.study_id,
      reported: study.counts.reported,
      not_reported: study.counts.not_reported,
      missing_record: study.counts.missing_record,
      complete: study.complete ? '是' : '否',
    }))));
  section.append(table(
    [['study_id', '研究'], ['comparison_id', '比较'], ['week', '时间点'], ['scale_id', '量表'],
     ['status', '状态'], ['reason', '含义'], ['raw_difference', '原始差值']],
    synthesis.parallel));
  return section;
}

function renderSynthesis(synthesis) {
  const container = element('synthesis');
  container.replaceChildren();
  element('warnings').replaceChildren();
  for (const warning of synthesis.warnings) {
    const item = document.createElement('p');
    item.className = 'warning';
    item.textContent = warning;
    element('warnings').append(item);
  }
  const scope = document.createElement('p');
  scope.className = 'scope-line';
  scope.textContent = '所选范围：' + synthesis.outcome.id + ' · 时间点 '
    + synthesis.scope.week_from + '–' + synthesis.scope.week_to + ' · '
    + labelFor('time_policy', synthesis.scope.time_policy) + ' · '
    + labelFor('model', synthesis.scope.model) + ' · '
    + labelFor('reporting_policy', synthesis.scope.reporting_policy || '') + ' · '
    + labelFor('overlap_policy', synthesis.scope.overlap_policy || '');
  container.append(scope);
  if (!synthesis.groups.length) {
    const empty = document.createElement('p');
    empty.className = 'not-poolable';
    empty.textContent = '所选范围没有可按量表合并的资料；下方并列呈现原始比较。';
    container.append(empty);
  }
  for (const group of synthesis.groups) container.append(renderGroup(group));
  if (synthesis.skipped && synthesis.skipped.length) {
    const section = document.createElement('details');
    section.className = 'reporting';
    const summary = document.createElement('summary');
    summary.textContent = '未进入任何量表分组的研究（' + synthesis.skipped.length + ' 项）';
    section.append(summary);
    section.append(table(
      [['study_id', '研究'], ['comparison_id', '比较'], ['status', '状态'], ['reason', '原因']],
      synthesis.skipped));
    container.append(section);
  }
  renderSensitivity(synthesis);
  container.append(renderReporting(synthesis));
}

function render(result, heading) {
  state.current = result;
  element('summary').replaceChildren();
  for (const [label, value] of Object.entries(result.view.summary)) {
    const card = document.createElement('article');
    const title = document.createElement('span'); title.textContent = label;
    const amount = document.createElement('strong'); amount.textContent = displayValue(value);
    card.append(title, amount); element('summary').append(card);
  }
  element('notes').replaceChildren();
  for (const note of result.view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('notes').append(paragraph);
  }
  const rows = result.view.rows;
  const fields = rows.length ? Object.keys(rows[0]) : [];
  const header = document.createElement('tr');
  for (const field of fields) { const cellNode = document.createElement('th'); cellNode.textContent = field; header.append(cellNode); }
  element('columns').replaceChildren(header); element('rows').replaceChildren();
  for (const row of rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) { const cellNode = document.createElement('td'); cellNode.textContent = displayValue(row[field]); line.append(cellNode); }
    line.onclick = () => { element('details').textContent = JSON.stringify(row, null, 2); };
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    element('rows').append(line);
  }
  drawChart(result.view.series);
  renderSynthesis(result.view.synthesis);
  element('details').textContent = JSON.stringify({revision: result.revision, selection: result.selection,
    history: result.view.history || null}, null, 2);
  message((heading || '已更新描述') + ' · 数据 ' + result.revision.slice(0, 12));
}

function applySelection(selection) {
  for (const [key, value] of Object.entries(selection)) {
    const control = element('select-' + key);
    if (control) control.value = value;
  }
  element('week-from').value = selection.week_from === undefined ? '' : String(selection.week_from);
  element('week-to').value = selection.week_to === undefined ? '' : String(selection.week_to);
  element('rho').value = selection.rho === undefined ? '' : String(selection.rho);
}

async function refresh() {
  state.context = await request('/api/context');
  renderControls();
  element('archive').replaceChildren();
  for (const notebook of state.context.notebooks) {
    const button = document.createElement('button'); button.textContent = notebook.title + ' #' + notebook.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/notebooks/' + notebook.id);
      element('revision').value = saved.revision;
      applySelection(saved.selection);
      render(saved.result, '已读取保存的复核记录，不随新数据变化');
    });
    element('archive').append(button);
  }
}

element('inspect').onclick = () => guarded(async () => render(await request('/api/explore', selected())));
element('save').onclick = () => guarded(async () => {
  const saved = await request('/api/notebooks', {...selected(), title: element('title').value});
  await refresh(); render(saved.result, '已保存记录 #' + saved.id);
});
element('raw').onclick = () => guarded(async () => {
  const revision = state.current ? state.current.revision : element('revision').value;
  element('details').textContent = JSON.stringify(await request('/api/data?revision=' + encodeURIComponent(revision)), null, 2);
});
element('upload').onchange = () => guarded(async () => {
  const file = element('upload').files[0]; if (!file) return;
  const data = JSON.parse(await file.text());
  const uploaded = await request('/api/datasets', {label: file.name, data});
  await refresh(); element('revision').value = uploaded.revision;
  render(await request('/api/explore', selected()), '已导入独立数据版本');
});
element('export').onclick = () => guarded(async () => {
  if (!state.current) throw new Error('请先查看一个范围');
  const data = await request('/api/data?revision=' + encodeURIComponent(state.current.revision));
  const blob = new Blob([JSON.stringify({description: state.current, data}, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'analysis-' + state.current.revision.slice(0, 12) + '.json'; link.click();
  URL.revokeObjectURL(url);
});
guarded(async () => { await refresh(); render(await request('/api/explore', selected())); });

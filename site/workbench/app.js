/* FinAgent Research · dependency-free, evidence-grounded workbench. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const ALL_MODES = ['demo', 'snapshot', 'live'];
  const MODE_NAMES = {demo: '历史案例回放', snapshot: 'AI 研读 · 历史证据', live: '实时研究'};
  const COMPANY_NAMES = {MSFT: '微软', AAPL: '苹果', NVDA: '英伟达'};
  const METRIC_NAMES = {revenue:'营业收入',operating_income:'营业利润',operating_cash_flow:'经营现金流',capital_expenditure:'PP&E 现金资本支出',free_cash_flow:'自由现金流',net_income:'净利润',operating_margin:'营业利润率',net_margin:'净利润率',rd_ratio:'研发费用率',cash_conversion:'现金转换率',research_and_development:'研发费用'};
  const ICONS = {
    check: '<svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg>',
    doc: '<svg viewBox="0 0 24 24"><path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h6"/></svg>',
    info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/></svg>',
    shield: '<svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6zM8 12l3 3 5-6"/></svg>'
  };
  function readStorage(storage, key, fallback) { try { return storage.getItem(key) || fallback; } catch (_) { return fallback; } }
  function saveStorage(storage, key, value) { try { if (value) storage.setItem(key, value); else storage.removeItem(key); return true; } catch (_) { return false; } }
  function parseJSON(value, fallback) { try { return JSON.parse(value); } catch (_) { return fallback; } }
  const state = {
    mode: 'demo', backend: readStorage(localStorage, 'finagent.backend.v1', ''),
    token: readStorage(sessionStorage, 'finagent.token.v1', ''),
    config: null, reports: [], examples: [], report: null, activeTab: 'overview',
    history: parseJSON(readStorage(localStorage, 'finagent.history.v1', '[]'), []),
    generation: 0, loading: false, job: null, toastTimer: null, sourceTrigger: null,
    activeRunId: null, activeRunBackend: null, linkedRunId: new URLSearchParams(location.search).get('run'),
    workflow: 'single', quality: null, qualityLoading: false
  };
  if (!Array.isArray(state.history)) state.history = [];
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const list = (value) => Array.isArray(value) ? value : [];
  const textValue = (value) => typeof value === 'object' && value !== null ? (value.text || value.label || value.description || JSON.stringify(value)) : String(value == null ? '' : value);
  const dateOnly = (value) => value ? String(value).slice(0, 10) : '未提供';
  const num = (value) => { if (value === null || value === undefined || value === '') return NaN; return Number(String(value).replace(/,/g, '')); };
  const safeURL = (value) => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch (_) { return ''; } };
  const reportKey = (report) => `${report.ticker || ''}|${report.as_of || ''}|${report.question || ''}|${report.mode || 'demo'}`;
  const sourceIds = (item) => list(item.evidence_ids || item.source_ids).map(String);
  const evidenceFor = (id) => list(state.report?.evidence).find(item => String(item.id) === String(id));
  const citations = (item) => sourceIds(item).map(id => `<button class="citation" data-evidence="${esc(id)}" title="查看证据 ${esc(id)}" aria-label="查看证据 ${esc(id)}">${esc(id)}</button>`).join('');
  const citedText = (value) => esc(textValue(value)).replace(/\[([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\]/g, (match,id) => evidenceFor(id) ? `<button class="citation" data-evidence="${id}" title="查看证据 ${id}" aria-label="查看证据 ${id}">${id}</button>` : match);
  const isComparison = report => report?.report_type === 'comparison';
  const isComparisonExample = example => !!example?.compare_with;
  const matchesExample = (report, example) => report && example && report.question === example.question && report.as_of === example.as_of && (isComparison(report) ? list(report.comparison?.tickers)[0] === example.ticker && list(report.comparison?.tickers)[1] === example.compare_with : !example.compare_with && report.ticker === example.ticker);
  const exampleForReport = report => state.examples.find(example => matchesExample(report, example));
  const modeLabel = report => report?.mode === 'demo' && !report.static_replay ? '历史数据计算 · 未调用模型' : MODE_NAMES[report?.mode] || report?.mode || '研究报告';
  const apiURL = (path, base = state.backend) => `${base.replace(/\/$/, '')}${path}`;
  const localToday = () => { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`; };

  async function fetchJSON(path, options = {}) {
    const {base = state.backend, token = state.token, timeout = 20000, absolute = false, ...init} = options;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const headers = {...(init.body ? {'Content-Type': 'application/json'} : {}), ...init.headers};
      if (token && !absolute) headers.Authorization = `Bearer ${token}`;
      const response = await fetch(absolute ? path : apiURL(path, base), {...init, headers, signal: controller.signal, cache: 'no-store'});
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('json')) throw new Error(response.ok ? '此地址未返回 FinAgent API 数据，请检查后端地址。' : `服务返回 HTTP ${response.status}，请检查后端地址。`);
      const data = await response.json();
      if (!response.ok) {
        const detail = data.detail || data.error || {};
        let message = typeof detail === 'string' ? detail : detail.message || data.message;
        if (Array.isArray(detail)) message = detail.map(item => item.msg || '输入格式无效').join('；');
        if (response.status === 401 || response.status === 403) message = message || '服务需要有效的访问令牌，请在连接设置中填写部署者提供的服务令牌。';
        if (response.status === 429) message = message || '服务调用额度已用完，请稍后重试。';
        throw new Error(message || `请求失败（HTTP ${response.status}）`);
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时。请检查服务状态后重试。');
      if (error instanceof TypeError) throw new Error('无法连接后端。请检查地址、服务是否启动，以及后端是否允许此页面的跨域请求。');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function showError(message, title = '研究未完成') { $('error-title').textContent = title; $('error-message').textContent = message; $('error-banner').hidden = false; }
  function hideError() { $('error-banner').hidden = true; $('reload-linked-report').hidden = true; }
  function toast(message) { clearTimeout(state.toastTimer); $('toast').textContent = message; $('toast').hidden = false; state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3400); }
  function fillList(id, items, fallback) { $(id).innerHTML = (list(items).length ? list(items) : [fallback]).map(item => `<li>${esc(textValue(item))}</li>`).join(''); }
  function formatNumeric(value, unit = '') {
    const number = num(value);
    if (!Number.isFinite(number)) return value == null ? '—' : String(value);
    const unitLower = String(unit).toLowerCase();
    if (unitLower === 'percentage_points') return `${number > 0 ? '+' : ''}${number.toFixed(2)} 个百分点`;
    if (unitLower.includes('percent') || unit === '%' || unitLower === 'pct') return `${number.toFixed(2).replace(/\.00$/, '')}%`;
    if (unitLower === 'usd') {
      const abs = Math.abs(number);
      if (abs >= 1e9) return `$${(number / 1e9).toFixed(2).replace(/\.00$/, '')}B`;
      if (abs >= 1e6) return `$${(number / 1e6).toFixed(2).replace(/\.00$/, '')}M`;
      return `$${number.toLocaleString('en-US', {maximumFractionDigits:2})}`;
    }
    return number.toLocaleString('en-US', {maximumFractionDigits:2});
  }
  function displayMetric(metric) { return metric.display_value || formatNumeric(metric.value, metric.unit); }
  function fiscalPeriod(item) {
    if (item.period_start && item.period_end) return `${dateOnly(item.period_start)} — ${dateOnly(item.period_end)}`;
    return item.period || dateOnly(item.period_end);
  }
  function displayChange(value) {
    const number = num(value);
    if (!Number.isFinite(number)) return '';
    return `<span class="metric-change ${number < 0 ? 'negative' : ''}">${number >= 0 ? '↗ +' : '↘ '}${number.toFixed(2)}%</span>`;
  }

  function renderExamples() {
    const filtered = state.examples.map((example,index) => ({example,index})).filter(({example}) => isComparisonExample(example) === (state.workflow === 'compare'));
    $('example-buttons').innerHTML = filtered.map(({example,index}) => `<button type="button" class="example-chip ${matchesExample(state.report,example) ? 'current' : ''}" data-example="${index}" title="${esc(example.question)}">${esc(isComparisonExample(example) ? `${example.ticker} × ${example.compare_with}` : COMPANY_NAMES[example.ticker] || example.ticker)}<span>·</span>${esc(example.short_title || (isComparisonExample(example) ? '财务口径对照' : {MSFT:'AI 投入与现金流',AAPL:'收入与盈利质量',NVDA:'高增长与证据缺口'}[example.ticker]) || example.title || '财报研究')}<span>↗</span></button>`).join('') || '<span class="subtle-text">连接后端可提交新的研究问题</span>';
  }
  function applyExample(example, announce = false) {
    if (!example) return;
    const compareTickers = list(example.comparison?.tickers);
    const compareWith = example.compare_with || compareTickers[1];
    setWorkflow(compareWith ? 'compare' : 'single');
    $('ticker').value = compareTickers[0] || example.ticker;
    if (compareWith) $('compare-with').value = compareWith;
    $('question').value = example.question;
    $('as-of').value = example.as_of || '2024-11-01';
    if (state.mode === 'live') setMode('demo', false);
    hideError();
    if (announce) toast('已填入研究案例，点击「运行研究」查看报告。');
  }
  function setWorkflow(workflow, options = {}) {
    if (!['single','compare','quality'].includes(workflow)) return;
    state.workflow = workflow;
    document.querySelectorAll('[data-workflow]').forEach(button => {
      const active = button.dataset.workflow === workflow;
      button.classList.toggle('active',active); button.setAttribute('aria-selected',String(active)); button.tabIndex = active ? 0 : -1;
      if (active && options.focus) button.focus();
    });
    $('research-workflow').hidden = workflow === 'quality';
    $('quality-workflow').hidden = workflow !== 'quality';
    $('research-workflow').setAttribute('aria-labelledby',workflow === 'compare' ? 'workflow-compare' : 'workflow-single');
    $('compare-field').hidden = workflow !== 'compare';
    $('composer-heading').lastChild.textContent = workflow === 'compare' ? '比较哪些财务问题？' : '你想核验什么？';
    if (workflow === 'quality') { loadQuality(); return; }
    renderExamples();
    if (options.seed) {
      const example = state.examples.find(item => isComparisonExample(item) === (workflow === 'compare') && (workflow === 'compare' || item.ticker === $('ticker').value)) || state.examples.find(item => isComparisonExample(item) === (workflow === 'compare'));
      if (example) {
        applyExample(example);
        const report = state.reports.find(item => matchesExample(item,example));
        if (report && state.mode === 'demo') renderReport(report);
      }
    }
  }
  function setMode(mode, adjustDate = true) {
    state.mode = mode;
    ALL_MODES.forEach(item => { $(`mode-${item}`).classList.toggle('selected', item === mode); $(`mode-${item}`).setAttribute('aria-pressed', String(item === mode)); });
    const labels = {demo: '无需 API Key', snapshot: '服务端调用 DeepSeek', live: '实时获取公开披露'};
    $('mode-hint').innerHTML = mode === 'demo' ? `${ICONS.shield}${labels.demo}` : `${ICONS.shield}${labels[mode]}`;
    if (adjustDate) $('as-of').value = mode === 'live' ? localToday() : (state.examples.find(item => item.ticker === $('ticker').value)?.as_of || '2024-11-01');
    const messages = {
      demo: state.config ? '<strong>历史披露，确定性计算。</strong>预设案例直接回放；新的财务问题提交到后端，在冻结的 SEC 样本内计算，不调用模型。' : '<strong>真实数据，历史回放。</strong>案例使用已保存的 SEC 官方披露；不代表最新行情。接入后端即可运行新的研究。',
      snapshot: '<strong>历史证据，真实 AI 研读。</strong>使用已审计的财报快照，实际调用服务端 DeepSeek。信息范围保持在案例截止日。',
      live: '<strong>实时检索，按日期核验。</strong>后端获取 SEC 公开披露，并在已配置时调用 DeepSeek。财报研究不包含实时股价。'
    };
    $('mode-notice').querySelector('p').innerHTML = messages[mode];
    $('research-button').querySelector('span').textContent = mode === 'demo' ? '运行研究' : mode === 'snapshot' ? '开始 AI 研读' : '开始实时研究';
    hideError();
  }
  function updateConnection(config) {
    state.config = config;
    $('status-dot').classList.toggle('connected', !!config);
    $('connection-label').textContent = config ? '研究引擎已连接' : '历史案例模式';
    $('connection-status').title = config ? `后端 ${state.backend || location.origin} · ${config.version || 'FinAgent'}` : '连接后端以运行新的研究';
    $('mode-demo').textContent = config ? '历史计算' : '历史案例';
    if (config?.tickers?.length) {
      ['ticker','compare-with'].forEach(id => {
        const selected = $(id).value;
        $(id).innerHTML = config.tickers.map(item => `<option value="${esc(item.ticker)}">${esc(item.ticker)} · ${esc(COMPANY_NAMES[item.ticker] || item.name)}</option>`).join('');
        if ([...$(id).options].some(option => option.value === selected)) $(id).value = selected;
      });
    }
    if (state.mode === 'demo') setMode('demo',false);
  }

  function ratioLabel(value) { const number = num(value); return Number.isFinite(number) ? `${(number*100).toFixed(1).replace(/\.0$/,'')}%` : '—'; }
  function countLabel(value) { return Number.isFinite(num(value)) ? String(value) : '—'; }
  async function loadQuality() {
    if (state.quality || state.qualityLoading) return;
    state.qualityLoading = true; $('quality-loading').hidden = false; $('quality-error').hidden = true;
    try {
      const quality = await fetchJSON('data/quality_summary.json',{absolute:true,token:'',timeout:12000});
      if (!quality.current?.overall || !quality.baseline?.overall) throw new Error('质量评价文件未包含预期的版本对照结果。');
      state.quality = quality;
      const overall = quality.current.overall;
      const detailsLink = safeURL(quality.details_url) || $('quality-details-link').href;
      const firstFailures = num(overall.cases)-num(overall.fulfilled);
      const regression = quality.post_eval_regression;
      const regressionNote = regression ? `修复后回归为 ${countLabel(regression.fulfilled)} / ${countLabel(regression.cases)}；${regression.detail || '回归结果应结合其问题集与方法阅读'}。` : '修复后的结果另列，不替换首次评测成绩。';
      $('evaluation-context').innerHTML = `<p class="evaluation-first-pass"><strong>v2 首评成绩 · ${esc(countLabel(overall.fulfilled))} / ${esc(countLabel(overall.cases))}</strong><br>保留 ${esc(countLabel(firstFailures))} 道失败。${esc(regressionNote)}<a href="${esc(detailsLink)}" target="_blank" rel="noopener noreferrer">查看首评与修复记录 ↗</a></p><p><strong>评价范围：</strong>${esc(countLabel(overall.cases))} 个独立问题。${esc(quality.scope || '以评价文件记录为准')}<br><strong>评价方法：</strong>冻结问题集与样本，模型调用 ${esc(countLabel(quality.model_calls))} 次。下列分数只描述这组问题的结果。</p>`;
      const cards = [
        {label:'完整问题满足',value:`${countLabel(overall.fulfilled)} / ${countLabel(overall.cases)}`,note:`满足率 ${ratioLabel(overall.fulfilled_rate)} · 所有请求都正确完成才计入`},
        {label:'请求数值正确',value:`${countLabel(overall.correct_requested_values)} / ${countLabel(overall.requested_values)}`,note:`数值正确率 ${ratioLabel(overall.numeric_accuracy)} · 检查明确请求的数值`},
        {label:'正确保留判断',value:`${countLabel(overall.correct_abstentions)} / ${countLabel(overall.abstain_cases)}`,note:'在应拒答的问题中，正确识别证据边界'},
        {label:'有证据却错误拒答',value:countLabel(overall.false_refusals),note:`在 ${countLabel(overall.answerable_cases)} 个可回答问题中检查`}
      ];
      $('evaluation-metrics').innerHTML = cards.map(card => `<article class="evaluation-metric"><h3>${esc(card.label)}</h3><strong>${esc(card.value)}</strong><p>${esc(card.note)}</p></article>`).join('');
      $('evaluation-table-body').innerHTML = [['overall','整体问题集'],['development','开发问题集'],['heldout','留出问题集']].map(([key,label]) => {
        const baseline = quality.baseline[key] || {}, current = quality.current[key] || {};
        const delta = Number.isFinite(num(current.fulfilled_rate)) && Number.isFinite(num(baseline.fulfilled_rate)) ? (num(current.fulfilled_rate)-num(baseline.fulfilled_rate))*100 : NaN;
        return `<tr><td>${label}<small>${esc(countLabel(current.cases))} 个问题</small></td><td><strong>${esc(ratioLabel(baseline.fulfilled_rate))}</strong><small>${esc(countLabel(baseline.fulfilled))} / ${esc(countLabel(baseline.cases))} 完整满足</small></td><td><strong>${esc(ratioLabel(current.fulfilled_rate))}</strong><small>${esc(countLabel(current.fulfilled))} / ${esc(countLabel(current.cases))} 完整满足</small></td><td><span class="evaluation-delta ${delta<0?'negative':''}">${Number.isFinite(delta) ? `${delta>0?'+':''}${delta.toFixed(1).replace(/\.0$/,'')} 个百分点` : '不适用'}</span></td></tr>`;
      }).join('');
      fillList('evaluation-limitations',quality.limitations,'本评价仅覆盖文件中列明的问题集，不代表所有金融研究任务。');
      const failures = list(quality.first_failures || quality.failures || quality.current.failures);
      if (failures.length) $('evaluation-limitations').innerHTML = `<li class="evaluation-failure-label">首评保留的失败记录</li>${failures.map(item => `<li>${esc(item.question || item.prompt || item.label || item.case_id || textValue(item))}${item.reason || item.detail ? `：${esc(item.reason || item.detail)}` : ''}</li>`).join('')}${$('evaluation-limitations').innerHTML}`;
      $('evaluation-provenance').innerHTML = `<div><dt>评价套件</dt><dd>${esc(quality.suite)}</dd></div><div><dt>评价时间</dt><dd>${esc(String(quality.checked_at || '').replace('T',' '))}</dd></div><div><dt>版本</dt><dd>v1 ${esc(String(quality.baseline.revision).slice(0,7))} → v2 首评<details class="evaluation-record-details"><summary>完整版本与指纹</summary><code>基线：${esc(quality.baseline.revision)}<br>首评：${esc(quality.current.revision)}<br>实现 SHA-256：${esc(quality.current.implementation_sha256 || '未提供')}</code></details></dd></div><div><dt>问题集 SHA-256</dt><dd><code>${esc(quality.dataset_sha256 || '未提供')}</code></dd></div>`;
      if (regression) $('evaluation-provenance').innerHTML += `<div><dt>修复后回归记录</dt><dd>${esc(countLabel(regression.fulfilled))} / ${esc(countLabel(regression.cases))} 个问题完整满足；${esc(countLabel(regression.correct_requested_values))} / ${esc(countLabel(regression.requested_values))} 个请求数值正确。<br>${esc(String(regression.checked_at || '').replace('T',' '))}${safeURL(regression.receipt_url) ? `<br><a class="text-button" href="${esc(safeURL(regression.receipt_url))}" target="_blank" rel="noopener noreferrer">查看回归结果记录 ↗</a>` : ''}</dd></div>`;
      if (safeURL(quality.details_url)) $('quality-details-link').href = safeURL(quality.details_url);
      $('quality-content').hidden = false;
    } catch (error) {
      $('quality-error').textContent = `尚未读取到可核验的质量评价：${error.message}。这里不会用引用覆盖率或单元测试通过数代替回答质量。`;
      $('quality-error').hidden = false;
    } finally { state.qualityLoading = false; $('quality-loading').hidden = true; }
  }

  function renderAnswers(report) {
    const answers = list(report.answers);
    $('answers-card').hidden = !answers.length;
    $('synthesis-details').open = !answers.length;
    const answered = answers.filter(answer => answer.answerability === 'answered').length;
    $('answer-coverage').textContent = `${answered} / ${answers.length} 项已回答`;
    const operationNames = {value:'财年值',growth_pct:'同比变化',growth_amount:'同比增量',change_pp:'变化 · 百分点',unsupported:'超出证据范围'};
    const latestYear = Math.max(...answers.map(answer => num(answer.fiscal_year)).filter(Number.isFinite));
    const priority = answer => answer.answerability !== 'answered' ? 0 : answer.operation === 'value' && num(answer.fiscal_year) === latestYear ? 1 : answer.operation !== 'value' ? 2 : 3;
    const orderedAnswers = [...answers].sort((left,right) => priority(left)-priority(right));
    const rows = orderedAnswers.map((answer,index) => {
      const complete = answer.answerability === 'answered';
      const period = answer.fiscal_year ? `FY${answer.fiscal_year}${answer.comparison_fiscal_year ? ` / FY${answer.comparison_fiscal_year}` : ''}` : answer.period_end ? dateOnly(answer.period_end) : '';
      const label = answer.label || METRIC_NAMES[answer.metric_id] || answer.metric_id || `子问题 ${index+1}`;
      return `<article class="answer-item ${complete ? 'answered' : 'unanswered'}" data-answerability="${esc(answer.answerability)}"><div class="answer-item-head"><div><h4 class="answer-title">${esc(label)}</h4><p class="answer-period">${esc(period)}${period ? ' · ' : ''}${esc(operationNames[answer.operation] || answer.operation || '财务核验')}</p></div><div class="answer-result">${complete ? `<strong class="answer-value">${esc(answer.display_value || formatNumeric(answer.value,answer.unit))}</strong>` : `<span class="answer-status">${answer.answerability === 'missing_evidence' ? '缺少证据' : '超出当前范围'}</span>`}</div></div>${!complete ? `<p class="answer-text">${esc(answer.reason || answer.text || '现有证据不足以回答此子问题。')}</p>` : ''}<div class="answer-evidence-line">${answer.period_start || answer.period_end ? `<span>${esc(fiscalPeriod(answer))}</span>` : ''}${citations(answer)}</div>${answer.formula ? `<details class="answer-formula"><summary>计算口径与公式</summary><p>${esc(answer.formula)}${answer.comparison_period_end ? `<br>对照期间：${esc(answer.comparison_period_start || '')} — ${esc(answer.comparison_period_end)}` : ''}</p></details>` : ''}</article>`;
    });
    $('direct-answers').innerHTML = rows.slice(0,6).join('') + (rows.length > 6 ? `<details class="more-answers"><summary>展开其余 ${rows.length-6} 项回答</summary><div>${rows.slice(6).join('')}</div></details>` : '');
  }

  function renderComparison(report) {
    const comparison = report.comparison || {};
    const enabled = isComparison(report);
    $('comparison-card').hidden = !enabled;
    $('chart-card').hidden = enabled;
    $('metrics-grid').closest('.metrics-section').hidden = enabled || !list(report.metrics).length;
    if (!enabled) return;
    const tickers = list(comparison.tickers);
    $('comparison-pair').textContent = tickers.join(' × ');
    $('comparison-policy').textContent = comparison.period_policy || '分别保留两家公司的财年期间；期间不一致时，表中数据仅作背景对照。';
    $('comparison-table-head').innerHTML = `<tr><th>财务指标</th><th>${esc(tickers[0] || '公司一')}<small>原始期间与证据</small></th><th>${esc(tickers[1] || '公司二')}<small>原始期间与证据</small></th><th>可比性 / 差异</th></tr>`;
    const cell = item => item ? `<strong class="comparison-cell-value">${esc(item.display_value || formatNumeric(item.value, item.unit))}</strong><span class="comparison-cell-period">${esc(fiscalPeriod(item))}</span>${citations(item)}` : '<span class="comparison-cell-status">证据缺失</span>';
    $('comparison-table-body').innerHTML = list(comparison.rows).map(row => `<tr><td>${esc(row.label || METRIC_NAMES[row.metric_id] || row.metric_id)}<small class="comparison-cell-period">${esc(row.unit || '')}</small></td><td>${cell(row.left)}</td><td>${cell(row.right)}</td><td><span class="comparison-cell-status ${row.comparable ? 'comparable' : ''}">${row.comparable ? '同口径可比' : '限制直接比较'}</span>${row.difference && row.comparable ? `<span class="comparison-cell-value">${esc(row.difference.display_value || formatNumeric(row.difference.value,row.difference.unit))}</span><p class="comparison-row-note">${esc(row.difference.formula || '')}${citations(row.difference)}</p>` : ''}<p class="comparison-row-note">${esc(row.note || '')}</p></td></tr>`).join('');
  }

  function renderFindings(report) {
    const strengths = list(report.strengths);
    $('strengths-card').hidden = !strengths.length;
    $('strengths-list').innerHTML = strengths.map(item => `<div class="finding-row"><span>✓</span><div>${citedText(textValue(item))}${typeof item === 'object' ? citations(item) : ''}</div></div>`).join('');
    const risks = list(report.risk_findings);
    if (risks.length) $('risks-list').innerHTML = risks.map(item => `<li>${citedText(textValue(item))}${typeof item === 'object' ? citations(item) : ''}</li>`).join('');
  }

  function renderMetrics(report) {
    const metrics = list(report.metrics);
    const priorities = ['revenue','operating_margin','operating_cash_flow','free_cash_flow','operating_income'];
    const topMetrics = priorities.map(id => metrics.find(metric => metric.id === id)).filter(Boolean).slice(0, 4);
    if (topMetrics.length < 4) topMetrics.push(...metrics.filter(metric => !topMetrics.includes(metric)).slice(0,4-topMetrics.length));
    $('metrics-grid').innerHTML = topMetrics.map(metric => {
      const display = displayMetric(metric);
      const ids = list(metric.value_evidence_ids).length ? list(metric.value_evidence_ids) : sourceIds(metric);
      const period = metric.period_end ? `截至 ${dateOnly(metric.period_end)}` : metric.period || '报告期间';
      return `<article class="metric-card"><div class="metric-top"><span class="metric-label" title="${esc(metric.label)}">${esc(metric.label || metric.name || metric.id)}</span><span class="metric-info" title="${esc(metric.formula || fiscalPeriod(metric))}">${ICONS.info}</span></div><div class="metric-value ${String(display).length > 12 ? 'long' : ''}">${esc(display)}</div><div class="metric-bottom">${displayChange(metric.change_pct)}<span>${esc(period)}</span>${ids[0] ? `<button class="metric-source" data-evidence="${esc(ids[0])}" title="查看该指标的证据">${esc(ids[0])} ↗</button>` : '<span>无来源</span>'}</div></article>`;
    }).join('');
    $('metrics-grid').hidden = !metrics.length;
  }

  function renderChart(report) {
    let metrics = list(report.metrics).filter(item => String(item.unit).toUpperCase() === 'USD' && Number.isFinite(num(item.value))).slice(0, 4);
    if (!metrics.length) {
      $('financial-chart').innerHTML = '<div class="chart-empty">报告没有可直接比较的同单位金额指标。请在证据页查看原始数据。</div>';
      $('chart-note').textContent = '不拼接不同单位、不同定义的指标。';
      return;
    }
    const hasPrevious = metrics.some(item => Number.isFinite(num(item.comparison_value)));
    const values = metrics.flatMap(item => [num(item.value), ...(Number.isFinite(num(item.comparison_value)) ? [num(item.comparison_value)] : [])]);
    const upper = Math.max(0, ...values) * 1.18 || 1;
    const lower = Math.min(0, ...values) * 1.15;
    const width = 620, height = 240, left = 47, right = 15, top = 24, bottom = 192;
    const chartWidth = width - left - right, chartHeight = bottom - top;
    const y = value => bottom - (value - lower) / (upper - lower) * chartHeight;
    const baseY = y(0), step = chartWidth / metrics.length, barWidth = Math.min(hasPrevious ? 39 : 57, step / 3.3);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(metrics.map(item => `${item.label} ${displayMetric(item)}${Number.isFinite(num(item.comparison_value)) ? `，前期 ${formatNumeric(item.comparison_value, item.unit)}` : ''}`).join('；'))}">`;
    for (let i = 0; i <= 3; i++) {
      const value = lower + (upper - lower) * i / 3, gridY = y(value);
      svg += `<line x1="${left}" x2="${width-right}" y1="${gridY}" y2="${gridY}" stroke="#e9eee8" stroke-dasharray="3 4"/><text x="${left-10}" y="${gridY+3}" text-anchor="end" font-size="9" fill="#8b9b8f">${esc((value / 1e9).toFixed(0))}</text>`;
    }
    svg += `<text x="${left}" y="12" font-size="8" fill="#8b9b8f">十亿美元 · USD B</text><line x1="${left}" x2="${width-right}" y1="${baseY}" y2="${baseY}" stroke="#dce5dc"/>`;
    metrics.forEach((metric, index) => {
      const center = left + step * (index + .5), current = num(metric.value), previous = num(metric.comparison_value);
      const currentX = hasPrevious ? center + 4 : center - barWidth / 2;
      if (hasPrevious && Number.isFinite(previous)) {
        const previousY = y(previous);
        svg += `<rect x="${center-barWidth-4}" y="${Math.min(previousY,baseY)}" width="${barWidth}" height="${Math.max(Math.abs(baseY-previousY),1)}" rx="3" fill="#d0ddd2"/><text x="${center-barWidth/2-4}" y="${previous >= 0 ? previousY-7 : previousY+12}" text-anchor="middle" font-size="9" fill="#94a696">${esc((previous/1e9).toFixed(1))}</text>`;
      }
      svg += `<rect x="${currentX}" y="${Math.min(y(current),baseY)}" width="${barWidth}" height="${Math.max(Math.abs(baseY-y(current)),1)}" rx="3" fill="#548f75"/><text x="${currentX+barWidth/2}" y="${current >= 0 ? y(current)-7 : y(current)+12}" text-anchor="middle" font-size="10" font-weight="500" fill="#47785f">${esc((current/1e9).toFixed(1))}</text><text x="${center}" y="216" text-anchor="middle" font-size="10" fill="#7a8f7c">${esc(metric.label || metric.id)}</text><text x="${center}" y="232" text-anchor="middle" font-size="8" fill="#9aaa96">${esc(metric.period_end ? `截至 ${dateOnly(metric.period_end)}` : '')}</text>`;
    });
    svg += '</svg>';
    $('financial-chart').innerHTML = `${hasPrevious ? '<div class="chart-legend"><span>前期披露</span><span>本期披露</span></div>' : ''}${svg}`;
    $('chart-description').textContent = hasPrevious ? '原始金额与前期披露对照 · 未提供前期的指标不补值' : '同一单位下的财务规模 · 财报期间见各指标';
    $('chart-note').textContent = metrics.map(item => `${item.label}：${item.formula || '来自原始披露'}${sourceIds(item).length ? ` [${sourceIds(item).join(', ')}]` : ''}`).join('；');
  }

  function renderReport(report, options = {}) {
    state.report = report;
    setWorkflow(isComparison(report) ? 'compare' : 'single');
    state.activeRunId = options.runId || null;
    state.activeRunBackend = options.backend ?? state.backend;
    const evidence = list(report.evidence), claims = list(report.claims), trace = list(report.trace), coverage = report.coverage || {}, verdict = report.verdict || {};
    $('initial-loading').hidden = true; $('report-area').hidden = false;
    $('company-avatar').className = `company-avatar ${isComparison(report) ? 'comparison' : report.ticker === 'MSFT' ? 'msft' : ''}`;
    $('company-avatar').innerHTML = isComparison(report) ? '⇄' : report.ticker === 'MSFT' ? '<span></span><span></span><span></span><span></span>' : esc(report.ticker === 'AAPL' ? 'A' : report.ticker === 'NVDA' ? 'N' : String(report.ticker || 'F').slice(0, 1));
    $('report-title').textContent = report.title ? report.title.replace(report.company || report.ticker, COMPANY_NAMES[report.ticker] || report.company || report.ticker) : `${COMPANY_NAMES[report.ticker] || report.company || report.ticker} · 财务研究简报`;
    $('report-ticker').textContent = report.ticker || '';
    $('report-meta').textContent = `${modeLabel(report)} · 信息截止 ${dateOnly(report.as_of)} · ${report.company || report.ticker}`;
    $('report-question').textContent = `研究问题：${report.question || '未提供'}`;
    $('evidence-count').textContent = evidence.length; $('trace-count').textContent = trace.length;
    $('report-audit').innerHTML = `${ICONS.shield} ${report.static_replay ? '历史案例 · 证据可追溯' : '按报告时间边界核验'}`;
    renderMetrics(report);
    $('verdict-badge').textContent = verdict.label || '研究性判断';
    $('verdict-badge').className = `verdict-badge ${['constructive','mixed','cautious','insufficient_evidence'].includes(verdict.stance) ? verdict.stance : 'mixed'}`;
    $('summary-text').innerHTML = citedText(report.summary || '此报告未提供摘要。');
    $('verdict-rationale').textContent = textValue(verdict.rationale || '请结合已验证事实与证据缺口阅读。');
    $('claims-list').innerHTML = claims.length ? `<details class="verified-facts"><summary>查看 ${claims.length} 条已核验事实 <span>原始披露与确定性计算</span></summary><div class="verified-facts-body">${claims.map(claim => `<div class="claim-row"><span class="claim-bullet">${claim.kind === 'derived' ? '≈' : '✓'}</span><div class="claim-text">${esc(claim.text)}${citations(claim)}</div></div>`).join('')}</div></details>` : '';
    $('confidence-label').innerHTML = `${ICONS.shield} ${verdict.stance === 'insufficient_evidence' ? '证据不足 · 保留判断' : '有条件的基本面判断'}`;
    const model = report.model || {};
    $('model-label').textContent = model.used ? `${model.provider || 'AI'} · ${model.model || '模型已调用'}` : (report.mode === 'demo' ? `${report.static_replay ? '确定性历史回放' : '历史样本计算'} · 未调用模型` : `模型未使用 · ${model.status || '未配置'}`);
    fillList('conditions-list', verdict.conditions, '当前证据尚不足以给出成立条件。');
    fillList('risks-list', report.risks, '报告未列出独立风险，请先核查证据边界。');
    fillList('limitations-list', report.limitations, '财报仅覆盖已披露期间，不能替代当前估值与市场数据。');
    fillList('next-steps-list', report.next_steps, '补充最新披露、估值与情景假设。');
    renderAnswers(report); renderComparison(report); renderFindings(report);
    const cited = claims.filter(claim => sourceIds(claim).length > 0 && sourceIds(claim).every(id => evidence.some(item => String(item.id) === id))).length;
    const ratio = claims.length ? Math.round(cited / claims.length * 100) : null;
    $('coverage-score').textContent = ratio === null ? '—' : `${ratio}%`;
    $('coverage-caption').textContent = `${cited} / ${claims.length} 条事实带有效引用`;
    $('coverage-ring-value').style.strokeDashoffset = String(113.1 * (1 - (ratio || 0)/100));
    $('quality-sources').textContent = `${evidence.length} 条证据 / ${new Set(evidence.map(item => item.url)).size} 个链接`;
    $('quality-cutoff').textContent = dateOnly(coverage.filing_cutoff || report.as_of);
    const missing = list(coverage.missing_metrics);
    $('quality-missing').textContent = missing.length ? `${missing.length} 项 · ${missing.map(item => METRIC_NAMES[item] || textValue(item)).join('、')}` : '未记录缺失';
    $('source-preview-list').innerHTML = evidence.slice(0, 3).map(item => `<button class="source-preview" data-evidence="${esc(item.id)}"><span class="source-document-icon">${ICONS.doc}</span><span class="source-preview-info"><span class="source-preview-title">${esc(item.title)}</span><span class="source-preview-meta"><span>${esc(item.id)}</span><span>·</span><span>${esc(dateOnly(item.published_at))}</span></span></span><span class="arrow">↗</span></button>`).join('') || '<p class="empty-state">尚无原始证据</p>';
    if (!isComparison(report)) renderChart(report);
    renderEvidence(); renderTrace(report); renderExamples();
    $('copy-report-link').disabled = !state.activeRunId && !(exampleForReport(report) && report.mode === 'demo');
    if (options.save) saveHistory(report);
    if (options.scroll) $('report-area').scrollIntoView({behavior:'smooth', block:'start'});
    document.title = `${report.ticker} 研究简报 · FinAgent Research`;
  }

  function renderEvidence() {
    const query = $('evidence-search').value.trim().toLowerCase();
    const all = list(state.report?.evidence);
    const items = all.filter(item => [item.id,item.title,item.excerpt,item.metric,item.source_type].join(' ').toLowerCase().includes(query));
    $('evidence-filter-count').textContent = `${items.length} / ${all.length} 条证据`;
    $('evidence-empty').hidden = items.length > 0;
    $('evidence-table-body').innerHTML = items.map(item => `<tr><td><button class="evidence-title-button" data-evidence="${esc(item.id)}"><span class="evidence-id">${esc(item.id)}</span><span>${esc(item.title)}${item.metric ? `<small>${esc(item.metric)}${item.value !== undefined ? ` · ${esc(formatNumeric(item.value,item.unit))}` : ''}</small>` : ''}</span></button></td><td>${esc(fiscalPeriod(item))}</td><td>${esc(dateOnly(item.published_at))}</td><td><span class="source-type">${esc(item.source_type || '公开披露')}</span></td><td><button class="table-open" data-evidence="${esc(item.id)}" aria-label="查看证据 ${esc(item.id)}">展开 ↗</button></td></tr>`).join('');
  }

  function renderTrace(report) {
    const steps = list(report.trace);
    const model = report.model || {};
    $('trace-summary').innerHTML = `<div><small>运行方式</small><strong>${esc(modeLabel(report))}</strong></div><div><small>记录步骤</small><strong>${steps.length} 个可检查节点</strong></div><div><small>模型状态</small><strong>${esc(model.used ? `${model.provider || 'AI'} · 已调用` : `未调用 · ${model.status || '离线回放'}`)}</strong></div>${report.static_replay || report.timestamp_basis === 'data_capture' ? '<p class="trace-replay-note">静态回放 · 非运行时计时。离线样例步骤的时间为数据采集时刻，并非线上执行时间或耗时。</p>' : report.mode === 'demo' ? '<p class="trace-replay-note">历史数据模式通过确定性工作流生成报告，没有调用模型。</p>' : ''}`;
    const statusLabels = {completed:'已完成',complete:'已完成',success:'已完成',passed:'已通过',failed:'失败',running:'执行中',skipped:'已跳过',warning:'有边界',pending:'等待中'};
    $('trace-timeline').innerHTML = steps.map((step,index) => `<li class="trace-step ${step.status === 'failed' ? 'failed' : ''}"><span class="trace-number">${['completed','complete','success','passed'].includes(step.status) ? ICONS.check : String(index+1).padStart(2,'0')}</span><div class="trace-step-head"><h4>${esc(step.label || step.step)}</h4><span>${esc(statusLabels[step.status] || step.status || '已记录')}</span>${step.timestamp ? `<time datetime="${esc(step.timestamp)}">${esc(String(step.timestamp).replace('T',' ').slice(0,19))}</time>` : ''}</div><p>${esc(textValue(step.detail || '此步骤没有附加说明。'))}</p></li>`).join('');
  }

  function showTab(name, focus = false) {
    state.activeTab = name;
    document.querySelectorAll('[data-tab]').forEach(button => {
      const active = button.dataset.tab === name;
      button.classList.toggle('active',active); button.setAttribute('aria-selected',String(active)); button.tabIndex = active ? 0 : -1;
      $(`panel-${button.dataset.tab}`).hidden = !active;
      if (active && focus) button.focus();
    });
  }
  function openSource(id, trigger) {
    const item = evidenceFor(id);
    if (!item) { toast('此引用未在报告证据集中找到。'); return; }
    state.sourceTrigger = trigger;
    const allMetrics = [...list(state.report.metrics), ...list(state.report.companies).flatMap(company => list(company.metrics))];
    const url = safeURL(item.url), metrics = allMetrics.filter(metric => sourceIds(metric).includes(String(item.id)));
    $('source-dialog-content').innerHTML = `<h2>${esc(item.title)}</h2><span class="source-drawer-id">${esc(item.id)} · ${esc(item.source_type || '公开披露')}</span><dl class="source-facts"><div><dt>报告期间</dt><dd>${esc(fiscalPeriod(item))}</dd></div><div><dt>披露 / 申报日期</dt><dd>${esc(dateOnly(item.published_at))}</dd></div><div><dt>证据抓取时间</dt><dd>${esc(item.retrieved_at ? String(item.retrieved_at).replace('T',' ').slice(0,19) : '未提供')}</dd></div><div><dt>研究信息截止日</dt><dd>${esc(dateOnly(state.report.as_of))}</dd></div>${item.value !== undefined ? `<div><dt>记录值</dt><dd>${esc(formatNumeric(item.value,item.unit))}</dd></div>` : ''}${item.metric ? `<div><dt>指标标签</dt><dd>${esc(item.metric)}</dd></div>` : ''}</dl><h3>原始记录摘录</h3><div class="source-excerpt">${esc(item.excerpt || '此来源未附带摘录，请打开原文核查。')}</div>${metrics.length ? `<h3>用于以下指标</h3><div class="source-related">${metrics.map(metric => `<span>${esc(metric.label)} · ${esc(displayMetric(metric))}</span>`).join('')}</div>${metrics.filter(metric => metric.formula).map(metric => `<p class="field-help">${esc(metric.label)}：${esc(metric.formula)}</p>`).join('')}` : ''}${item.sha256 ? `<h3>记录指纹 · SHA-256</h3><div class="source-hash">${esc(item.sha256)}</div><p class="field-help">指纹用于核对保存的证据记录，并不等同于第三方真实性认证。</p>` : ''}${url ? `<a class="primary-button source-original" href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开官方来源 <span>↗</span></a><p class="source-host">${esc(new URL(url).hostname)}</p>` : '<p class="field-help">此证据未提供有效的来源链接。</p>'}`;
    $('source-dialog').showModal();
  }

  function saveHistory(report) {
    const key = reportKey(report);
    state.history = [{key, saved_at:new Date().toISOString(), report, run_id:state.activeRunId, backend:state.activeRunBackend}, ...state.history.filter(item => item.key !== key)].slice(0, 10);
    if (!saveStorage(localStorage, 'finagent.history.v1', JSON.stringify(state.history))) toast('浏览器存储不可用；本次报告仍可导出。');
    renderHistory();
  }
  function renderHistory() {
    $('history-list').innerHTML = state.history.length ? state.history.map((item,index) => `<button class="history-item" data-history="${index}" title="${esc(item.report.question)}"><span class="history-item-title">${esc(item.report.ticker)} · ${esc(item.report.title || item.report.question || '财务研究')}</span><small>${esc(dateOnly(item.report.as_of))} · ${esc(MODE_NAMES[item.report.mode] || item.report.mode || '研究')}</small></button>`).join('') : '<p class="history-empty">完成的研究会保存在此设备。</p>';
  }
  function renderProgress(events, status) {
    const steps = list(events), latest = steps[steps.length-1];
    $('progress-label').textContent = latest?.label || (status === 'queued' ? '研究任务已排队' : '研究引擎正在执行');
    $('progress-detail').textContent = latest ? textValue(latest.detail).slice(0,70) : '正在等待首条执行记录';
    const completeCount = steps.filter(step => ['completed','complete','success','passed'].includes(step.status)).length;
    $('progress-fill').style.width = `${Math.min(92, 10 + completeCount * 10)}%`;
    $('progress-steps').innerHTML = steps.slice(-6).map(step => `<span class="${['completed','complete','success','passed'].includes(step.status) ? 'completed' : ''}">${['completed','complete','success','passed'].includes(step.status) ? '✓' : '·'} ${esc(step.label || step.step)}</span>`).join('');
  }
  function setRunning(running) {
    state.loading = running;
    $('run-progress').hidden = !running;
    $('research-button').disabled = running;
    ALL_MODES.forEach(mode => { $(`mode-${mode}`).disabled = running; });
  }
  async function runResearch(event) {
    event.preventDefault();
    if (state.loading) return;
    hideError();
    const request = {question:$('question').value.trim(),ticker:$('ticker').value,as_of:$('as-of').value,mode:state.mode};
    if (state.workflow === 'compare') request.compare_with = $('compare-with').value;
    if (!request.question) { showError('请先填写研究问题。','还缺一个研究问题'); $('question').focus(); return; }
    if (!request.as_of || request.as_of > localToday()) { showError('信息截止日需要是今天或过去的日期。','请检查信息截止日'); $('as-of').focus(); return; }
    if (request.compare_with === request.ticker) { showError('请选择两家不同的公司。','请检查对比公司'); $('compare-with').focus(); return; }
    if (state.mode === 'demo') {
      const report = state.reports.find(item => matchesExample(item,request));
      if (report) { renderReport(report,{save:true,scroll:true}); showTab('overview'); toast('已载入真实 SEC 历史案例；没有调用模型。'); return; }
      if (!state.config) {
        showError('当前静态页面只回放已经核验的固定问题。请点击上方预设案例，或连接后端，在「历史计算」模式下提交新的财务问题。','这个问题需要新的研究');
        return;
      }
    }
    if (!state.config) { showError('请先连接已部署的研究后端。历史案例仍可免配置体验。','尚未连接研究引擎'); openSettings(); return; }
    const modeConfig = state.config.modes?.[state.mode];
    if (modeConfig && modeConfig.available === false) { showError(modeConfig.detail || '该模式尚未在后端启用，请检查部署配置。','研究模式尚未启用'); return; }
    if (modeConfig?.requires_token && !state.token) { showError('部署者为此模式设置了访问保护，请在连接设置中填写服务访问令牌。','需要服务访问令牌'); openSettings(); return; }
    const generation = ++state.generation, runBackend = state.backend, runToken = state.token;
    setRunning(true); renderProgress([], 'queued');
    try {
      const created = await fetchJSON('/api/research',{method:'POST',body:JSON.stringify(request),base:runBackend,token:runToken});
      if (generation !== state.generation) return;
      if (!created.id) throw new Error('后端未返回研究任务编号。');
      state.job = created.id;
      const start = Date.now();
      while (generation === state.generation) {
        const job = await fetchJSON(`/api/research/${encodeURIComponent(created.id)}`,{base:runBackend,token:runToken});
        if (generation !== state.generation) return;
        if (job.status === 'completed') {
          if (!job.result) throw new Error('任务已完成，但后端未提供报告内容。');
          renderReport(job.result,{save:true,scroll:true,runId:created.id,backend:runBackend}); showTab('overview'); toast('研究已完成，证据与执行记录已就绪。'); return;
        }
        if (job.status === 'failed') throw new Error(job.error?.message || '后端研究任务失败，请查看服务日志。');
        try {
          const eventData = await fetchJSON(`/api/research/${encodeURIComponent(created.id)}/events`,{base:runBackend,token:runToken});
          if (generation !== state.generation) return;
          renderProgress(eventData.events,job.status);
        } catch (_) { $('progress-detail').textContent = '任务仍在运行；执行记录暂时不可用。'; }
        if (Date.now()-start > 300000) throw new Error(`等待超过 5 分钟，任务 ${created.id} 可能仍在后端运行。请检查服务日志；无需立即重复创建任务。`);
        await new Promise(resolve => setTimeout(resolve,1500));
      }
    } catch (error) { if (generation === state.generation) showError(error.message); }
    finally { if (generation === state.generation) { setRunning(false); state.job = null; } }
  }

  async function restoreLinkedRun() {
    const runId = state.linkedRunId;
    if (!runId) return;
    hideError(); $('initial-loading').hidden = false; $('report-area').hidden = true;
    $('initial-loading').querySelector('p').textContent = '正在读取链接中的已有研究，不会创建新任务';
    $('reload-linked-report').disabled = true;
    try {
      if (!/^[A-Za-z0-9_-]{1,160}$/.test(runId)) throw new Error('报告链接中的任务编号格式无效。');
      if (!state.config) throw new Error('此链接指向一个已有后端任务，但当前尚未连接研究引擎。请先连接保存该任务的后端，再点击「重新读取报告」。');
      const job = await fetchJSON(`/api/research/${encodeURIComponent(runId)}`);
      if (job.status === 'failed') {
        showError(`任务 ${runId} 执行失败：${job.error?.message || '后端未提供详细原因。'} 此操作没有重新提交研究。`, '链接中的研究未成功完成');
      } else if (job.status === 'completed' && job.result) {
        applyExample(job.result);
        setMode(ALL_MODES.includes(job.result.mode) ? job.result.mode : 'demo', false);
        renderReport(job.result,{runId,backend:state.backend}); showTab('overview');
      } else if (['queued','running'].includes(job.status)) {
        showError(`已找到任务 ${runId}，当前状态：${job.status === 'queued' ? '排队中' : '运行中'}。稍后点击「重新读取报告」检查结果；不会重复创建任务。`, '这份研究尚未完成');
      } else {
        throw new Error(`任务 ${runId} 未返回可用报告（状态：${job.status || '未知'}）。`);
      }
    } catch (error) { showError(error.message, '无法读取链接中的报告'); }
    finally {
      $('initial-loading').hidden = true; $('reload-linked-report').disabled = false;
      $('reload-linked-report').hidden = $('error-banner').hidden;
    }
  }

  async function copyReportLink() {
    if (!state.report) return;
    let url;
    if (state.activeRunId) {
      url = new URL(state.activeRunBackend ? `${state.activeRunBackend.replace(/\/$/,'')}/` : location.href);
      url.search = ''; url.hash = ''; url.searchParams.set('run',state.activeRunId);
    } else {
      const example = exampleForReport(state.report);
      if (!example) { toast('此报告暂无共享编号，可导出完整报告。'); return; }
      url = new URL(location.href); url.search = ''; url.hash = ''; url.searchParams.set('example',example.id);
    }
    url.username = ''; url.password = '';
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(url.href);
      else {
        const field = document.createElement('textarea'); field.value = url.href; field.style.cssText = 'position:fixed;left:-9999px;top:0'; document.body.appendChild(field); field.select();
        const copied = document.execCommand('copy'); field.remove();
        if (!copied) throw new Error('clipboard unavailable');
      }
      toast(state.activeRunId ? '报告链接已复制，不包含访问令牌。受保护的服务仍需授权。' : '历史案例链接已复制。');
    } catch (_) {
      showError(`浏览器未允许复制，可手动复制此链接：${url.href}`, '报告链接已生成');
    }
  }

  function openSettings() { $('backend-url').value = state.backend; $('service-token').value = state.token; $('connection-test-result').hidden = true; $('settings-dialog').showModal(); }
  function validateBackend(value) {
    if (!value.trim()) return '';
    const parsed = new URL(value.trim());
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('请输入 HTTP(S) 后端根地址，不要在地址中包含密码、查询参数或片段。');
    return parsed.href.replace(/\/$/,'');
  }
  async function saveConnection(event) {
    event.preventDefault();
    const feedback = $('connection-test-result'); feedback.hidden = false; feedback.className = 'connection-test-result'; feedback.textContent = '正在检查后端配置…'; $('save-connection').disabled = true;
    try {
      const backend = validateBackend($('backend-url').value), token = $('service-token').value.trim();
      const config = await fetchJSON('/api/config',{base:backend,token,timeout:12000});
      if (!config.modes || !config.version) throw new Error('该地址没有返回预期的 FinAgent 配置。请确认使用的是研究后端根地址。');
      state.backend = backend; state.token = token;
      const backendSaved = saveStorage(localStorage,'finagent.backend.v1',backend), tokenSaved = saveStorage(sessionStorage,'finagent.token.v1',token);
      updateConnection(config);
      feedback.textContent = `已连接 FinAgent ${config.version}。${config.features?.deepseek ? 'DeepSeek 已配置。' : 'DeepSeek 尚未配置。'}${backendSaved && tokenSaved ? '' : '浏览器限制了存储，本次页面仍可使用。'}`;
      toast('研究引擎已连接。');
    } catch (error) { feedback.classList.add('failure'); feedback.textContent = error.message; }
    finally { $('save-connection').disabled = false; }
  }

  function markdownReport(report) {
    const verdict = report.verdict || {};
    const lines = [`# ${report.title || report.ticker + ' 财务研究简报'}`, '', `- 公司：${report.company || report.ticker} (${report.ticker})`, `- 研究问题：${report.question}`, `- 信息截止日：${dateOnly(report.as_of)}`, `- 运行方式：${modeLabel(report)}`, `- 生成时间：${report.generated_at || '未提供'}`, '', `**${verdict.label || '有条件的研究判断'}**`, '', textValue(verdict.rationale)];
    const mdCell = value => String(value ?? '').replace(/\|/g,'\\|').replace(/\n/g,' ');
    if (list(report.answers).length) {
      lines.push('', '## 对研究问题的回答', '', '| 子问题 | 财年 | 结果 | 状态 | 计算口径 / 边界 | 证据 |', '| --- | --- | --- | --- | --- | --- |');
      report.answers.forEach(answer => lines.push(`| ${mdCell(answer.label || answer.metric_id)} | ${mdCell(answer.fiscal_year || answer.period_end)} | ${mdCell(answer.answerability === 'answered' ? answer.display_value || formatNumeric(answer.value,answer.unit) : '未回答')} | ${mdCell(answer.answerability)} | ${mdCell(answer.formula || answer.reason)} | ${sourceIds(answer).map(id => `[${id}]`).join(' ')} |`));
    }
    if (isComparison(report)) {
      lines.push('', '## 两家公司对照', '', report.comparison.period_policy || '', '', '| 指标 | 公司一 / 期间 | 公司二 / 期间 | 可比性与备注 |', '| --- | --- | --- | --- |');
      list(report.comparison.rows).forEach(row => lines.push(`| ${mdCell(row.label)} | ${mdCell(row.left ? `${row.left.ticker}: ${row.left.display_value} (${fiscalPeriod(row.left)}) [${sourceIds(row.left).join(', ')}]` : '证据缺失')} | ${mdCell(row.right ? `${row.right.ticker}: ${row.right.display_value} (${fiscalPeriod(row.right)}) [${sourceIds(row.right).join(', ')}]` : '证据缺失')} | ${mdCell(`${row.comparable ? '可比' : '限制直接比较'}；${row.note || ''}`)} |`));
    }
    lines.push('', '## 研究摘要', '', textValue(report.summary));
    if (list(report.metrics).length) lines.push('', '## 关键指标', '', '| 指标 | 数值 | 期间 | 计算方式 | 证据 |', '| --- | --- | --- | --- | --- |');
    list(report.metrics).forEach(metric => lines.push(`| ${mdCell(metric.label)} | ${mdCell(displayMetric(metric))} | ${mdCell(fiscalPeriod(metric))} | ${mdCell(metric.formula || '原始披露')} | ${sourceIds(metric).map(id => `[${id}]`).join(' ')} |`));
    lines.push('', '## 可查证事实', '');
    list(report.claims).forEach(claim => lines.push(`- ${claim.text} ${sourceIds(claim).map(id => `[${id}]`).join(' ')}`));
    [['判断成立的条件',verdict.conditions],['风险',report.risks],['研究边界',report.limitations],['下一步核验',report.next_steps]].forEach(([heading,items]) => { lines.push('',`## ${heading}`,''); list(items).forEach(item => lines.push(`- ${textValue(item)}`)); });
    lines.push('', '## 证据与来源', '');
    list(report.evidence).forEach(item => { lines.push(`### [${item.id}] ${item.title}`, '', `- 来源：${safeURL(item.url) || '未提供有效链接'}`, `- 披露日期：${dateOnly(item.published_at)}`, `- 报告期间：${fiscalPeriod(item)}`, `- 抓取时间：${item.retrieved_at || '未提供'}`, '', `> ${String(item.excerpt || '').replace(/\n/g,'\n> ')}`, ''); if (item.sha256) lines.push(`记录 SHA-256：\`${item.sha256}\``, ''); });
    lines.push('## 运行轨迹', '');
    list(report.trace).forEach(step => lines.push(`- **${step.label || step.step}** (${step.status})：${textValue(step.detail)}`));
    lines.push('', '---', '由 FinAgent Research 导出。研究辅助工具，非个性化投资建议；信息范围以报告截止日为准。');
    return lines.join('\n');
  }
  function exportReport(format) {
    if (!state.report) return;
    const report = state.report, content = format === 'json' ? JSON.stringify(report,null,2) : markdownReport(report);
    const blob = new Blob([format === 'md' ? '\uFEFF' : '',content],{type:format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8'});
    const url = URL.createObjectURL(blob), anchor = document.createElement('a');
    anchor.href = url; anchor.download = `FinAgent-${String(report.ticker).replace(/[^A-Za-z0-9_-]+/g,'-')}-${dateOnly(report.as_of)}.${format}`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(format === 'json' ? '已导出完整 JSON，包括证据与运行记录。' : '已导出 Markdown 研究报告。');
  }

  function bindEvents() {
    document.querySelectorAll('[data-workflow]').forEach(button => {
      button.addEventListener('click',() => setWorkflow(button.dataset.workflow,{seed:true}));
      button.addEventListener('keydown',event => {
        const names = ['single','compare','quality']; let index = names.indexOf(button.dataset.workflow);
        if (event.key === 'ArrowRight') index = (index+1)%3; else if (event.key === 'ArrowLeft') index = (index+2)%3; else if (event.key === 'Home') index = 0; else if (event.key === 'End') index = 2; else return;
        event.preventDefault(); setWorkflow(names[index],{seed:true,focus:true});
      });
    });
    ['nav-quality','open-quality'].forEach(id => $(id).addEventListener('click',() => setWorkflow('quality')));
    document.querySelector('.main-nav .active').addEventListener('click',() => setWorkflow('single',{seed:true}));
    ALL_MODES.forEach(mode => $(`mode-${mode}`).addEventListener('click',() => setMode(mode)));
    $('research-form').addEventListener('submit',runResearch);
    $('ticker').addEventListener('change',() => { if (state.mode === 'demo' && state.workflow === 'single') applyExample(state.examples.find(item => !isComparisonExample(item) && item.ticker === $('ticker').value)); });
    $('example-buttons').addEventListener('click',event => { const button = event.target.closest('[data-example]'); if (button) applyExample(state.examples[Number(button.dataset.example)],true); });
    document.addEventListener('click',event => { const button = event.target.closest('[data-evidence]'); if (button) openSource(button.dataset.evidence,button); });
    document.querySelectorAll('[data-tab]').forEach(button => {
      button.addEventListener('click',() => showTab(button.dataset.tab));
      button.addEventListener('keydown',event => {
        const names = ['overview','evidence','trace']; let index = names.indexOf(button.dataset.tab);
        if (event.key === 'ArrowRight') index = (index+1)%3; else if (event.key === 'ArrowLeft') index = (index+2)%3; else if (event.key === 'Home') index = 0; else if (event.key === 'End') index = 2; else return;
        event.preventDefault(); showTab(names[index],true);
      });
    });
    ['nav-evidence','all-evidence'].forEach(id => $(id).addEventListener('click',() => { setWorkflow(isComparison(state.report)?'compare':'single'); showTab('evidence'); $('report-area').scrollIntoView({behavior:'smooth'}); }));
    $('nav-trace').addEventListener('click',() => { setWorkflow(isComparison(state.report)?'compare':'single'); showTab('trace'); $('report-area').scrollIntoView({behavior:'smooth'}); });
    $('evidence-search').addEventListener('input',renderEvidence);
    ['settings-button','sidebar-settings','connection-status','connect-inline'].forEach(id => $(id).addEventListener('click',openSettings));
    $('connection-form').addEventListener('submit',saveConnection);
    $('reset-connection').addEventListener('click',() => { $('backend-url').value = ''; $('service-token').value = ''; state.backend = ''; state.token = ''; saveStorage(localStorage,'finagent.backend.v1',''); saveStorage(sessionStorage,'finagent.token.v1',''); updateConnection(null); $('connection-test-result').hidden = true; toast('已恢复默认地址。点击「连接并保存」检测当前站点后端。'); });
    $('dismiss-error').addEventListener('click',hideError);
    $('cancel-run').addEventListener('click',() => { state.generation++; setRunning(false); toast('已停止前端等待。已提交的后端任务可能仍在执行。'); });
    $('export-md').addEventListener('click',() => exportReport('md')); $('export-json').addEventListener('click',() => exportReport('json'));
    $('copy-report-link').addEventListener('click',copyReportLink);
    $('reload-linked-report').addEventListener('click',restoreLinkedRun);
    $('history-list').addEventListener('click',event => { const button = event.target.closest('[data-history]'); if (!button) return; const item = state.history[Number(button.dataset.history)]; if (item?.report) { hideError(); renderReport(item.report,{scroll:true,runId:item.run_id,backend:item.backend}); showTab('overview'); toast('已打开保存在此设备的研究报告。'); } });
    $('clear-history').addEventListener('click',() => { state.history = []; saveStorage(localStorage,'finagent.history.v1',''); renderHistory(); toast('本地研究历史已清空。'); });
    ['settings-dialog','source-dialog'].forEach(id => $(id).addEventListener('click',event => { if (event.target === $(id)) { const rect = $(id).getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $(id).close(); } }));
    $('source-dialog').addEventListener('close',() => { if (state.sourceTrigger?.isConnected) state.sourceTrigger.focus(); });
  }

  async function init() {
    bindEvents(); renderHistory(); $('as-of').max = localToday(); $('as-of').value = '2024-11-01';
    const results = await Promise.allSettled([
      fetchJSON('data/demo_reports.json',{absolute:true,token:'',timeout:15000}),
      fetchJSON('/api/config',{timeout:6000})
    ]);
    const [demoResult,configResult] = results;
    if (configResult.status === 'fulfilled') updateConnection(configResult.value); else updateConnection(null);
    if (demoResult.status === 'fulfilled') {
      const data = demoResult.value;
      state.reports = Array.isArray(data) ? data : Array.isArray(data.reports) ? data.reports : Object.values(data.reports || {}).filter(item => item?.ticker);
      state.reports = state.reports.filter(item => item && item.ticker && Array.isArray(item.evidence));
      state.examples = list(data.examples);
      if (!state.examples.length) state.examples = state.reports.map(report => ({id:report.ticker,ticker:report.ticker,as_of:report.as_of,question:report.question,title:{MSFT:'增长与现金流',AAPL:'盈利质量',NVDA:'增长与风险'}[report.ticker] || '财报研究'}));
    }
    if (state.linkedRunId) {
      renderExamples();
      await restoreLinkedRun();
      return;
    }
    const requestedView = new URLSearchParams(location.search).get('view');
    if (requestedView === 'quality' && !state.reports.length) { $('initial-loading').hidden = true; setWorkflow('quality'); return; }
    if (!state.reports.length) {
      $('initial-loading').hidden = true;
      $('example-buttons').innerHTML = '<span class="subtle-text">历史案例暂时不可用</span>';
      showError('未能读取历史案例文件。请通过 HTTP 服务打开此页面，并检查 data/demo_reports.json 是否已部署。已连接后端时仍可使用 AI 研读或实时研究。','历史案例未加载');
      return;
    }
    renderExamples();
    const requestedExampleId = new URLSearchParams(location.search).get('example');
    const requestedExample = requestedExampleId ? state.examples.find(example => example.id === requestedExampleId) : null;
    if (requestedExampleId && !requestedExample) {
      $('initial-loading').hidden = true;
      showError('链接中的历史案例不存在。请选择上方已有案例再运行。','无法读取历史案例链接');
      return;
    }
    const initial = requestedExample ? state.reports.find(report => matchesExample(report,requestedExample)) : state.reports.find(report => report.ticker === 'MSFT') || state.reports[0];
    if (!initial) { $('initial-loading').hidden = true; showError('此案例的报告文件缺失，请检查部署的数据文件。','历史案例暂不可用'); return; }
    applyExample(exampleForReport(initial) || initial);
    renderReport(initial);
    if (requestedView === 'quality') setWorkflow('quality');
    else if (requestedView === 'compare') setWorkflow('compare',{seed:true});
  }
  init().catch(error => { $('initial-loading').hidden = true; showError(error.message,'工作台初始化失败'); });
})();

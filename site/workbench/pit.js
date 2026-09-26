/* This page reads a saved evaluation bundle. It never creates research/model jobs. */
(() => {
  'use strict';
  const DATA_URL = 'data/pit_pilot.json';
  const CONDITIONS = {
    closed_book: { label: '不给证据', description: '模型只收到问题和历史截止日，不附财报材料。观察它能否遵守“只能依据当时可用的指定申报”这一约束。' },
    unfiltered: { label: '全部证据（含未来）', description: '提供指定的完整证据集合，即使其中的申报晚于截止日。观察模型会不会接受未来证据。' },
    pit_filtered: { label: '仅当时可用证据', description: '提供申报日不晚于截止日的材料。在披露前截面，目标申报会被过滤；披露后再提供相同材料。' }
  };
  const COMPANIES = { MSFT: '微软', AAPL: '苹果', NVDA: '英伟达' };
  const METRICS = { revenue: '年度营收', operating_income: '营业利润', operating_margin: '营业利润率' };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  let bundle;
  let selectedCase;

  function safeLink(value, text) {
    try {
      const url = new URL(value);
      if (!['https:', 'http:'].includes(url.protocol)) return esc(text);
      return `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(text)} ↗</a>`;
    } catch { return esc(text); }
  }

  function formatValue(value, unit) {
    if (value === null || value === undefined || value === '') return '—';
    const raw = String(value);
    if (unit === 'USD' && /^-?\d+$/.test(raw)) return '$' + raw.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    if (unit === '%') return raw + '%';
    return raw + (unit ? ' ' + unit : '');
  }

  function dateTime(value) {
    return value ? String(value).replace('T', ' ').replace(/\+00:00$/, ' UTC').replace(/Z$/, ' UTC') : '记录未提供';
  }

  function fact(label, value, className = '') {
    return `<div${className ? ` class="${className}"` : ''}><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  }

  function renderOverview() {
    const summary = bundle.summary || {};
    const issuers = new Set(bundle.cases.map(c => c.ticker));
    const events = new Set(bundle.cases.map(c => `${c.ticker}:${c.accession}`));
    const completed = bundle.runs.filter(r => r.status === 'completed').length;
    const planned = number(summary.planned_runs) || bundle.runs.length;
    $('pit-stats').innerHTML = [
      ['冻结问题', bundle.cases.length, `${issuers.size} 家公司 · 同题前后配对`],
      ['SEC 申报事件', events.size, '同一事件内的问题彼此相关'],
      ['实验条件', Object.keys(CONDITIONS).length, '每题 2 个历史时间截面'],
      ['已完成运行', `${completed} / ${planned}`, '重复次数与样本量分别记录']
    ].map(([label, value, note]) => `<div class="pit-stat"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');
    const attempted = number(summary.attempted_runs) || bundle.runs.filter(r => r.status !== 'not_run').length;
    $('pit-status-banner').classList.toggle('pending', attempted === 0);
    $('pit-status-banner').innerHTML = attempted === 0
      ? '<strong>实验已编排，模型尚未运行。</strong> 下方显示冻结问题、证据与预期答案。成绩保持未运行；浏览页面不会触发 API 调用。'
      : `<strong>已保存 ${attempted} 次尝试，${completed} 次完成。</strong> 这是项目自建的开发试点，答案已知，不属于盲测。页面呈现真实记录；${number(summary.error_runs)} 次错误单列，不补造结果。`;
    const pitPre = bundle.runs.filter(r => r.condition === 'pit_filtered' && r.phase === 'pre' && r.score);
    const unsupportedPre = pitPre.filter(r => r.score.unsupported_historical_answer).length;
    $('pit-round-observation').hidden = !unsupportedPre;
    if (unsupportedPre) $('pit-round-observation').innerHTML = `<strong>本轮观察：</strong>“仅当时可用证据”在披露前 ${unsupportedPre} / ${pitPre.length} 次仍给出无历史证据支持的答案。删除未来材料，并不能保证模型遵守历史证据边界。<a href="https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/PIT_PILOT_RESULTS.md" target="_blank" rel="noopener noreferrer">查看分析 ↗</a>`;
    $('pit-score-note').textContent = `计划 ${planned} 次运行；已尝试 ${attempted} 次；已评分 ${number(summary.scored_runs)} 次；记录用量 ${number(summary.total_tokens).toLocaleString('zh-CN')} tokens。主分母包含全部计划运行，错误不会从主分母中消失。${planned} 个配对条件观察不等于 ${planned} 个独立样本。`;
    $('score-body').innerHTML = Object.entries(CONDITIONS).map(([condition, meta]) => {
      const row = (summary.by_condition || []).find(r => r.condition === condition) || {};
      const runs = bundle.runs.filter(r => r.condition === condition);
      const errors = runs.filter(r => ['error','invalid'].includes(r.status)).length;
      const notScored = runs.filter(r => !r.score && !['error','invalid'].includes(r.status)).length;
      const unknown = runs.filter(r => !['not_run','completed','error','invalid'].includes(r.status)).length;
      const conditionAttempted = runs.some(r => r.status !== 'not_run');
      const ratio = (num, den, scored) => !conditionAttempted ? `<span class="pit-unrun-cell">未运行 · 计划 ${number(den)}</span>` : `<strong>${number(num)}</strong> / ${number(den)}<small class="pit-score-sub">已评分 ${number(scored)}</small>`;
      const conditionPlanned = number(row.planned_runs) || runs.length;
      const prePlanned = number(row.pre_planned) || runs.filter(r => r.phase === 'pre').length;
      const postPlanned = number(row.post_planned) || runs.filter(r => r.phase === 'post').length;
      const futureScored = row.future_exposed_scored === undefined ? runs.filter(r => r.score && r.presented_future_evidence_ids?.length).length : number(row.future_exposed_scored);
      const futurePlanned = row.future_exposed_planned === undefined ? runs.filter(r => r.presented_future_evidence_ids?.length).length : number(row.future_exposed_planned);
      const futureCell = futurePlanned === 0 ? '<span class="pit-unrun-cell">不适用<small class="pit-score-sub">未暴露未来证据</small></span>' : !conditionAttempted ? '<span class="pit-unrun-cell">未运行</span>' : futureScored === 0 ? '<span class="pit-unrun-cell">尚无可评分结果</span>' : `<strong>${number(row.accepted_future_evidence_runs)}</strong> / ${futureScored}<small class="pit-score-sub">收到未来材料且已评分</small>`;
      return `<tr data-condition="${condition}"><td>${esc(meta.label)}<small>${condition}</small></td><td>${ratio(row.decision_correct,conditionPlanned,row.scored_runs)}</td><td>${ratio(row.pre_correct_abstention,prePlanned,row.pre_scored)}</td><td>${ratio(row.post_numeric_correct,postPlanned,row.post_scored)}</td><td>${futureCell}</td><td>${notScored} / ${errors}${unknown ? `<small>（未知状态 ${unknown}）</small>` : ''}</td></tr>`;
    }).join('');
    // Preserve the exact published limitations in a secondary disclosure.
    $('protocol-limitations').innerHTML = (bundle.protocol.limitations || []).map(item => `<li>${esc(item)}</li>`).join('');
    const provenance = bundle.provenance || {};
    const model = bundle.model || bundle.provider || {};
    $('pit-provenance').innerHTML = [
      fact('实验协议', bundle.protocol.name || '未提供'),
      fact('记录生成时间', dateTime(bundle.generated_at)),
      fact('SEC 数据抓取时间', dateTime(provenance.captured_at)),
      fact('运行模式', bundle.mode === 'live' ? '真实模型调用记录' : '离线实验编排'),
      fact('时间精度', bundle.protocol.time_granularity === 'date' ? '日期；不包含申报当日盘中判断' : bundle.protocol.time_granularity || '未提供'),
      fact('模型记录', typeof model === 'string' ? model : model.name || model.model || bundle.model_name || '以各次原始请求 / 响应为准'),
      fact('数据角色', '开发试点；冻结参考答案可见，不属于盲测'),
      fact('重复次数', bundle.protocol.repeats === undefined ? '以协议为准' : String(bundle.protocol.repeats)),
      fact('冻结数据 SHA-256', bundle.dataset_sha256 || '记录未提供', 'pit-wide')
    ].join('');
    const traceName = typeof bundle.trace_file === 'string' ? bundle.trace_file.split(/[\\/]/).pop() : '';
    const traceLink = $('trace-records');
    traceLink.hidden = !/^[a-zA-Z0-9][a-zA-Z0-9._-]*\.jsonl$/.test(traceName);
    if (!traceLink.hidden) traceLink.href = 'https://github.com/QiQiyzhu/FinSearchComp-Audit/blob/main/docs/verification/' + encodeURIComponent(traceName);
  }

  function filteredCases() {
    const ticker = $('issuer-filter').value;
    return bundle.cases.filter(c => ticker === 'all' || c.ticker === ticker);
  }

  function setCases(wantedId) {
    const cases = filteredCases();
    $('case-filter').innerHTML = cases.map((c, index) => `<option value="${esc(c.id)}">${index + 1}. ${esc(c.ticker)} · ${esc(METRICS[c.metric] || c.metric)}</option>`).join('');
    selectedCase = cases.find(c => c.id === wantedId) || cases[0];
    if (!selectedCase) throw new Error('筛选后的问题列表为空。');
    $('case-filter').value = selectedCase.id;
    renderCase();
  }

  function renderObservation(phase, condition) {
    const c = selectedCase;
    const run = bundle.runs.find(r => r.case_id === c.id && r.phase === phase && r.condition === condition);
    const cutoff = run?.as_of || c[phase + '_as_of'];
    const gold = c.gold?.[phase] || {};
    const available = Boolean(c.filing_date && cutoff && c.filing_date <= cutoff);
    const answer = run?.answer;
    const isUnrun = !run || run.status === 'not_run';
    const isError = run && ['error','invalid'].includes(run.status);
    const isUnknown = run && !['not_run','completed','error','invalid'].includes(run.status);
    const verdict = isUnrun ? '未运行' : isError ? (run.status === 'invalid' ? '响应无效' : '运行错误') : isUnknown ? '未知状态' : run.score ? (run.score.decision_correct ? '任务通过' : '任务未通过') : '尚未评分';
    const verdictClass = isUnrun || isUnknown || !run?.score ? 'pending' : run.score.decision_correct ? '' : 'failed';
    let answerValue = isUnrun ? '等待真实运行' : isError ? '没有可评分答案' : isUnknown ? '无法判定运行结果' : answer?.action === 'abstain' ? '保留答案' : answer?.action === 'answer' ? formatValue(answer.value, answer.unit) : '未提供答案';
    const explanation = isUnrun ? '这里不会用参考答案模拟模型表现。' : isError ? run.error || '查看原始记录了解失败原因。' : answer?.explanation || '';
    const presented = run?.presented_evidence_ids || [];
    const future = run?.presented_future_evidence_ids || [];
    const citations = answer?.evidence_ids || [];
    const expected = gold.action === 'abstain' ? '应当保留答案' : formatValue(gold.value, gold.unit);
    const expectedNote = gold.action === 'abstain' ? '指定的目标申报尚不可用' : '冻结参考答案 · ' + (gold.unit || '单位未提供');
    const detail = run ? `<details class="pit-run-detail"><summary>这一次的结构化评分记录</summary><pre>${esc(JSON.stringify(run, null, 2))}</pre></details>` : '';
    return `<article class="pit-observation ${phase}" data-phase="${phase}"><div class="pit-observation-head"><div><h4>${phase === 'pre' ? '披露前 · 截止日' : '披露后 · 截止日'}</h4><time datetime="${esc(cutoff)}">${esc(cutoff)}</time></div><span class="pit-pill ${available ? '' : 'unavailable'}">目标申报${available ? '可用' : '不可用'}</span></div><div class="pit-observation-body"><div class="pit-target"><div><span class="pit-label">评测标准</span><strong>${esc(expected)}</strong><small>${esc(expectedNote)}</small></div></div><div class="pit-answer"><div class="pit-answer-top"><span class="pit-label">模型回答</span><span class="pit-verdict ${verdictClass}">${esc(verdict)}</span></div><p class="pit-answer-value${isUnrun ? ' unrun' : ''}">${esc(answerValue)}</p><p class="pit-answer-explanation">${esc(typeof explanation === 'object' ? JSON.stringify(explanation) : explanation)}</p></div><dl class="pit-run-facts">${fact(isUnrun ? '计划提供的证据' : '实际提供的证据',presented.length ? presented.join('、') : '无')}${fact('其中晚于截止日',future.length ? `${future.length} 条：${future.join('、')}` : '0 条')}${fact('模型引用',isUnrun ? '未运行' : citations.length ? citations.join('、') : '无')}${fact('运行耗时',isUnrun ? '未运行' : run?.latency_ms === undefined || run?.latency_ms === null ? '记录未提供' : `${run.latency_ms} ms`)}</dl>${detail}</div></article>`;
  }

  function renderEvidence() {
    const ids = selectedCase.evidence_ids || [];
    const records = bundle.evidence.filter(e => ids.includes(e.id));
    $('evidence-count').textContent = `${records.length} 条`;
    $('pit-evidence').innerHTML = records.length ? records.map(e => `<article class="pit-evidence-item"><div class="pit-evidence-top"><strong>${esc(e.ticker)} · ${esc(METRICS[e.metric] || e.metric)} · ${esc(formatValue(e.value,e.unit))}</strong><code>${esc(e.id)}</code></div><dl>${fact('报告期间',`${e.period_start || '未提供'} → ${e.period_end || '未提供'}`)}${fact('SEC 申报日期',e.filing_date || '未提供')}${fact('本地数据抓取时间',dateTime(e.captured_at || bundle.provenance?.captured_at))}${fact('申报编号',e.accession || '未提供')}${fact('原始指标标签',e.taxonomy_tag || e.metric)}${fact('申报类型',e.form || '记录未提供')}</dl><div class="pit-evidence-links">${safeLink(e.url,'查看 SEC 原始申报')}${safeLink(e.source_url,'查看 SEC Company Facts')}</div><p class="pit-hash">保存的原始响应 SHA-256：${esc(e.upstream_sha256 || '记录未提供')}</p></article>`).join('') : '<p class="pit-table-note">记录中没有可关联的证据。请检查原始数据。</p>';
  }

  function renderCase() {
    const cases = filteredCases();
    const index = cases.findIndex(c => c.id === selectedCase.id);
    const condition = $('condition-filter').value;
    $('case-position').textContent = `问题 ${index + 1} / ${cases.length}`;
    $('case-id').textContent = `${selectedCase.ticker} · ${selectedCase.id}`;
    $('case-question').textContent = selectedCase.question;
    $('event-date').textContent = `${selectedCase.filing_date} · ${selectedCase.ticker}`;
    $('condition-description').innerHTML = `<strong>${esc(CONDITIONS[condition].label)}：</strong>${esc(CONDITIONS[condition].description)}`;
    $('pit-pair').innerHTML = renderObservation('pre',condition) + renderObservation('post',condition);
    $('previous-case').disabled = index <= 0;
    $('next-case').disabled = index >= cases.length - 1;
    renderEvidence();
    const params = new URLSearchParams();
    if ($('issuer-filter').value !== 'all') params.set('issuer',$('issuer-filter').value);
    params.set('case',selectedCase.id);
    params.set('condition',condition);
    history.replaceState(null,'',`${location.pathname}?${params}${location.hash}`);
  }

  function bind() {
    $('issuer-filter').addEventListener('change',() => setCases(selectedCase.id));
    $('case-filter').addEventListener('change',() => {
      selectedCase = filteredCases().find(c => c.id === $('case-filter').value);
      renderCase();
    });
    $('condition-filter').addEventListener('change',renderCase);
    for (const [id,step] of [['previous-case',-1],['next-case',1]]) {
      $(id).addEventListener('click',() => {
        const cases = filteredCases();
        const index = cases.findIndex(c => c.id === selectedCase.id);
        selectedCase = cases[index + step] || selectedCase;
        $('case-filter').value = selectedCase.id;
        renderCase();
      });
    }
    $('pit-retry').addEventListener('click',load);
  }

  async function load() {
    $('pit-error').hidden = true;
    $('pit-content').hidden = true;
    $('pit-loading').hidden = false;
    try {
      const response = await fetch(DATA_URL,{cache:'no-cache'});
      if (!response.ok) throw new Error(`实验数据读取失败（HTTP ${response.status}）。`);
      const data = await response.json();
      if (data.schema_version !== 1 || !Array.isArray(data.cases) || !data.cases.length || !Array.isArray(data.runs) || !Array.isArray(data.evidence) || !data.protocol) throw new Error('实验记录格式不完整；尚未显示任何模型成绩。');
      bundle = data;
      renderOverview();
      const params = new URLSearchParams(location.search);
      const issuers = [...new Set(bundle.cases.map(c => c.ticker))];
      $('issuer-filter').innerHTML = '<option value="all">全部公司</option>' + issuers.map(t => `<option value="${esc(t)}">${esc(t)} · ${esc(COMPANIES[t] || t)}</option>`).join('');
      if (issuers.includes(params.get('issuer'))) $('issuer-filter').value = params.get('issuer');
      if (Object.hasOwn(CONDITIONS,params.get('condition'))) $('condition-filter').value = params.get('condition');
      setCases(params.get('case'));
      $('pit-content').hidden = false;
    } catch (error) {
      $('pit-error-detail').textContent = error.message || '网络或数据读取错误。';
      $('pit-error').hidden = false;
    } finally { $('pit-loading').hidden = true; }
  }

  bind();
  load();
})();

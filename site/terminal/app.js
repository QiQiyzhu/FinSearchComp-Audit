import {parseQuestion} from './query.mjs';
import {selectState,resolveMetric} from './data.mjs';
import {LiveResearchUI} from './live.mjs';

const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=id=>`<svg aria-hidden="true"><use href="#i-${id}"/></svg>`;
const REPO='https://github.com/QiQiyzhu/FinSearchComp-Audit';
const VIEWS={live:['联网研究','LIVE FINANCIAL RESEARCH','从问题到资料，从证据到判断；查看一次真实研究如何完成。'],research:['公司研究','FUNDAMENTAL RESEARCH','从财报数据出发，把问题、计算和证据放在一起。'],compare:['同业比较','COMPARABLE ANALYSIS','看清相同指标，也看清不同公司的财务期间。'],evidence:['证据库','SOURCE INTELLIGENCE','每一个数值都能追溯到原始申报、标签与版本。'],pit:['时点评测','POINT-IN-TIME LAB','回到信息披露之前，检验答案是否越过时间边界。'],quality:['质量与方法','EVALUATION & METHODOLOGY','把真实成绩、失败案例与能力边界一起公开。']};
const ALIASES={MSFT:/微软|microsoft|\bMSFT\b/i,AAPL:/苹果|apple|\bAAPL\b/i,NVDA:/英伟达|nvidia|\bNVDA\b/i,GOOGL:/谷歌|alphabet|google|\bGOOGL\b/i,META:/meta|脸书|facebook/i,AMZN:/亚马逊|amazon|\bAMZN\b/i,TSLA:/特斯拉|tesla|\bTSLA\b/i,AMD:/超微|\bAMD\b/i};
const DEFAULT_QUESTION='营收、营业利润率和自由现金流是多少？';
const METRIC_ORDER=['revenue','operating_income','net_income','operating_cash_flow','free_cash_flow','operating_margin','net_margin','gross_margin','research_and_development','rd_ratio','cash_conversion','capital_expenditure','assets','liabilities','cash','current_ratio'];
const state={cube:null,view:'research',ticker:'MSFT',peer:'AAPL',year:2024,asof:'',question:DEFAULT_QUESTION,plan:null,answers:[],chart:'revenue',history:[],saved:[],quality:null,v3quality:null,pit:null,gate:null,config:null,backend:'',token:'',modelReport:null,pitCase:null,pitCondition:'pit_filtered'};
const liveResearch=new LiveResearchUI({getConnection:()=>({apiBase:state.backend,token:state.token,config:state.config,checking:state.connectionChecking,progress:state.connectionProgress}),onUpdate:()=>{if(state.cube&&state.view==='live')render();},onConnect:()=>{const button=document.querySelector('[data-connect]');button?.click();},onReconnect:()=>reconnectBackend(),toast});
const operationLabel={value:'',growth_pct:'同比增长',growth_amount:'同比变化金额',change_pp:'同比变化'};
let toastTimer;
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,2800);}
function safeRead(key){try{return JSON.parse(localStorage.getItem(key)||'[]');}catch{return [];}}
function persist(){try{localStorage.setItem('finsearch-v3-saved',JSON.stringify(state.saved));localStorage.setItem('finsearch-v3-history',JSON.stringify(state.history));}catch{toast('浏览器未允许保存本地工作区。');}}
function currentWorkspace(){return {view:state.view,ticker:state.ticker,peer:state.peer,year:state.year,asof:state.asof,question:state.question};}
function workspaceKey(w){return [w.view,w.ticker,w.peer,w.year,w.asof,w.question].join('|');}
function updateURL(){if(state.view==='live'){const params=new URLSearchParams({view:'live'});if(liveResearch.state.job?.id)params.set('live_run',liveResearch.state.job.id);history.replaceState(null,'',`?${params}`);return;}const params=new URLSearchParams({view:state.view,ticker:state.ticker,year:String(state.year),asof:state.asof});if(state.view==='compare')params.set('peer',state.peer);if(state.question!==DEFAULT_QUESTION)params.set('q',state.question);history.replaceState(null,'',`?${params}`);}
function addHistory(){const item=currentWorkspace();state.history=[item,...state.history.filter(x=>workspaceKey(x)!==workspaceKey(item))].slice(0,6);persist();renderWorkspace();}
function renderWorkspace(){
  $('#saved-count').textContent=state.saved.length;
  for(const [kind,items] of [['saved',state.saved],['history',state.history]])$('#'+kind+'-list').innerHTML=items.length?items.map((w,i)=>`<button class="saved-item" data-restore="${kind}:${i}" title="${esc(w.question)}">${icon(kind==='saved'?'bookmark':'clock')}<span>${esc(w.ticker)}${w.view==='compare'?' / '+esc(w.peer):''} · FY${w.year}</span></button>`).join(''):'<p class="empty-small">'+(kind==='saved'?'收藏研究，方便下次接着看。':'研究记录会保存在此浏览器。')+'</p>';
}
function selectControls(){
  $('#company-select').value=state.ticker;$('#peer-select').value=state.peer;
  const years=state.cube.companies[state.ticker].fiscal_years;
  $('#year-select').innerHTML=[...years].sort((a,b)=>b-a).map(y=>`<option value="${y}">FY${y}</option>`).join('');
  if(!years.includes(state.year))state.year=years.at(-1);
  $('#year-select').value=String(state.year);$('#asof-input').value=state.asof;$('#question-input').value=state.question;
}
function switchView(view){state.view=VIEWS[view]?view:'research';state.modelReport=null;closeSidebar();render();}
function closeSidebar(){$('#sidebar').classList.remove('open');$('#sidebar-scrim').hidden=true;$('#menu-toggle').setAttribute('aria-expanded','false');}
function recordAnswer(request,ticker=state.ticker){return resolveMetric(state.cube,ticker,state.asof,request.fiscal_year,request.metric_id,request.operation);}
function updateResearch(){
  state.ticker=$('#company-select').value;state.peer=$('#peer-select').value;state.year=Number($('#year-select').value);state.asof=$('#asof-input').value;state.question=$('#question-input').value.trim()||DEFAULT_QUESTION;state.modelReport=null;
  const entities=Object.entries(ALIASES).filter(([,pattern])=>pattern.test(state.question)).map(([ticker])=>ticker);
  if(entities.length===1)state.ticker=entities[0];
  if(entities.length===2){state.ticker=entities[0];state.peer=entities[1];state.view='compare';}
  const plan=parseQuestion(state.question,{ticker:state.ticker,cutoff:state.asof,year:state.year});
  if(Number.isInteger(plan.fiscal_year)&&state.cube.companies[state.ticker].fiscal_years.includes(plan.fiscal_year))state.year=plan.fiscal_year;
  selectControls();render();addHistory();
}
function formatValue(value,unit='USD'){
  const n=Number(value);if(!Number.isFinite(n))return '—';
  if(unit==='USD'){const abs=Math.abs(n);return abs>=1e9?'$'+(n/1e9).toFixed(2)+'B':abs>=1e6?'$'+(n/1e6).toFixed(2)+'M':'$'+n.toLocaleString('en-US');}
  return n.toFixed(2)+(unit==='percentage_points'?' pp':unit==='x'?'×':unit==='%'?'%':'');
}
function citation(ids,label='查看证据'){return ids?.length?`<button class="citation" data-evidence="${esc(ids.join(','))}">${icon('file')}${esc(label)}${ids.length>1?' '+ids.length:''}</button>`:'';}
function answerTitle(answer){return answer.label+(operationLabel[answer.operation]?' · '+operationLabel[answer.operation]:'');}
function renderAnswer(answer){const available=answer.status==='available';return `<article class="answer-card ${available?'':'missing'}" data-metric="${esc(answer.metric_id)}" data-operation="${esc(answer.operation)}"><div class="answer-label"><span>${esc(answerTitle(answer))}</span>${icon('chart')}</div><div class="answer-value">${available?esc(answer.display_value):'证据不足'}${available&&answer.unit==='USD'?'<small>美元</small>':''}</div><div class="answer-bottom"><span class="answer-period">FY${answer.fiscal_year}${answer.comparison_fiscal_year?' vs FY'+answer.comparison_fiscal_year:''}</span>${available?citation(answer.evidence_ids):'<span class="pill warning">保留判断</span>'}</div><details class="calculation"><summary>${available?(answer.operation==='reported'?'原始申报口径':'计算口径与期间'):'查看缺失原因'}</summary><p>${esc(available?answer.formula:answer.reason)}</p>${available?`<p>${esc(answer.period_start||'时点')} → ${esc(answer.period_end)}</p>`:''}</details></article>`;}
function renderGap(gaps){return gaps.length?`<div class="gap-banner">${icon('shield')}<div><strong>这次问题有明确的回答边界</strong><ul>${gaps.map(g=>`<li>${esc(g)}</li>`).join('')}</ul></div></div>`:'';}
function companyHeading(){const company=state.cube.companies[state.ticker];const selected=selectState(state.cube,state.ticker,state.asof);return `<div class="result-heading"><div class="company-identity"><div class="ticker-avatar">${esc(state.ticker)}</div><div><h2>${esc(company.name)}</h2><small>${esc(state.ticker)} · FY${state.year} · 美国证券交易委员会</small></div></div><div class="result-meta"><span class="pill">${icon('check')}证据日期已过滤</span><div>截止 ${esc(state.asof)}${selected.event?' · 最近可用申报 '+esc(selected.event.filed):''}</div></div></div>`;}
function trendChart(metric='revenue'){
  const annuals=selectState(state.cube,state.ticker,state.asof).annuals.filter(a=>a.fiscal_year<=state.year).slice(-5);
  const values=annuals.map(a=>({year:a.fiscal_year,metric:a.metrics[metric]}));
  const usable=values.filter(x=>x.metric?.status==='available');
  if(!usable.length)return '<div class="empty-state"><p>当前截止日期没有可绘制的年度证据。</p></div>';
  const nums=usable.map(x=>Number(x.metric.value)),max=Math.max(...nums,0),min=Math.min(...nums,0),range=max-min||1,w=640,h=230,pad=42,base=171,chartH=124,step=(w-pad*2)/Math.max(values.length,1),barW=Math.min(52,step*.45),yFor=n=>base-(n-min)/range*chartH,zero=yFor(0);
  const ticks=[...new Set([min,(min+max)/2,max,0])].sort((a,b)=>a-b);
  const axis=ticks.map(n=>{const y=yFor(n);return `<line x1="${pad}" x2="${w-16}" y1="${y}" y2="${y}" stroke="${n===0?'#cad8de':'#edf1f4'}" stroke-width="1"/><text x="${pad-10}" y="${y+3}" text-anchor="end">${metric==='operating_margin'?n.toFixed(0)+'%':(n/1e9).toFixed(0)}</text>`;}).join('');
  const bars=values.map((x,i)=>{const bx=pad+step*i+step/2;const ok=x.metric?.status==='available',n=ok?Number(x.metric.value):0,y=yFor(n),bh=Math.abs(y-zero);return `<g><text x="${bx}" y="${base+32}" class="chart-year" text-anchor="middle">FY${x.year}</text>${ok?`<rect data-value="${n}" data-zero="${zero}" x="${bx-barW/2}" y="${Math.min(y,zero)}" width="${barW}" height="${Math.max(bh,1)}" fill="${n<0?'#c5a083':i===values.length-1?'#178e77':'#a9cdc3'}" rx="4"/><text x="${bx}" y="${n<0?y+15:y-9}" text-anchor="middle" class="bar-label">${esc(x.metric.display_value)}</text>`:`<text x="${bx}" y="${zero-8}" text-anchor="middle">缺失</text>`}</g>`;}).join('');
  return `<svg class="trend-chart" role="img" aria-label="${esc(state.cube.metric_catalog[metric].label)}按财年趋势" viewBox="0 0 ${w} ${h}">${axis}${bars}</svg>`;
}
function renderFinancialTable(){
  const annuals=selectState(state.cube,state.ticker,state.asof).annuals.filter(a=>a.fiscal_year<=state.year).slice(-3);
  if(!annuals.length)return '<div class="empty-state"><p>没有符合截止日期和财年条件的数据。</p></div>';
  return `<div class="table-scroll"><table class="financial-table"><thead><tr><th>核心指标</th>${annuals.map(a=>`<th>FY${a.fiscal_year}</th>`).join('')}<th>最新同比</th></tr></thead><tbody>${METRIC_ORDER.map(id=>{const current=annuals.at(-1).changes[id];return `<tr><td>${esc(state.cube.metric_catalog[id].label)}</td>${annuals.map(a=>{const m=a.metrics[id];return `<td>${m.status==='available'?`<button data-evidence="${esc(m.evidence_ids.join(','))}">${esc(m.display_value)}</button>`:'<span class="missing-cell" title="'+esc(m.reason)+'">—</span>'}</td>`;}).join('')}<td class="${current?.status==='available'?(Number(current.value)>=0?'positive':'negative'):'missing-cell'}">${current?.status==='available'?esc(current.display_value):'—'}</td></tr>`;}).join('')}</tbody></table></div><div class="table-footnote">B = 十亿美元 · M = 百万美元 · pp = 百分点；缺失值保留为空。点击数值查看来源。</div>`;
}
function findings(){
  const get=(id,op='value')=>resolveMetric(state.cube,state.ticker,state.asof,state.year,id,op);const items=[];
  for(const [id,op,title] of [['revenue','growth_pct','收入趋势'],['operating_margin','change_pp','经营盈利能力'],['free_cash_flow','value','自由现金流'],['cash_conversion','value','利润的现金支撑']]){
    const m=get(id,op);if(m.status!=='available'){items.push({risk:true,title:title+'待确认',text:m.reason,ids:[]});continue;}
    const n=Number(m.value);let text='';let risk=n<0;
    if(id==='revenue')text=`营业收入同比${n>=0?'增长':'下降'} ${Math.abs(n).toFixed(2)}%，以相邻财年收入为基数。`;
    if(id==='operating_margin')text=`营业利润率较前一年${n>=0?'提高':'下降'} ${Math.abs(n).toFixed(2)} 个百分点。`;
    if(id==='free_cash_flow')text=`经营现金流扣除 PP&E 现金支出后为 ${m.display_value}。此口径不包括所有投资活动。`;
    if(id==='cash_conversion'){risk=n<100;text=`经营现金流为净利润的 ${m.display_value}；应结合应收、存货和非现金项目理解。`;}
    items.push({risk,title,text,ids:m.evidence_ids});
  }
  return items;
}
function renderFindings(){return `<section class="panel"><div class="panel-header"><h3>研究信号</h3><span class="sub-label">规则解释</span></div><div class="findings-list">${findings().map(f=>`<div class="finding ${f.risk?'risk':''}"><span class="finding-mark">${icon(f.risk?'clock':'check')}</span><div><h4>${esc(f.title)}</h4><p>${esc(f.text)}</p>${citation(f.ids)}</div></div>`).join('')}</div></section>`;}
function renderScope(){const annual=selectState(state.cube,state.ticker,state.asof).annuals.find(a=>a.fiscal_year===state.year);const available=annual?Object.values(annual.metrics).filter(m=>m.status==='available').length:0;return `<section class="panel"><div class="panel-header"><h3>这份研究的边界</h3>${icon('shield')}</div><div class="panel-body"><ul class="scope-list"><li><span>报告期间</span><strong>${annual?esc(annual.period_start)+'<br>'+esc(annual.period_end):'当前不可用'}</strong></li><li><span>指标可用性</span><strong>${available} / ${Object.keys(state.cube.metric_catalog).length}<br><small>不是准确率</small></strong></li><li><span>数据抓取</span><strong>${esc(state.cube.captured_at.slice(0,10))}</strong></li><li><span>计算模式</span><strong>冻结数据 · 精确公式</strong></li></ul><p class="scope-note">申报从下一日纳入。历史条目按版本重建，未覆盖更早的业绩公告；财务信号不等同于未来收益预测。</p></div></section>`;}
function renderModel(){if(!state.config?.features?.terminal)return '';const available=state.config.modes?.snapshot?.available,requiresToken=state.config.modes?.snapshot?.requires_token&&!state.token,disabled=!available||requiresToken||state.busy||!state.plan?.supported;return `<section class="panel model-panel"><div class="panel-header"><h3>年度研究摘要</h3><span class="sub-label">DeepSeek</span></div><div class="panel-body"><p class="scope-note" style="margin:0;padding:0;border:0">从核验后的年度事实中组织摘要。同比问题继续以上方精确计算为准。</p><button id="generate-brief" class="button secondary" style="margin-top:14px" ${disabled?'disabled':''}>${state.busy?'摘要任务运行中…':'DeepSeek 整理年度摘要'} ${icon('arrow')}</button>${!available?'<p class="scope-note">后端已连接，但尚未开启 DeepSeek 模式；请在服务器配置模型凭据和运行开关。</p>':requiresToken?'<p class="scope-note">此后端需要访问令牌，请在连接设置中填写。</p>':''}<div id="model-output">${state.modelReport?modelReportHTML(state.modelReport):''}</div></div></section>`;}
function modelReportHTML(report){return `<p class="scope-note">模型状态：${esc(report.model?.status??'未知')} · ${report.model?.used?'已调用真实模型':'未使用模型'}</p>${(report.brief??[]).map(c=>`<p class="scope-note">${esc(c.text)} ${citation(c.evidence_ids)}</p>`).join('')}${report.model?.receipt?`<details class="calculation"><summary>调用记录</summary><p>${esc(JSON.stringify(report.model.receipt))}</p></details>`:''}`;}
function renderResearch(){
  state.plan=parseQuestion(state.question,{ticker:state.ticker,cutoff:state.asof,year:state.year});
  state.answers=state.plan.supported?state.plan.requests.map(r=>recordAnswer(r)):[];
  const gaps=[...state.plan.gaps];if(!state.asof)gaps.push('请选择证据截止日期。');
  return companyHeading()+renderGap(gaps)+`<div class="answer-grid" id="answer-grid">${state.answers.map(renderAnswer).join('')}</div><div class="analysis-layout"><div class="main-column"><section class="panel"><div class="panel-header"><h3>经营表现趋势</h3><div class="segmented" role="group" aria-label="趋势指标"><button data-chart="revenue" class="${state.chart==='revenue'?'active':''}">营业收入</button><button data-chart="free_cash_flow" class="${state.chart==='free_cash_flow'?'active':''}">自由现金流</button><button data-chart="operating_margin" class="${state.chart==='operating_margin'?'active':''}">营业利润率</button></div></div><p class="panel-description">所选截止日期下可用的年度记录 · ${state.chart==='operating_margin'?'百分比':'金额单位：十亿美元'}</p><div class="chart-wrap" id="trend-chart">${trendChart(state.chart)}</div><div class="chart-legend"><span><i class="legend-square"></i>已披露年度数据</span><span>每个年度独立保留原始期间</span></div></section><section class="panel"><div class="panel-header"><h3>财务概览</h3><span class="sub-label">最近三个可用财年</span></div><div style="height:17px"></div>${renderFinancialTable()}<div class="pipeline-strip"><span>${icon('check')}来源筛选</span><span class="pipe-arrow">→</span><span>${icon('check')}期间对齐</span><span class="pipe-arrow">→</span><span>${icon('check')}精确运算</span><span class="pipe-arrow">→</span><span>${icon('check')}证据校验</span></div></section>${renderModel()}</div><aside class="side-column">${renderFindings()}${renderScope()}</aside></div>`;
}
function renderCompare(){
  state.plan=parseQuestion(state.question,{ticker:state.ticker,cutoff:state.asof,year:state.year});
  const ids=state.plan.supported&&state.plan.metric_ids.length?state.plan.metric_ids:METRIC_ORDER.slice(0,8);
  const requests=state.plan.supported?state.plan.requests:ids.map(metric_id=>({metric_id,fiscal_year:state.year,operation:'value'}));
  state.answers=requests.flatMap(r=>[recordAnswer(r,state.ticker),recordAnswer(r,state.peer)]);
  const a=selectState(state.cube,state.ticker,state.asof).annuals.find(x=>x.fiscal_year===state.year),b=selectState(state.cube,state.peer,state.asof).annuals.find(x=>x.fiscal_year===state.year);
  const same=a&&b&&a.period_start===b.period_start&&a.period_end===b.period_end;
  return renderGap(state.plan.gaps)+`<div class="comparison-intro"><strong>${same?'两个年度期间一致':'先看期间，再看数值'}</strong> · ${same?'可在相同年度区间并排分析。':'财年相同不代表覆盖日期相同。本页保留各自期间，不对不同期间的值自动排序或计算差值。'}${state.ticker===state.peer?' 当前选择了同一家公司，请选择不同公司。':''}</div><section class="panel"><div class="table-scroll"><table class="financial-table comparison-table"><thead><tr><th>财务指标</th>${[[state.ticker,a],[state.peer,b]].map(([ticker,annual])=>`<th><strong>${esc(ticker)}</strong>${esc(state.cube.companies[ticker].name)}<small>${annual?esc(annual.period_start)+' → '+esc(annual.period_end):'该年度当前不可用'}</small></th>`).join('')}<th>口径检查</th></tr></thead><tbody>${requests.map((r,i)=>{const left=state.answers[i*2],right=state.answers[i*2+1],aligned=left.period_start===right.period_start&&left.period_end===right.period_end;return `<tr><td>${esc(answerTitle(left))}<br><span class="sub-label">FY${r.fiscal_year}</span></td>${[left,right].map(m=>`<td>${m.status==='available'?`<button data-evidence="${esc(m.evidence_ids.join(','))}">${esc(m.display_value)}</button>`:`<span class="missing-cell" title="${esc(m.reason)}">暂无可用证据</span>`}</td>`).join('')}<td class="difference-note">${left.status!=='available'||right.status!=='available'?'证据缺失，保留判断':aligned?'期间、币种与指标定义一致':'期间不同，仅并排呈现'}</td></tr>`;}).join('')}</tbody></table></div><div class="table-footnote">没有证据的数值不按 0 处理；同业差异不能单独构成买卖建议。</div></section><div class="comparison-footer">${[[state.ticker,a],[state.peer,b]].map(([ticker,annual])=>`<section class="panel compare-company"><h3>${esc(ticker)} · 数据口径</h3><p>FY${state.year} · ${annual?esc(annual.period_start)+' 至 '+esc(annual.period_end):'当前截止日无该年度'}<br>截止 ${esc(state.asof)} 的最后可用申报版本。点击上方数值，检查申报日期、XBRL 标签和原始事实。</p><button class="button text" data-open-company="${ticker}">打开公司研究 ${icon('arrow')}</button></section>`).join('')}</div>`;
}
function visibleEvidence(){const selected=selectState(state.cube,state.ticker,state.asof);const annual=selected.annuals.find(a=>a.fiscal_year===state.year);const ids=new Set(annual?Object.values(annual.metrics).flatMap(m=>m.evidence_ids):[]);return [...ids].map(id=>state.cube.evidence[id]).filter(Boolean).sort((a,b)=>a.metric_id.localeCompare(b.metric_id));}
function evidenceCards(filter=''){const rows=visibleEvidence().filter(e=>[e.metric_id,e.taxonomy_tag,e.accession,state.cube.metric_catalog[e.metric_id]?.label].join(' ').toLowerCase().includes(filter.toLowerCase()));return rows.length?rows.map(e=>`<button class="evidence-card" data-evidence="${esc(e.id)}"><span class="file-icon">${icon('file')}</span><span><h3>${esc(state.cube.metric_catalog[e.metric_id]?.label??e.metric_id)} <span class="pill neutral">${esc(e.form)}</span></h3><p>${esc(e.ticker)} · FY${e.fiscal_year} · ${esc(e.period_end)}<br>${esc(e.taxonomy_tag)}</p><span class="doc-tags"><span>申报 ${esc(e.filed)}</span><span>可用 ${esc(e.available_from)}</span><span>${esc(formatValue(e.value,e.unit))}</span></span></span></button>`).join(''):'<div class="empty-state"><h3>没有匹配的证据</h3><p>尝试其他指标关键词，或调整财年与截止日期。</p></div>';}
function renderEvidence(){state.answers=[];return `<div class="evidence-toolbar"><label class="search-input">${icon('search')}<input id="evidence-search" type="search" placeholder="搜索指标、XBRL 标签或申报编号" aria-label="搜索证据"></label><span class="evidence-count">${visibleEvidence().length} 条可用原始事实 · FY${state.year}</span></div><div class="evidence-list" id="evidence-list">${evidenceCards()}</div><div class="quality-note">证据显示原始申报期、申报日、次日可用规则与完整哈希。派生指标引用全部运算输入；抓取发生在 ${esc(state.cube.captured_at.slice(0,10))}，此页面不宣称是历史时点的完整档案。</div>`;}
function renderPIT(){
  if(!state.pit)return '<section class="panel empty-state"><h3>首轮实验记录正在准备</h3><p><a href="../workbench/pit.html">打开完整 PIT 实验页 ↗</a></p></section>';
  const p=state.pit,c=p.cases.find(x=>x.id===state.pitCase)??p.cases[0];state.pitCase=c.id;
  const conditionNames={closed_book:'无检索材料',unfiltered:'未过滤材料',pit_filtered:'按时点过滤'};
  const phases=['pre','post'].map(phase=>{const run=p.runs.find(r=>r.case_id===c.id&&r.phase===phase&&r.condition===state.pitCondition);return {phase,run};});
  const rows=p.summary.by_condition;
  return `${renderGate()}<div class="overview-cards"><article class="overview-card"><div class="label">首轮真实模型实验</div><div class="number">${p.summary.attempted_runs}<small> 次调用</small></div><p>6 道题 × 2 个时点 × 3 个条件</p></article><article class="overview-card"><div class="label">证据事件</div><div class="number">3<small> 份申报</small></div><p>MSFT / AAPL / NVDA · 开发试点</p></article><article class="overview-card"><div class="label">模型</div><div class="number" style="font-size:21px">DeepSeek</div><p>${esc(p.execution?.requested_model??'记录中保留模型别名')} · 原始记录可回放</p></article></div><section class="panel"><div class="panel-header"><h3>事件回放</h3><a class="quiet-link" href="../workbench/pit.html">查看完整实验 ${icon('external')}</a></div><div class="panel-body"><p style="font-size:14px;line-height:1.8" id="pit-question">${esc(c.question)}</p><div class="pit-controls"><label>选择试题<select id="pit-case">${p.cases.map(x=>`<option value="${esc(x.id)}" ${x.id===c.id?'selected':''}>${esc(x.ticker+' · '+x.question)}</option>`).join('')}</select></label><label>实验条件<select id="pit-condition">${Object.entries(conditionNames).map(([id,label])=>`<option value="${id}" ${id===state.pitCondition?'selected':''}>${label}</option>`).join('')}</select></label></div></div></section><div class="pit-columns">${phases.map(({phase,run})=>{const before=phase==='pre';const answer=run?.answer;const display=answer?.action==='abstain'?'模型选择拒答':answer?.value!=null?String(answer.value)+(answer.unit?' '+answer.unit:''):run?.status==='not_run'?'等待真实运行':'查看完整原始响应';return `<section class="panel pit-phase"><h3>${before?'披露之前':'披露之后'} <span class="pill ${before?'warning':''}">${before?'目标申报不可用':'目标申报已可用'}</span></h3><div class="pit-time">${esc(before?c.pre_as_of:c.post_as_of)}</div><div class="source-block" style="margin:14px 0">${esc(display)}</div><p>${before?'按本实验指定申报语料，合规行为应是拒答。模型可能凭记忆回答，这与申报时点证据合规是不同问题。':'按指定申报与期间检查精确数值，同时检查回答引用是否越过截止日期。'}</p></section>`;}).join('')}</div><section class="panel"><div class="panel-header"><h3>首轮观测成绩</h3><span class="sub-label">小样本开发试点</span></div><div style="height:18px"></div><div class="table-scroll"><table class="financial-table"><thead><tr><th>条件</th><th>披露前正确拒答</th><th>披露后数值正确</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${conditionNames[r.condition]}</td><td>${r.pre_correct_abstention} / ${r.pre_scored}</td><td>${r.post_numeric_correct} / ${r.post_scored}</td></tr>`).join('')}</tbody></table></div></section><div class="quality-note"><strong>数值答对不等于时间合规。</strong>首轮“过滤未来材料”仍出现越界回答；过滤还同时移除了申报时间线索，因此不能断言过滤策略更差。下一轮必须分别控制时间元数据、记忆许可和证据闸门。此试点不能测出真实训练截止日，也不支持模型排名。<br><a href="${REPO}/blob/main/docs/PIT_PILOT_RESULTS.md" target="_blank" rel="noopener">实验分析与原始记录 ↗</a></div><div class="methods-grid"><section class="panel method-card"><span class="step-number">01 / REPLAY</span><h3>从数据版本到研究回放</h3><p>公司研究页可以调整截止日期。系统只使用该日期之前已生效的申报版本，缺少该财年时明确保留判断。</p><button class="button text" data-view="research">回到研究工作台 ${icon('arrow')}</button></section><section class="panel method-card"><span class="step-number">02 / RESEARCH</span><h3>面向老师的研究计划</h3><p>将证据时点合规与模型时间知识画像分开测量，逐步扩展申报修订、新闻与月度采样。</p><a href="${REPO}/blob/main/docs/PIT_RESEARCH_PROPOSAL.md" target="_blank" rel="noopener">查看研究问题、相关工作与汇报稿 ↗</a></section></div>`;
}
function renderGate(){
  if(!state.gate)return '';const {before,after}=state.gate.summary;
  return `<section class="panel" id="pit-gate-result" style="margin-bottom:22px"><div class="panel-header"><h3>V3 证据闸门：同一批输出的前后对照</h3><span class="pill">系统干预回放</span></div><p class="panel-description">复用首轮已公开的 36 个模型输出 · 新增模型调用 ${state.gate.new_model_calls} 次 · 不作为新的盲测或模型能力提升</p><div style="height:16px"></div><div class="table-scroll"><table class="financial-table"><thead><tr><th>观察项</th><th>原始输出</th><th>证据闸门后</th></tr></thead><tbody><tr><td>披露前无依据回答</td><td>${before.pre_unsupported_answers} / ${before.pre_planned}</td><td>${after.pre_unsupported_answers} / ${after.pre_planned}</td></tr><tr><td>披露后回答覆盖</td><td>${before.post_answers} / ${before.post_planned}</td><td>${after.post_answers} / ${after.post_planned}</td></tr><tr><td>决策正确</td><td>${before.decision_correct} / ${before.planned}</td><td>${after.decision_correct} / ${after.planned}</td></tr></tbody></table></div><div class="panel-body"><p style="font-size:11px;line-height:1.9;color:#8196a4">闸门要求答案引用全部可用输入，且数值等于精确重算。它阻止了越界回答，同时拦截 ${state.gate.summary.post_answer_coverage_loss} 个数值正确但没有引用的披露后答案。这是合规与回答覆盖之间的真实取舍。</p><a class="button text" href="${esc(state.gate.methods_url)}" target="_blank" rel="noopener">查看协议与全部原始记录 ${icon('external')}</a></div></section>`;
}
function renderQuality(){
  const q=state.quality,stats=state.cube.statistics;
  const cards=q?[['V2 首次完整问题回答',`${q.current.overall.fulfilled} / ${q.current.overall.cases}`,'项目自编题集首次运行；不代表开放金融研究准确率。'],['V2 首次请求数值正确',`${q.current.overall.correct_requested_values} / ${q.current.overall.requested_values}`,'缺失答案计错；分别核对期间、口径与引用。'],['V2 修复后同题回归',`${q.post_eval_regression.fulfilled} / ${q.post_eval_regression.cases}`,'已公开题目上的回归结果，不能称为新的保留集。']]:[];
  return `${state.v3quality?renderV3Quality(state.v3quality):''}<div class="overview-cards">${cards.map(([label,note,description])=>`<article class="overview-card"><div class="label">${label}</div><div class="number">${note}</div><p>${description}</p></article>`).join('')}</div>${state.v3quality?'</details>':''}<div class="quality-note"><strong>真实模型接入验证。</strong>已完成 2 次 DeepSeek 调用，保留 8 项核验后的年度答案，共 1,617 tokens。此项验证接口与约束输出，不计入财务准确率。<br><a href="${REPO}/blob/main/docs/verification/terminal-deepseek-live.json" target="_blank" rel="noopener">查看模型调用与原始答案记录 ↗</a></div><div class="quality-note"><strong>成绩按轨道披露。</strong>数值运算、自然语言意图、模型时间合规分别评价。V2 的 40 题成绩是历史对照，不能直接视为新终端全功能的准确率。每次升级都应留下首评、修复后回归及未覆盖范围。</div><div class="methods-grid"><section class="panel method-card"><span class="step-number">01 / DATA</span><h3>版本化的财务证据</h3><p>${stats.companies} 家公司，${stats.metrics} 类指标，${stats.events} 个披露事件，${stats.annual_versions} 个年度版本。数据按 SEC 原始行和申报版本重建，同日冲突保留判断；日期截止采用申报次日可用。</p><a href="../workbench/data/finance_cube.json" download>下载完整财务数据集 ↗</a></section><section class="panel method-card"><span class="step-number">02 / COMPUTE</span><h3>语言理解与精确运算分开</h3><p>受支持的中英文问题编译为结构化指标请求。浏览器读取预先核验的分数精确运算结果，不让语言模型填写财务数值。不存在的指标或预测请求会显示缺口。</p><a href="${REPO}/tree/main/research_workbench" target="_blank" rel="noopener">查看计算与证据检查实现 ↗</a></section><section class="panel method-card"><span class="step-number">03 / EVALUATE</span><h3>失败案例是产品的一部分</h3><p>历史首评中的增长金额和研发占比意图错误单独记录。修复后保留首次成绩，避免把针对已知题目的修复误报为独立泛化能力。</p><a href="${REPO}/blob/main/docs/WORKBENCH_QUALITY.md" target="_blank" rel="noopener">查看 V2 完整评测协议 ↗</a></section><section class="panel method-card"><span class="step-number">04 / PRODUCT</span><h3>研究流程与可交付成果</h3><p>参考金融研究产品对统一研究、结构化财务与可追溯成果的组织方式。这里仍是公开数据研究项目，未宣称覆盖机构终端的数据、风控或协作能力。</p><a href="https://rogo.com/" target="_blank" rel="noopener">Rogo 官方产品 ↗</a>　<a href="https://www.alpha-sense.com/platform/" target="_blank" rel="noopener">AlphaSense 官方产品 ↗</a></section></div><div class="quality-note">当前不评估实时行情、新闻检索、投资收益、真实训练截止日或企业级多租户能力。财务数据抓取日：${esc(state.cube.captured_at.slice(0,10))}。<br><a href="${REPO}/blob/main/docs/PIT_RESEARCH_PROPOSAL.md" target="_blank" rel="noopener">论文方法与下一步研究计划 ↗</a></div>`;
}
function renderV3Quality(q){
  const s=q.structured_first,n=q.nlq_first,r=q.structured_regression,nr=q.nlq_regression;
  return `<div class="overview-cards" id="v3-quality-cards"><article class="overview-card"><div class="label">V3 结构化查询 · 首评</div><div class="number">${s.summary.strict_passed}<small> / ${s.summary.cases}</small></div><p>财务数值、时点、版本、证据同时正确；项目自建题集。</p></article><article class="overview-card"><div class="label">V3 自然语言意图 · 首评</div><div class="number">${n.summary.strict_passed}<small> / ${n.summary.cases}</small></div><p>只评指标、财年、运算及范围；与数值轨道分开。</p></article><article class="overview-card"><div class="label">结构化数值 · 首評</div><div class="number">${s.summary.numeric_correct}<small> / ${s.summary.numeric_total}</small></div><p>缺失数值计错，不用“已回答的题”缩小分母。</p></article></div><section class="panel" style="margin-bottom:22px"><div class="panel-header"><h3>首评与修复后回归</h3><span class="pill">原始成绩保留</span></div><div style="height:18px"></div><div class="table-scroll"><table class="financial-table"><thead><tr><th>轨道</th><th>首次全题</th><th>首次保留集</th><th>修复后同题回归</th></tr></thead><tbody><tr><td>结构化财务 / PIT</td><td>${s.summary.strict_passed} / ${s.summary.cases}</td><td>${s.by_split.heldout.strict_passed} / ${s.by_split.heldout.cases}</td><td>${r?r.summary.strict_passed+' / '+r.summary.cases:'尚未运行'}</td></tr><tr><td>自然语言意图</td><td>${n.summary.strict_passed} / ${n.summary.cases}</td><td>${n.by_split.heldout.strict_passed} / ${n.by_split.heldout.cases}</td><td>${nr?nr.summary.strict_passed+' / '+nr.summary.cases:'尚未运行'}</td></tr></tbody></table></div><div class="panel-body"><div class="findings-list" style="padding:0">${[...s.failures,...n.failures].map(f=>`<div class="finding risk"><span class="finding-mark">${icon('clock')}</span><div><h4>${esc(f.id)} · 首次失败</h4><p>${esc(f.description)}</p></div></div>`).join('')}</div><div class="source-links" style="margin-top:19px"><a class="button secondary" href="${esc(q.links.methods)}" target="_blank" rel="noopener">研究方法与评测协议 ${icon('external')}</a><a class="button secondary" href="${esc(q.links.briefing)}" target="_blank" rel="noopener">面试 / 教师演示指南 ${icon('external')}</a></div><details class="calculation"><summary>查看首评证据、代码版本和分轨结果</summary><p><a href="${esc(s.receipt_url)}" target="_blank" rel="noopener">结构化首评原始记录 ↗</a>　<a href="${esc(n.receipt_url)}" target="_blank" rel="noopener">自然语言首评原始记录 ↗</a></p><p>${esc(q.interpretation.join(' '))}</p></details></div></section><details class="historical-quality"><summary>历史版本 V2 · 留存对照成绩</summary>`;
}
function render(){
  const [title,eyebrow,description]=VIEWS[state.view];$('#breadcrumb-title').textContent=title;$('#page-eyebrow').textContent=eyebrow;$('#page-title').innerHTML=title+'<span class="title-dot">.</span>';$('#page-description').textContent=description;
  $$('.nav-item').forEach(x=>{x.classList.toggle('active',x.dataset.view===state.view);if(x.dataset.view===state.view)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current');});
  $('#research-controls').hidden=['quality','pit','live'].includes(state.view);$('#peer-field').hidden=state.view!=='compare';$('.question-form').hidden=state.view==='evidence';$('.suggestion-row').hidden=state.view==='evidence';
  $('#save-workspace').disabled=state.view==='live';$('#export-toggle').disabled=!['research','compare','evidence'].includes(state.view);
  const content=({live:()=>liveResearch.render(),research:renderResearch,compare:renderCompare,evidence:renderEvidence,pit:renderPIT,quality:renderQuality})[state.view]();$('#view-content').innerHTML=content;$('#view-content').classList.remove('result-pulse');if(state.view!=='live'){void $('#view-content').offsetWidth;$('#view-content').classList.add('result-pulse');}updateURL();
}
function showEvidence(ids){
  const rows=ids.map(id=>state.cube.evidence[id]).filter(Boolean);if(!rows.length){toast('引用未在当前数据集中找到。');return;}
  $('#evidence-detail').innerHTML=rows.map((e,i)=>`${i?'<hr style="border:0;border-top:1px solid #e7ebef;margin:28px 0">':''}<div class="source-title">${esc(state.cube.metric_catalog[e.metric_id]?.label??e.metric_id)} <span class="pill neutral">${esc(e.ticker)} · ${esc(e.form)}</span></div><div class="source-value">${esc(formatValue(e.value,e.unit))}</div><dl class="detail-list">${[['财务期间',`${e.period_start||'时点'} → ${e.period_end}`],['申报日期',e.filed],['最早纳入日期',e.available_from],['申报编号',e.accession],['XBRL 标签',e.taxonomy_tag],['抓取时间',e.retrieved_at],['数值与单位',`${e.value} ${e.unit}`]].map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl><div class="source-block">事实指纹 SHA-256<br>${esc(e.fact_sha256)}<br><br>原始响应 SHA-256<br>${esc(e.upstream_response_sha256)}</div><details class="calculation"><summary>查看原始 XBRL 记录</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(e.raw_row,null,2))}</pre></details><div class="source-links" style="margin-top:20px"><a class="button primary" href="${esc(e.url)}" target="_blank" rel="noopener">打开 SEC 原始申报 ${icon('external')}</a><a class="button secondary" href="${esc(e.source_url)}" target="_blank" rel="noopener">CompanyFacts ${icon('external')}</a></div>`).join('')+'<p class="source-note">原始来源中的申报日只有日期粒度，因此从下一日纳入可用证据。不能由此推断当日盘中可见性。</p>';
  $('#evidence-dialog').showModal();
}
function exportData(type){
  const rows=state.view==='evidence'?visibleEvidence():state.answers;
  const data={product:'FinAgent Terminal V3',workspace:currentWorkspace(),dataset:{id:state.cube.dataset_id,captured_at:state.cube.captured_at,source_manifest_sha256:state.cube.source_manifest_sha256},policy:state.cube.policy,question_plan:state.plan,answers:rows,evidence:[...new Set(rows.flatMap(r=>r.evidence_ids??[r.id]))].map(id=>state.cube.evidence[id]).filter(Boolean)};
  let content,mime,extension;
  if(type==='json'){content=JSON.stringify(data,null,2);mime='application/json';extension='json';}
  else if(type==='csv'){const keys=['ticker','fiscal_year','metric_id','operation','status','value','unit','display_value','period_start','period_end','evidence_ids'];const field=x=>'"'+String(Array.isArray(x)?x.join(';'):x??'').replace(/"/g,'""')+'"';content='\uFEFF'+[keys.join(','),...rows.map(r=>keys.map(k=>field(r[k])).join(','))].join('\r\n');mime='text/csv;charset=utf-8';extension='csv';}
  else{content=`# ${state.ticker}${state.view==='compare'?' / '+state.peer:''} · FY${state.year} 财务研究\n\n问题：${state.question}\n\n证据截止：${state.asof} · 数据抓取：${state.cube.captured_at}\n\n| 指标 | 公司 | 数值 | 期间 | 来源 |\n|---|---|---|---|---|\n`+rows.map(r=>`| ${r.label??r.metric_id} | ${r.ticker} | ${r.status==='available'?r.display_value:r.reason??r.value} | ${r.period_start??'时点'} ~ ${r.period_end??''} | ${(r.evidence_ids??[r.id]).join(', ')} |`).join('\n')+'\n\n## 原始证据\n\n'+data.evidence.map(e=>`- [${e.id}](${e.url}) · ${e.taxonomy_tag} · filed ${e.filed} · available ${e.available_from}`).join('\n')+'\n\n## 边界\n\n'+state.cube.policy.source_limitation+'\n'+state.cube.policy.availability_note+'\n';mime='text/markdown;charset=utf-8';extension='md';}
  const a=document.createElement('a'),url=URL.createObjectURL(new Blob([content],{type:mime}));a.href=url;a.download=`finsearch-${state.ticker}-FY${state.year}-${state.asof}.${extension}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('#export-menu').hidden=true;$('#export-toggle').setAttribute('aria-expanded','false');toast('研究文件已导出，包含来源与时间边界。');
}
async function generateBrief(){
  const button=$('#generate-brief');button.disabled=true;button.textContent='正在核验并整理摘要…';state.busy=true;
  const requestWorkspace=currentWorkspace(),signature=workspaceKey(requestWorkspace),backend=state.backend,token=state.token;
  try{
    const headers={'Content-Type':'application/json'};if(token)headers.Authorization='Bearer '+token;
    const response=await fetch(backend+'/api/terminal/research',{method:'POST',headers,body:JSON.stringify({question:requestWorkspace.question,ticker:requestWorkspace.ticker,as_of:requestWorkspace.asof,fiscal_year:requestWorkspace.year,metric_ids:state.plan.metric_ids.slice(0,12),mode:'snapshot'})});
    if(!response.ok)throw new Error(`任务请求失败 (${response.status})`);const job=await response.json();
    for(let attempt=0;attempt<90;attempt++){await new Promise(resolve=>setTimeout(resolve,1000));const r=await fetch(backend+'/api/research/'+encodeURIComponent(job.id),{headers});if(!r.ok)throw new Error(`任务读取失败 (${r.status})`);const result=await r.json();if(result.status==='failed')throw new Error(typeof result.error==='string'?result.error:JSON.stringify(result.error??'摘要任务失败'));if(result.status==='completed'){if(signature===workspaceKey(currentWorkspace())){state.modelReport=result.result??result.report;const output=$('#model-output');if(output)output.innerHTML=modelReportHTML(state.modelReport);toast('真实模型调用已完成，年度事实仍保留引用。');}else{toast(`${requestWorkspace.ticker} FY${requestWorkspace.year} 摘要已完成；当前研究已切换，因此未替换当前内容。`);}return;}}
    throw new Error('任务尚未完成，请在后端工作台中检查运行状态。');
  }catch(error){const output=$('#model-output');if(output&&signature===workspaceKey(currentWorkspace()))output.innerHTML=`<p class="scope-note">${esc(error.message)}。<a href="../workbench/">打开后端工作台检查连接或访问令牌 ↗</a></p>`;else toast('原研究摘要任务失败：'+error.message);}
  finally{state.busy=false;button.disabled=false;button.textContent='DeepSeek 整理年度摘要';const current=$('#generate-brief');if(current&&current!==button){current.disabled=!state.config?.modes?.snapshot?.available;current.textContent='DeepSeek 整理年度摘要';}}
}
async function fetchBackendConfig(base,token,onProgress=()=>{},isCurrent=()=>true){
  const deadline=Date.now()+90000,headers={};if(token)headers.Authorization='Bearer '+token;let lastError=new Error('服务尚未就绪');
  for(let attempt=1;Date.now()<deadline&&isCurrent();attempt++){
    onProgress(`正在唤醒研究服务 · 第 ${attempt} 次连接`);
    try{const response=await fetch(base+'/api/config',{headers,signal:AbortSignal.timeout(Math.max(1,Math.min(15000,deadline-Date.now())))});
      if(response.ok){const config=await response.json();if(!config.features?.terminal&&!config.features?.live_research){const error=new Error('该后端尚未启用研究终端，请更新部署');error.permanent=true;throw error;}return config;}
      const error=new Error(`服务连接失败 (HTTP ${response.status})`);error.permanent=![408,425,429,500,502,503,504].includes(response.status);throw error;
    }catch(error){lastError=error;if(error.permanent)throw error;}
    const remaining=deadline-Date.now();if(remaining<=0)break;await new Promise(resolve=>setTimeout(resolve,Math.min(1200*2**Math.min(attempt-1,3),8000,remaining)));
  }
  throw new Error('研究服务暂未就绪，可点击“唤醒并重连”继续连接。'+(lastError.message.includes('HTTP')?' '+lastError.message:''));
}
async function reconnectBackend(){
  if(!state.backend)return;const base=state.backend,token=state.token,epoch=(state.connectionEpoch||0)+1;state.connectionEpoch=epoch;state.connectionChecking=true;state.config=null;liveResearch.connectionChanged();
  try{const config=await fetchBackendConfig(base,token,message=>{if(state.connectionEpoch===epoch){state.connectionProgress=message;liveResearch.connectionChanged();}},()=>state.connectionEpoch===epoch);
    if(state.connectionEpoch===epoch){state.config=config;liveResearch.state.connectionError='';}
  }catch(error){if(state.connectionEpoch===epoch)liveResearch.state.connectionError=error.message;}
  finally{if(state.connectionEpoch===epoch){state.connectionChecking=false;state.connectionProgress='';if(['research','live'].includes(state.view))render();}}
}
async function connectBackend(event){
  event?.preventDefault();const button=$('#connect-backend');button.disabled=true;$('#connection-message').textContent='正在检查后端…';
  try{
    const url=new URL($('#backend-url').value.trim()||location.origin);if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new Error('请输入不含用户名和密码的 HTTP(S) 地址');
    const base=url.href.replace(/\/$/,''),token=$('#backend-token').value.trim(),headers={};if(token)headers.Authorization='Bearer '+token;
    const config=await fetchBackendConfig(base,token,message=>$('#connection-message').textContent=message+'；首次唤醒最多约 90 秒。');
    state.connectionEpoch=(state.connectionEpoch||0)+1;state.connectionChecking=false;state.connectionProgress='';
    state.backend=base;state.token=token;state.config=config;try{sessionStorage.setItem('finagent.terminal.backend',base);sessionStorage.setItem('finagent.terminal.token',token);}catch{}
    $('#connection-dialog').close();liveResearch.connectionChanged();render();toast(config.modes?.live_research?.available?'研究后端已连接，可开始联网研究。':'研究后端已连接；可使用已开启的研究功能。');
  }catch(error){$('#connection-message').textContent=error.message+'。远程部署需允许当前网站跨域访问，HTTPS 页面通常需要 HTTPS 后端。';}
  finally{button.disabled=false;}
}
async function loadJSON(url,required=true){const response=await fetch(url);if(!response.ok){if(!required)return null;throw new Error(`数据请求失败 (${response.status})：${url}`);}return response.json();}
async function initialize(){
  $('#loading').hidden=false;$('#error-panel').hidden=true;$('#app-content').hidden=true;
  try{
    const [cube,quality,pit]=await Promise.all([loadJSON('../workbench/data/finance_cube.json'),loadJSON('../workbench/data/quality_summary.json',false),loadJSON('../workbench/data/pit_pilot.json',false)]);state.cube=cube;state.quality=quality;state.pit=pit;
    const params=new URLSearchParams(location.search);state.view=VIEWS[params.get('view')]?params.get('view'):(params.has('ticker')||params.has('year')||params.has('q')?'research':'live');state.ticker=cube.companies[params.get('ticker')]?params.get('ticker'):'MSFT';state.peer=cube.companies[params.get('peer')]?params.get('peer'):'AAPL';state.year=Number(params.get('year'))||cube.companies[state.ticker].fiscal_years.at(-1);state.asof=params.get('asof')||cube.captured_at.slice(0,10);state.question=params.get('q')||DEFAULT_QUESTION;
    state.saved=safeRead('finsearch-v3-saved').filter(w=>cube.companies[w.ticker]&&VIEWS[w.view]).slice(0,12);state.history=safeRead('finsearch-v3-history').filter(w=>cube.companies[w.ticker]&&VIEWS[w.view]).slice(0,6);
    const options=Object.entries(cube.companies).map(([ticker,c])=>`<option value="${ticker}">${ticker} · ${esc(c.name)}</option>`).join('');$('#company-select').innerHTML=options;$('#peer-select').innerHTML=options;$('#asof-input').max=cube.captured_at.slice(0,10);$('#asof-input').min='2018-01-01';
    selectControls();renderWorkspace();render();$('#data-status').textContent=`${cube.statistics.companies} 家公司 · ${cube.statistics.metrics} 类指标`;
    $('#loading').hidden=true;$('#app-content').hidden=false;
    try{state.backend=sessionStorage.getItem('finagent.terminal.backend')||'';state.token=sessionStorage.getItem('finagent.terminal.token')||'';}catch{}
    if(!state.backend)try{const runtime=await loadJSON('data/runtime.json',false);if(runtime?.use_same_origin===true&&['https:','http:'].includes(location.protocol))state.backend=location.origin;else if(runtime?.api_base_url){const url=new URL(runtime.api_base_url);if(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password)state.backend=url.href.replace(/\/$/,'');}}catch{}
    if(!state.backend&&['localhost','127.0.0.1'].includes(location.hostname)&&location.port!=='8092'&&location.port!=='8093')state.backend=location.origin;
    if(state.backend)reconnectBackend().then(()=>{if(params.get('live_run')&&state.view==='live'&&state.config)liveResearch.restore(params.get('live_run'));});
    // The independently generated first-evaluation summary is optional until the release run finishes.
    try{state.v3quality=await loadJSON('data/quality.json',false);state.gate=await loadJSON('data/pit_gate_summary.json',false);if(['quality','pit'].includes(state.view))render();}catch{}
  }catch(error){$('#loading').hidden=true;$('#error-panel').hidden=false;$('#error-detail').textContent=error.message;$('#data-status').textContent='数据暂不可用';}
}
document.addEventListener('click',event=>{
  const target=event.target.closest('button,a');if(!target)return;
  if(target.dataset.view){switchView(target.dataset.view);return;}
  if(target.dataset.prompt){state.question=target.dataset.prompt;$('#question-input').value=state.question;updateResearch();return;}
  if(target.dataset.chart){state.chart=target.dataset.chart;render();return;}
  if(target.dataset.evidence){showEvidence(target.dataset.evidence.split(','));return;}
  if(target.dataset.export){exportData(target.dataset.export);return;}
  if(target.dataset.openCompany){state.ticker=target.dataset.openCompany;state.view='research';selectControls();render();return;}
  if(target.dataset.restore){const [kind,index]=target.dataset.restore.split(':');const item=state[kind==='saved'?'saved':'history'][Number(index)];if(item){Object.assign(state,item);selectControls();render();closeSidebar();}return;}
  if(target.id==='generate-brief')generateBrief();
  if(target.dataset.connect!==undefined){$('#backend-url').value=state.backend||(['localhost','127.0.0.1'].includes(location.hostname)?location.origin:'');$('#backend-token').value=state.token;$('#connection-message').textContent=state.config?'当前已连接研究后端。':'';$('#connection-dialog').showModal();}
});
document.addEventListener('input',event=>{if(event.target.id==='evidence-search')$('#evidence-list').innerHTML=evidenceCards(event.target.value);});
document.addEventListener('change',event=>{
  if(event.target.id==='pit-case'){state.pitCase=event.target.value;render();return;}if(event.target.id==='pit-condition'){state.pitCondition=event.target.value;render();return;}
  if(['company-select','peer-select','year-select','asof-input'].includes(event.target.id)){
    if(event.target.id==='company-select'){state.ticker=event.target.value;state.year=state.cube.companies[state.ticker].fiscal_years.at(-1);selectControls();}
    updateResearch();
  }
});
$('#question-form').addEventListener('submit',event=>{event.preventDefault();updateResearch();});
$('#question-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();updateResearch();}});
$('#retry-load').addEventListener('click',initialize);
$('#connection-form').addEventListener('submit',connectBackend);
$('#close-connection').addEventListener('click',()=>$('#connection-dialog').close());
$('#disconnect-backend').addEventListener('click',()=>{state.connectionEpoch=(state.connectionEpoch||0)+1;state.connectionChecking=false;state.connectionProgress='';state.backend='';state.token='';state.config=null;state.modelReport=null;try{sessionStorage.removeItem('finagent.terminal.backend');sessionStorage.removeItem('finagent.terminal.token');}catch{}$('#connection-dialog').close();render();toast('已断开研究后端；财务数据研究仍可使用。');});
$('#menu-toggle').addEventListener('click',()=>{const open=$('#sidebar').classList.toggle('open');$('#sidebar-scrim').hidden=!open;$('#menu-toggle').setAttribute('aria-expanded',String(open));});
$('#sidebar-scrim').addEventListener('click',closeSidebar);
$('#close-evidence').addEventListener('click',()=>$('#evidence-dialog').close());
$('#evidence-dialog').addEventListener('click',event=>{if(event.target===$('#evidence-dialog')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right)event.target.close();}});
$('#save-workspace').addEventListener('click',()=>{const item=currentWorkspace(),key=workspaceKey(item),exists=state.saved.some(w=>workspaceKey(w)===key);state.saved=exists?state.saved.filter(w=>workspaceKey(w)!==key):[item,...state.saved].slice(0,12);persist();renderWorkspace();toast(exists?'已取消收藏。':'研究已收藏到此浏览器。');});
$('#export-toggle').addEventListener('click',()=>{const menu=$('#export-menu');menu.hidden=!menu.hidden;$('#export-toggle').setAttribute('aria-expanded',String(!menu.hidden));});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeSidebar();$('#export-menu').hidden=true;$('#export-toggle').setAttribute('aria-expanded','false');}});
const connect=document.createElement('button');connect.className='connect-toggle';connect.dataset.connect='';connect.innerHTML='连接研究后端 '+icon('external');$('.sidebar-bottom').prepend(connect);
initialize();

/** Browser selection mirrors the documented cube contract. Never uses a future version. */
export function selectState(cube,ticker,cutoff) {
  const company=cube.companies[ticker];
  if(!company)return {status:'unsupported',annuals:[],event:null,reason:'公司未收录。'};
  if(!/^\d{4}-\d{2}-\d{2}$/.test(cutoff)||!Number.isFinite(Date.parse(cutoff))||new Date(cutoff).toISOString().slice(0,10)!==cutoff)return {status:'unsupported',annuals:[],event:null,reason:'请选择有效的证据截止日期。'};
  if(cutoff>company.source.retrieved_at.slice(0,10))return {status:'unknown',annuals:[],event:null,reason:'截止日期超过数据抓取日，无法保证覆盖此后披露。'};
  const event=company.events.filter(x=>x.available_from<=cutoff).at(-1);
  if(!event)return {status:'unavailable_as_of',annuals:[],event:null,reason:'该截止日期尚无收录的可用年度申报。'};
  return {status:'available',annuals:event.annual_ids.map(id=>company.annuals[id]).sort((a,b)=>a.fiscal_year-b.fiscal_year),event,reason:null};
}
export function resolveMetric(cube,ticker,cutoff,year,metricId,operation='value') {
  const state=selectState(cube,ticker,cutoff);
  const annual=state.annuals.find(x=>x.fiscal_year===Number(year));
  const catalog=cube.metric_catalog[metricId];
  const base={ticker,metric_id:metricId,label:catalog?.label??metricId,fiscal_year:Number(year),operation,period_start:null,period_end:null,evidence_ids:[],value:null,unit:catalog?.unit??'',display_value:'暂无可用证据',formula:null};
  if(!catalog)return {...base,status:'unsupported',reason:'该指标尚未验证。'};
  if(state.status!=='available')return {...base,status:state.status,reason:state.reason};
  if(!annual){const known=Object.values(cube.companies[ticker].annuals).some(a=>a.fiscal_year===Number(year));const future=Number(year)>Number(cutoff.slice(0,4));return {...base,status:known||future?'unavailable_as_of':'unsupported',reason:known||future?'该财务年度在所选截止日期不可用；不会使用之后的申报补全。':'该财务年度尚未收录。'};}
  const value=operation==='value'?annual.metrics[metricId]:operation==='growth_amount'?annual.amount_changes?.[metricId]:annual.changes?.[metricId];
  if(!value||operation!=='value'&&value.status==='available'&&value.operation!==operation)return {...base,status:'unsupported',reason:'该指标的变化计算尚未提供，未进行未经验证的替代计算。'};
  const refs=(value.evidence_ids??[]).map(id=>cube.evidence[id]);
  if(value.status==='available'&&(!refs.length||refs.some(x=>!x||x.available_from>cutoff||x.ticker!==ticker)))return {...base,status:'conflict',reason:'证据校验未通过，已停止呈现该数值。'};
  return {...base,...value,period_start:catalog.kind==='duration'?annual.period_start:null,period_end:annual.period_end,annual_id:annual.id};
}

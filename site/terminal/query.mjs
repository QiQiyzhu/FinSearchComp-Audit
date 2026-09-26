/** Bounded, deterministic intent parsing. Values are always resolved from the audited cube. */
const PATTERNS = [
  ['free_cash_flow_margin', /自由现金流率|free\s+cash\s*flow\s+margin|fcf\s+margin/gi],
  ['free_cash_flow', /自由现金流|free\s+cash\s*flow|\bfcf\b/gi],
  ['operating_margin', /营业利润率|经营利润率|operating\s+(?:profit\s+)?margin/gi],
  ['gross_margin', /毛利率|gross\s+(?:profit\s+)?margin/gi],
  ['net_margin', /净利润率|净利率|net\s+(?:profit\s+|income\s+)?margin/gi],
  ['rd_ratio', /研发(?:费用|投入|支出)?(?:率|强度)|研发.{0,8}占.{0,5}(?:收入|营收)|r&d\s+(?:intensity|ratio)|r&d\s+(?:as\s+)?(?:a\s+)?percentage\s+of\s+(?:revenue|sales)|percentage\s+of.{0,40}(?:revenue|sales).{0,30}(?:r&d|research\s+and\s+development)/gi],
  ['cash_conversion', /现金(?:转换|转化)率|cash\s+conversion(?!\s+cycle)(?:\s+(?:ratio|rate))?|经营现金流.{0,8}(?:占|除以|\/).{0,4}净利润(?:.{0,4}比例)?|(?:operating\s+cash\s*flow|ocf)\s*(?:\/|as\s+a\s+(?:percentage|share)\s+of|divided\s+by)\s*net\s+income/gi],
  ['cash_to_assets', /现金(?:及现金等价物)?(?:占|对|\/).{0,3}(?:总)?资产|cash[-\s]+to[-\s]+assets/gi],
  ['current_ratio', /流动比率|current\s+ratio/gi],
  ['liabilities_to_assets', /资产负债率|负债率|debt\s+(?:to\s+assets?\s+)?ratio|liabilit(?:y|ies)[-\s]+to[-\s]+assets?/gi],
  ['capex_ratio', /资本(?:性)?支出(?:收入比|营收比|占比)|capex\s+(?:ratio|to\s+revenue)/gi],
  ['operating_cash_flow', /经营(?:活动)?(?:产生的)?现金流(?:量)?(?:净额)?|operating\s+cash\s*flow|cash\s*flow\s+from\s+operations|\bocf\b/gi],
  ['capital_expenditure', /资本(?:性)?支出|资本开支|\bcapex\b|capital\s+expenditure/gi],
  ['research_and_development', /研发(?:费用|投入|支出)?|r&d|research\s+and\s+development/gi],
  ['gross_profit', /毛利(?!率)|gross\s+profit(?!\s+margin)/gi],
  ['operating_income', /营业利润(?!率)|经营利润(?!率)|operating\s+(?:income|profit)(?!\s+margin)/gi],
  ['net_income', /净利润(?!率)|净收益|net\s+(?:income|profit)(?!\s+margin)/gi],
  ['current_assets', /流动资产|current\s+assets/gi],
  ['current_liabilities', /流动负债|current\s+liabilities/gi],
  ['assets', /总资产|资产总额|total\s+assets/gi],
  ['liabilities', /总负债|负债总额|total\s+liabilities/gi],
  ['stockholders_equity', /股东权益|净资产|stockholders.?\s+equity|shareholders.?\s+equity/gi],
  ['cash', /现金及(?:现金)?等价物|现金余额|cash\s+(?:and\s+)?(?:cash\s+)?equivalents/gi],
  ['cost_of_revenue', /营业成本|销售成本|cost\s+of\s+(?:revenue|sales|goods)/gi],
  ['revenue', /营业收入|营收|收入|净销售额|revenue|net\s+sales|\bsales\b/gi],
];
const RATIOS = new Set(['operating_margin','gross_margin','net_margin','rd_ratio','cash_conversion','liabilities_to_assets','capex_ratio','free_cash_flow_margin']);
const GAPS = [
  [/股价|目标价|市盈率|市净率|估值|市值|买入|卖出|stock\s+price|target\s+price|valuation|market\s+cap|\bp\/e\b|\bbuy\b|\bsell\b/i,'价格、估值或买卖建议需要本工作台尚未覆盖的市场数据。'],
  [/\bq[1-4]\b|季度|季报|quarter|月度|monthly|\bttm\b|滚动十二|过去十二个月|过去12个月/i,'当前证据为年度财报，暂不回答季度、月度或 TTM 指标。'],
  [/明年|未来|预测|forecast|next\s+year|predict/i,'历史财报不足以支持未来数值预测。'],
  [/最近几年|近几年|这些年|近年来|上一年|去年|前年|今年|recent\s+years|last\s+few\s+years|last\s+year|previous\s+year|this\s+year/i,'期间不明确；请指定财务年度。'],
  [/复合增长|\bcagr\b|\bebitda\b|现金转换周期|cash\s+conversion\s+cycle|\broe\b|\broa\b/i,'请求包含尚未验证的派生公式，请使用已支持指标。'],
  [/每股|\beps\b|股息|分红|dividend/i,'当前未纳入每股收益或股息数据。'],
  [/(?:投资|筹资|融资)(?:活动)?现金流|(?:investing|financing)\s+cash\s*flow/i,'投资或筹资现金流尚未覆盖；不会用经营现金流替代。'],
  [/分部|销量|市场份额|segment|unit\s+sales|market\s+share/i,'当前只覆盖公司整体年度财务，暂不支持分部或经营数量。'],
];
function yearsIn(text) {
  return [...new Set([...text.replace(/\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b/g,'').matchAll(/(?<!\d)(?:FY\s*)?((?:19|20)\d{2})(?!\d)/gi)].map(m=>+m[1]))];
}
function operationFor(text,id) {
  if(/增长额|增加额|增量|变化额|增长金额|增加金额|多少(?:亿|万|美?元|美元)|how\s+many\s+(?:us\s+)?dollars?|dollar\s+(?:growth|change|increase)|absolute/i.test(text)) return 'growth_amount';
  if(RATIOS.has(id)&&/百分点|变化|变动|提高|下降|增加|减少|change|difference|percentage\s+points?/i.test(text)) return 'change_pp';
  if(/同比|增速|增长率|增长|增幅|下降|增加|减少|yoy|year.over.year|growth|increase|decrease|percent\s+change/i.test(text)) return 'growth_pct';
  return 'value';
}
export function parseQuestion(question,context={}) {
  const text=String(question??'').trim();
  const gaps=GAPS.filter(([pattern])=>pattern.test(text)).map(([,message])=>message);
  const years=yearsIn(text);
  const year=years.length?Math.max(...years):Number(context.year??context.fiscal_year);
  const matches=[];
  for(const [id,pattern] of PATTERNS){
    pattern.lastIndex=0;
    for(const m of text.matchAll(pattern)){
      if(!matches.some(x=>m.index<x.end&&m.index+m[0].length>x.start))matches.push({id,start:m.index,end:m.index+m[0].length});
    }
  }
  matches.sort((a,b)=>a.start-b.start);
  if(!matches.length&&/基本面|财务概览|业绩分析|fundamental|financial\s+overview/i.test(text)) for(const id of ['revenue','operating_margin','free_cash_flow'])matches.push({id,start:0,end:0});
  if(!matches.length&&!gaps.length)gaps.push('未识别到受支持指标；请明确营收、营业利润率、自由现金流等财务口径。');
  if(/(?<!营业)(?<!经营)(?<!净)(?<!毛)利润率/.test(text)&&!matches.some(m=>RATIOS.has(m.id)))gaps.push('利润率口径不明确，请指定营业利润率、净利润率或毛利率。');
  if(!Number.isInteger(year))gaps.push('请选择一个财务年度。');
  if(years.length>2)gaps.push('一次问题最多指定两个年度；完整历史可查看下方趋势和财务表。');
  const requests=[];
  for(const m of matches){
    const before=text.slice(0,m.start),after=text.slice(m.end);
    const last=Math.max(before.lastIndexOf('，'),before.lastIndexOf(','),before.lastIndexOf('；'),before.lastIndexOf(';'))+1;
    const stop=after.search(/[，,；;。?!！？]/);
    const end=stop<0?text.length:m.end+stop;
    const clause=text.slice(last,end);
    const localMatches=matches.filter(x=>x.start>=last&&x.end<=end);
    const separators=[...clause.matchAll(/、|和|与|及|\band\b/gi)].filter(s=>!localMatches.some(x=>last+s.index>x.start&&last+s.index<x.end));
    const left=Math.max(last,...separators.filter(s=>last+s.index<m.start).map(s=>last+s.index+s[0].length));
    const right=Math.min(end,...separators.filter(s=>last+s.index>=m.end).map(s=>last+s.index));
    let segment=text.slice(left,right);
    const trailing=localMatches.length?text.slice(Math.max(...localMatches.map(x=>x.end)),end):'';
    if(operationFor(segment,m.id)==='value'&&localMatches.length>1&&!localMatches.slice(0,-1).some(x=>operationFor(text.slice(x.end,Math.min(end,x.end+10)),x.id)!=='value'))segment+=' '+trailing;
    const operation=operationFor(segment,m.id);
    const localYears=yearsIn(clause);
    const fiscalYear=localYears.length?Math.max(...localYears):year;
    if(operation!=='value'&&years.length===2&&Math.abs(years[1]-years[0])!==1)gaps.push('变化只支持相邻财年的同比；请明确相邻年度。');
    const selectedYears=operation==='value'&&localYears.length===2?localYears:[fiscalYear];
    for(const y of selectedYears)if(!requests.some(r=>r.metric_id===m.id&&r.operation===operation&&r.fiscal_year===y))requests.push({metric_id:m.id,operation,fiscal_year:y});
  }
  return {metric_ids:[...new Set(requests.map(r=>r.metric_id))],fiscal_year:year,operation:requests[0]?.operation??'value',supported:gaps.length===0&&requests.length>0,gaps:[...new Set(gaps)],requests,periods:years.length?years:[year]};
}

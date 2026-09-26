const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
const BASE='http://127.0.0.1:8092/terminal/';
const API='http://127.0.0.1:8092';
const JOB='research_mock_live_001';
const config={features:{terminal:true,live_research:true},modes:{snapshot:{available:true},live_research:{available:true,requires_token:false}}};
const report={report_type:'live_research',title:'NVIDIA 增长与现金流研究',question:'分析 NVIDIA 最新财报的增长和主要风险。',ticker:'NVDA',company:'NVIDIA',as_of:'2026-09-26',generated_at:'2026-09-26T06:00:00Z',
  plan:{objective:'核对增长与财务支撑',research_questions:['增长由什么驱动？'],queries:['NVIDIA investor relations annual report'],metric_ids:['revenue'],fiscal_year:2024,forms:['10-K']},
  answer:'已核对本次读取的年度报告。[D01]',claims:[{id:'C01',type:'observation',text:'公司在本次读取的披露中说明了业务增长。[D01]',evidence_ids:['D01'],quotes:[{source_id:'D01',quote:'Revenue increased during the fiscal year.'}],verification:{structural:true,entailment:'supported'}},{id:'C02',type:'interpretation',text:'财务数值为增长分析提供依据。[F01]',evidence_ids:['F01'],verification:{structural:true,entailment:'supported'}}],
  sources:[{id:'D01',title:'NVIDIA Annual Report — captured test document',url:'https://www.sec.gov/Archives/mock.htm',published_at:'2024-02-21',available_from:'2024-02-22',retrieved_at:'2026-09-26T05:59:00Z',sha256:'test-document-sha',text:'Revenue increased during the fiscal year.',excerpt:'Revenue increased during the fiscal year.',source_type:'10-K',status:'read'}],
  financial_answers:[{id:'F01',metric_id:'revenue',label:'营业收入',status:'available',value:'60922000000',display_value:'$60.92B',unit:'USD',fiscal_year:2024,period_start:'2023-01-30',period_end:'2024-01-28',formula:'reported',evidence_ids:['X01']}],
  financial_evidence:[{id:'X01',ticker:'NVDA',metric_id:'revenue',taxonomy_tag:'Revenues',unit:'USD',value:'60922000000',period_start:'2023-01-30',period_end:'2024-01-28',filed:'2024-02-21',available_from:'2024-02-22',url:'https://www.sec.gov/Archives/mock.htm',raw_row:{val:60922000000,start:'2023-01-30',end:'2024-01-28',filed:'2024-02-21',form:'10-K'}}],
  model_receipts:[{stage:'plan',provider:'DeepSeek',response_model:'mock-deepseek-model',request_id:'mock-plan-receipt',usage:{total_tokens:600},latency_ms:500},{stage:'draft',provider:'DeepSeek',response_model:'mock-deepseek-model',request_id:'mock-draft-receipt',usage:{total_tokens:1000}},{stage:'verify',provider:'DeepSeek',response_model:'mock-deepseek-model',request_id:'mock-verify-receipt',usage:{total_tokens:700}}],
  trace:[{step:'plan',label:'问题规划',detail:'已形成检索计划',status:'completed',timestamp:'2026-09-26T05:59:01Z'},{step:'search',label:'资料搜索与读取',detail:'已读取 1 份公开文档',status:'completed',timestamp:'2026-09-26T05:59:04Z'},{step:'verify',label:'来源与时间核验',detail:'已逐项核验引用',status:'completed',timestamp:'2026-09-26T05:59:07Z'}],
  gaps:[{code:'NO_FORECAST',message:'未获得可以独立核验的未来盈利预测。'}],limitations:['结论基于本次实际读取的资料，不代表投资收益预测。'],verification:{accepted_claims:2,rejected_claims:1,total_claims:3},budget:{model_calls:3,max_model_calls:3,documents_read:1},data_mode:'live',searches:[{query:'NVIDIA annual report',provider:'test-provider',result_count:2,status:'completed'}]};

async function setup(page,{configured=true,protectedMode=false}={}){
  await page.route('**/terminal/data/runtime.json',route=>route.fulfill({json:{api_base_url:configured?API:''}}));
  await page.route('**/api/config',route=>route.fulfill({json:{...config,modes:{...config.modes,live_research:{available:true,requires_token:protectedMode}}}}));
}
async function completeJob(page,customReport=report){
  await page.route(`**/api/research/${JOB}`,route=>route.fulfill({json:{id:JOB,status:'completed',result:customReport}}));
  await page.route(`**/api/research/${JOB}/events`,route=>route.fulfill({json:{id:JOB,status:'completed',events:customReport.trace}}));
}

test('live entry is the default and an unconnected service never displays an invented result',async({page})=>{
  let posts=0;page.on('request',request=>{if(request.method()==='POST')posts++;});await setup(page,{configured:false});
  await page.goto(BASE);await expect(page.locator('#live-question-form')).toBeVisible();await expect(page.locator('#page-title')).toContainText('联网研究');
  await expect(page.locator('#live-connection-state')).toContainText('尚未连接');await expect(page.locator('#live-submit')).toBeDisabled();
  await expect(page.locator('#live-report')).toHaveCount(0);expect(posts).toBe(0);
  await page.locator('[data-view="research"]').click();await expect(page.locator('#research-controls')).toBeVisible();
  await page.locator('[data-view="live"]').click();await expect(page.locator('#live-question-form')).toBeVisible();
});

test('mocked live job shows actual events, source quotes, financial provenance, model receipts and exports',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));await setup(page);let payload,posts=0,polls=0;
  await page.route('**/api/live/research',async route=>{posts++;payload=route.request().postDataJSON();await route.fulfill({status:202,json:{id:JOB,status:'queued'}});});
  await page.route(`**/api/research/${JOB}`,route=>route.fulfill({json:++polls===1?{id:JOB,status:'running'}:{id:JOB,status:'completed',result:report}}));
  await page.route(`**/api/research/${JOB}/events`,route=>route.fulfill({json:{events:report.trace.slice(0,polls===1?1:3)}}));
  await page.route(`**/api/research/${JOB}/export?format=json`,route=>route.fulfill({contentType:'application/json',body:JSON.stringify(report)}));
  await page.goto(BASE);await expect(page.locator('#live-submit')).toBeEnabled();
  await page.locator('#live-question').fill(report.question);await page.locator('#live-asof').fill('2026-09-26');await page.locator('#live-submit').click();
  await expect(page.locator('.live-trace')).toContainText('问题规划');await expect(page.locator('#live-report')).toBeVisible();
  expect(posts).toBe(1);expect(payload).toEqual({question:report.question,ticker:'NVDA',as_of:'2026-09-26',mode:'live'});
  await expect(page.locator('#live-report')).toContainText('业务增长');await expect(page.locator('.live-financial')).toContainText('$60.92B');
  await page.screenshot({path:'build/live-ui-desktop-mock.png',fullPage:true});
  await page.locator('#live-report [data-live-source="D01"]').first().click();await expect(page.locator('#evidence-dialog')).toBeVisible();
  await expect(page.locator('#evidence-detail')).toContainText('Revenue increased');await expect(page.locator('#evidence-detail a')).toHaveAttribute('href','https://www.sec.gov/Archives/mock.htm');await page.keyboard.press('Escape');
  await page.locator('#live-report [data-live-source="F01"]').first().click();await expect(page.locator('#evidence-detail')).toContainText('$60.92B');
  await page.locator('#evidence-detail [data-live-source="X01"]').click();await expect(page.locator('#evidence-detail')).toContainText('60922000000');await page.keyboard.press('Escape');
  await page.locator('.live-receipts summary').click();await expect(page.locator('.live-receipts')).toContainText('mock-plan-receipt');await expect(page.locator('.live-receipts')).toContainText('mock-deepseek-model');
  await expect(page.locator('.live-boundaries')).toContainText('未来盈利预测');
  const downloadPromise=page.waitForEvent('download');await page.locator('[data-live-export="json"]').click();const download=await downloadPromise;
  expect(JSON.parse(fs.readFileSync(await download.path(),'utf8')).report_type).toBe('live_research');expect(errors).toEqual([]);
});

test('quota failure is explicit and does not fall back to a snapshot report',async({page})=>{
  await setup(page);let posts=0;await page.route('**/api/live/research',route=>{posts++;return route.fulfill({status:429,json:{detail:'此部署今日联网研究额度已用完。'}});});
  await page.goto(BASE);await expect(page.locator('#live-submit')).toBeEnabled();await page.locator('#live-submit').click();
  await expect(page.locator('.live-error')).toContainText('额度已用完');await expect(page.locator('#live-submit')).toBeEnabled();await expect(page.locator('#live-report')).toHaveCount(0);expect(posts).toBe(1);
});

test('protected service uses only the session access token and preserves snapshot connection controls',async({page})=>{
  await setup(page,{protectedMode:true});let authorization;
  await page.route('**/api/live/research',route=>{authorization=route.request().headers().authorization;return route.fulfill({status:202,json:{id:JOB,status:'queued'}});});await completeJob(page);
  await page.goto(BASE);await expect(page.locator('#live-submit')).toBeDisabled();await page.locator('[data-live-connect]').click();
  await page.locator('#backend-url').fill(API);await page.locator('#backend-token').fill('test-session-access-token');await page.locator('#connect-backend').click();
  await expect(page.locator('#live-submit')).toBeEnabled();await page.locator('#live-submit').click();await expect(page.locator('#live-report')).toBeVisible();
  expect(authorization).toBe('Bearer test-session-access-token');expect(await page.evaluate(()=>Object.values(localStorage).some(v=>v.includes('test-session-access-token')))).toBe(false);
});

test('a late live result stays in its workspace and a temporary read failure resumes without a second POST',async({page})=>{
  await setup(page);let posts=0,reads=0;
  await page.route('**/api/live/research',route=>{posts++;return route.fulfill({status:202,json:{id:JOB,status:'queued'}});});
  await page.route(`**/api/research/${JOB}`,async route=>{reads++;if(reads===1)return route.fulfill({status:503,json:{detail:'temporary read failure'}});await new Promise(resolve=>setTimeout(resolve,500));return route.fulfill({json:{id:JOB,status:'completed',result:report}});});
  await page.route(`**/api/research/${JOB}/events`,route=>route.fulfill({json:{events:report.trace}}));
  await page.goto(BASE);await expect(page.locator('#live-submit')).toBeEnabled();await page.locator('#live-submit').click();await expect(page.locator('[data-live-resume]')).toBeVisible();
  await page.locator('[data-live-resume]').click();await page.locator('[data-view="quality"]').click();await expect(page.locator('#page-title')).toContainText('质量与方法');
  await page.waitForTimeout(700);await expect(page.locator('#page-title')).toContainText('质量与方法');await page.locator('[data-view="live"]').click();await expect(page.locator('#live-report')).toBeVisible();expect(posts).toBe(1);
});

test('read-only job link restores a result without submitting and mobile sources remain safe',async({page})=>{
  await page.setViewportSize({width:390,height:844});await setup(page);let posts=0;page.on('request',r=>{if(r.method()==='POST')posts++;});
  const malicious=structuredClone(report);malicious.sources[0].title='<img src=x onerror="window.injected=true">';malicious.sources[0].url='javascript:alert(1)';await completeJob(page,malicious);
  await page.goto(BASE+`?view=live&live_run=${JOB}`);await expect(page.locator('#live-report')).toBeVisible();expect(posts).toBe(0);
  await expect(page.locator('.live-source-card')).toContainText('<img src=x');expect(await page.evaluate(()=>!!window.injected)).toBe(false);
  await page.locator('#live-report [data-live-source="D01"]').first().click();await expect(page.locator('#evidence-dialog')).toBeVisible();await expect(page.locator('#evidence-detail a')).toHaveCount(0);
  await page.keyboard.press('Escape');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'build/live-ui-mobile-mock.png',fullPage:true});
});

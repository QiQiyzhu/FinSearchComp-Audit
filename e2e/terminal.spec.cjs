const fs = require('node:fs');
const path = require('node:path');
const {test,expect} = require('@playwright/test');
const BASE='http://127.0.0.1:8092/terminal/';
const readCube=()=>JSON.parse(fs.readFileSync(path.join(__dirname,'../site/workbench/data/finance_cube.json'),'utf8'));
const ready=page=>expect(page.locator('#app-content')).toBeVisible();

test('terminal computes arbitrary supported snapshot research and respects a named issuer',async({page})=>{
  const errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url());});
  await page.goto(BASE+'?ticker=MSFT&year=2024&asof=2024-11-02');await ready(page);
  await expect(page.locator('[data-metric="revenue"] .answer-value')).toContainText('$245.12B');
  await page.locator('#question-input').fill('苹果 FY2024 总资产和现金余额是多少？');await page.locator('#run-research').click();
  await expect(page.locator('#company-select')).toHaveValue('AAPL');
  await expect(page.locator('#answer-grid .answer-card')).toHaveCount(2);
  await expect(page.locator('[data-metric="assets"] .answer-value')).toContainText('$364.98B');
  await expect(page.locator('[data-metric="cash"] .answer-value')).toContainText('$29.94B');
  await page.locator('#question-input').fill('FY2024 相比 FY2023 营业利润率变化了多少个百分点？');await page.locator('#run-research').click();
  await expect(page.locator('[data-operation="change_pp"]')).toHaveCount(1);
  await expect(page.locator('[data-operation="change_pp"] .answer-value')).toContainText('百分点');
  await expect(page).toHaveURL(/ticker=AAPL/);expect(errors).toEqual([]);expect(writes).toEqual([]);
});

test('historical cutoff never fills a future fiscal year and unsupported scope is explicit',async({page})=>{
  await page.goto(BASE+'?ticker=MSFT&year=2024&asof=2024-07-30');await ready(page);
  await expect(page.locator('#answer-grid .missing')).toHaveCount(3);
  await expect(page.locator('#answer-grid')).not.toContainText('$245.12B');
  await page.locator('#asof-input').fill('2024-07-31');await page.locator('#asof-input').dispatchEvent('change');
  await expect(page.locator('[data-metric="revenue"] .answer-value')).toContainText('$245.12B');
  await page.locator('#question-input').fill('2024 年第三季度营收和目标价是多少？');await page.locator('#run-research').click();
  await expect(page.locator('.gap-banner')).toContainText('季度');await expect(page.locator('#answer-grid .answer-card')).toHaveCount(0);
  await page.locator('#question-input').fill('上一年的营业收入是多少？');await page.locator('#run-research').click();
  await expect(page.locator('.gap-banner')).toContainText('期间不明确');
});

test('comparison preserves issuer periods and source IDs without invented differences',async({page})=>{
  await page.goto(BASE+'?view=compare&ticker=MSFT&peer=AAPL&year=2024&asof=2024-11-02');await ready(page);
  await expect(page.locator('.comparison-table')).toContainText('2023-07-01 → 2024-06-30');
  await expect(page.locator('.comparison-table')).toContainText('2023-10-01 → 2024-09-28');
  await expect(page.locator('.comparison-table')).toContainText('期间不同，仅并排呈现');
  await page.locator('.comparison-table tbody button').first().click();
  await expect(page.locator('#evidence-dialog')).toBeVisible();await expect(page.locator('#evidence-detail')).toContainText('RevenueFromContract');
  await page.keyboard.press('Escape');await expect(page.locator('#evidence-dialog')).not.toBeVisible();
});

test('evidence search opens raw row and original source; workspace and exports persist',async({page})=>{
  await page.goto(BASE+'?view=evidence&ticker=MSFT&year=2024&asof=2024-11-02');await ready(page);
  await page.locator('#evidence-search').fill('RevenueFromContract');await expect(page.locator('.evidence-card')).toHaveCount(1);
  await page.locator('.evidence-card').click();await expect(page.locator('#evidence-detail')).toContainText('2024-07-31');
  await expect(page.locator('#evidence-detail a').first()).toHaveAttribute('href',/^https:\/\/www.sec.gov\//);
  await page.keyboard.press('Escape');await page.locator('#save-workspace').click();await expect(page.locator('#saved-count')).toHaveText('1');
  await page.reload();await ready(page);await expect(page.locator('#saved-count')).toHaveText('1');
  await page.locator('#export-toggle').click();const downloadPromise=page.waitForEvent('download');await page.locator('[data-export="json"]').click();
  const download=await downloadPromise;const data=JSON.parse(fs.readFileSync(await download.path(),'utf8'));
  expect(data.workspace.ticker).toBe('MSFT');expect(data.evidence.length).toBeGreaterThan(0);expect(data.policy.availability).toBe('filed_date_plus_one_calendar_day');
});

test('quality distinguishes first evaluation, intent scores and exposed regression',async({page})=>{
  const q=JSON.parse(fs.readFileSync(path.join(__dirname,'../site/terminal/data/quality.json'),'utf8'));
  await page.goto(BASE+'?view=quality');await ready(page);await expect(page.locator('#v3-quality-cards')).toBeVisible();
  await expect(page.locator('#v3-quality-cards .number').nth(0)).toContainText(String(q.structured_first.summary.strict_passed));
  await expect(page.locator('#v3-quality-cards .number').nth(1)).toContainText(String(q.nlq_first.summary.strict_passed));
  await expect(page.locator('#view-content')).toContainText('修复后同题回归');await expect(page.locator('#view-content')).toContainText('首次失败');
  await expect(page.locator('.historical-quality')).not.toHaveAttribute('open','');
});

test('terminal PIT replay switches actual saved observations and never calls a model',async({page})=>{
  const writes=[];page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url());});
  await page.goto(BASE+'?view=pit');await ready(page);await expect(page.locator('#pit-question')).toContainText('Microsoft');
  await page.locator('#pit-condition').selectOption('unfiltered');await expect(page.locator('.pit-phase').first()).toContainText('模型选择拒答');
  await page.locator('#pit-case').selectOption('nvda_fy2024_operating_margin');await expect(page.locator('#pit-question')).toContainText('NVIDIA');
  await expect(page.locator('#pit-gate-result')).toContainText('13 / 18');await expect(page.locator('#pit-gate-result')).toContainText('0 / 18');
  await expect(page.locator('#pit-gate-result')).toContainText('18 / 18');await expect(page.locator('#pit-gate-result')).toContainText('12 / 18');
  await expect(page.locator('#view-content')).toContainText('不能测出真实训练截止日');expect(writes).toEqual([]);
});

test('negative financial trends use a negative axis and reverse-year requests abstain',async({page})=>{
  await page.goto(BASE+'?ticker=TSLA&year=2019&asof=2020-03-01');await ready(page);
  await page.locator('[data-chart="operating_margin"]').click();
  const bars=await page.locator('.trend-chart rect[data-value]').evaluateAll(nodes=>nodes.map(n=>({value:+n.dataset.value,y:+n.getAttribute('y'),zero:+n.dataset.zero})));
  expect(bars.some(b=>b.value<0)).toBe(true);expect(bars.filter(b=>b.value<0).every(b=>b.y>=b.zero)).toBe(true);
  await page.locator('#question-input').fill('FY2023 相比 FY2024 营收增长金额是多少？');await page.locator('#run-research').click();
  await expect(page.locator('.gap-banner')).toContainText('反向比较');await expect(page.locator('#answer-grid .answer-card')).toHaveCount(0);
  await page.goto(BASE+'?ticker=MSFT&year=2024&asof=2024-02-31');await ready(page);await expect(page.locator('#answer-grid .missing')).toHaveCount(3);
});

test('backend connection restores session config and a mocked late job cannot replace another company',async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const config={features:{terminal:true},modes:{snapshot:{available:true,requires_token:true}}};
  await page.route('**/api/config',route=>route.fulfill({json:config}));
  await page.route('**/api/terminal/research',route=>route.fulfill({status:202,json:{id:'mock-delayed-job',status:'pending'}}));
  await page.route('**/api/research/mock-delayed-job',route=>route.fulfill({json:{status:'completed',result:{brief:[{text:'OLD MSFT SUMMARY MUST NOT APPEAR',evidence_ids:[]}],model:{status:'completed',used:false}}}}));
  await page.goto(BASE+'?ticker=MSFT&year=2024&asof=2024-11-02');await ready(page);
  await page.locator('[data-connect]').click();await page.locator('#backend-url').fill('http://127.0.0.1:8092');await page.locator('#backend-token').fill('test-session-token');await page.locator('#connect-backend').click();
  await expect(page.locator('#connection-dialog')).not.toBeVisible();await expect(page.locator('#generate-brief')).toBeEnabled();
  await page.reload();await ready(page);await expect(page.locator('#generate-brief')).toBeEnabled();
  await page.locator('#generate-brief').click();await page.locator('#company-select').selectOption('AAPL');
  await expect(page.locator('#toast')).toContainText('当前研究已切换');await expect(page.locator('#view-content')).not.toContainText('OLD MSFT SUMMARY MUST NOT APPEAR');expect(errors).toEqual([]);
  expect(await page.evaluate(()=>localStorage.getItem('finagent.terminal.token'))).toBeNull();
});

test('all five terminal workflows and evidence drawer fit a 390px viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844});await page.goto(BASE+'?ticker=MSFT&year=2024&asof=2024-11-02');await ready(page);
  for(const view of ['research','compare','evidence','pit','quality']){
    await page.locator('#menu-toggle').click();await page.locator('.nav-item[data-view="'+view+'"]').click();
    await expect(page.locator('#sidebar')).not.toHaveClass(/open/);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    if(view==='evidence'){await page.locator('.evidence-card').first().click();await expect(page.locator('#evidence-dialog')).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await page.keyboard.press('Escape');}
  }
  await page.screenshot({path:'test-results/terminal-mobile-quality.png',fullPage:true});
});

test('missing cube shows a retriable failure instead of invented values',async({page})=>{
  let fail=true;await page.route('**/finance_cube.json',route=>fail?route.fulfill({status:503,body:'unavailable'}):route.continue());
  await page.goto(BASE);await expect(page.locator('#error-panel')).toBeVisible();await expect(page.locator('#app-content')).toBeHidden();
  fail=false;await page.locator('#retry-load').click();await ready(page);await expect(page.locator('#error-panel')).toBeHidden();
});

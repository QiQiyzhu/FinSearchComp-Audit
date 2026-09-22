const { test, expect } = require('@playwright/test');

test('public homepage exposes working product and research archive', async ({page}) => {
  await page.goto('http://127.0.0.1:8092');
  await expect(page.getByRole('heading', {level:1})).toContainText('有据可查');
  await page.getByRole('link', {name:'一键体验研究工作台'}).click();
  await expect(page.locator('#report-area')).toBeVisible();
  await expect(page.locator('#connection-label')).toContainText('历史');
});

test('static snapshot exposes sources, source details, trace and export', async ({page}) => {
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:8092/workbench/');
  await expect(page.locator('#report-area')).toBeVisible();
  await page.locator('#tab-evidence').click();
  await expect(page.locator('#evidence-table-body tr').first()).toBeVisible();
  await page.locator('#evidence-table-body button').first().click();
  await expect(page.locator('#source-dialog')).toBeVisible();
  await expect(page.locator('#source-dialog-content')).toContainText('SHA');
  await page.getByRole('button', {name:'关闭证据详情'}).click();
  await page.locator('#tab-trace').click();
  await expect(page.locator('#trace-timeline li').first()).toBeVisible();
  const downloadEvent=page.waitForEvent('download');
  await page.locator('#export-json').click();
  const download=await downloadEvent;
  expect(download.suggestedFilename()).toMatch(/FinAgent-MSFT.*json/);
  expect(errors).toEqual([]);
});

test('a saved snapshot does not silently answer an arbitrary question', async ({page}) => {
  await page.goto('http://127.0.0.1:8092/workbench/');
  await expect(page.locator('#report-area')).toBeVisible();
  await page.locator('#question').fill('明天的苹果股价是多少？');
  await page.locator('#research-button').click();
  await expect(page.locator('#error-banner')).toBeVisible();
});

test('same-origin serves the UI and persists a real asynchronous demo API job', async ({page, request}) => {
  await page.goto('http://127.0.0.1:8091');
  await expect(page.locator('#report-area')).toBeVisible();
  const response=await request.post('http://127.0.0.1:8091/api/research', {data:{question:'分析微软年度营收、盈利能力与现金流',ticker:'MSFT',as_of:'2024-11-01',mode:'demo'}});
  expect(response.ok()).toBeTruthy();
  const created=await response.json();
  let job;
  await expect.poll(async()=>{
    job=await (await request.get('http://127.0.0.1:8091/api/research/'+created.id)).json();
    return job.status;
  }).toBe('completed');
  expect(job.result.model.used).toBe(false);
  expect(job.result.metrics.find(m=>m.id==='free_cash_flow').value).toBe('74071000000');
  const exported=await request.get('http://127.0.0.1:8091/api/research/'+created.id+'/export?format=json');
  expect(exported.ok()).toBeTruthy();
  expect((await exported.json()).ticker).toBe('MSFT');
  await page.goto('http://127.0.0.1:8091/?run='+created.id);
  await expect(page.locator('#report-area')).toBeVisible();
  await expect(page.locator('#report-meta')).toContainText('2024-11-01');
});

test('390px mobile layout has no document overflow and keeps research usable', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('http://127.0.0.1:8092/workbench/');
  await expect(page.locator('#report-area')).toBeVisible();
  await expect(page.locator('#research-button')).toBeVisible();
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
  expect(overflow).toBeFalsy();
  await page.screenshot({path:'test-results/mobile-workbench.png',fullPage:true});
});


test('deep links restore the chosen case and never replace a missing run with a demo', async ({page}) => {
  await page.goto('http://127.0.0.1:8092/workbench/?example=nvda-growth');
  await expect(page.locator('#report-ticker')).toContainText('NVDA');
  await expect(page.locator('#verdict-badge')).toContainText('证据不足');
  await page.goto('http://127.0.0.1:8091/?run=research_missing');
  await expect(page.locator('#error-banner')).toBeVisible();
  await expect(page.locator('#report-area')).toBeHidden();
});

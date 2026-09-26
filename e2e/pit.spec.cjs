const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
const URL = 'http://127.0.0.1:8092/workbench/pit.html';
const readBundle = () => JSON.parse(fs.readFileSync(path.join(__dirname,'../site/workbench/data/pit_pilot.json'),'utf8'));

test('PIT page compares saved observations, dates and evidence without creating jobs', async ({ page }) => {
  const bundle = readBundle();
  const errors = [];
  const writes = [];
  page.on('pageerror',error => errors.push(error.message));
  page.on('request',request => { if (request.method() !== 'GET') writes.push(request.url()); });
  await page.goto(URL);
  await expect(page.locator('#pit-content')).toBeVisible();
  const apple = bundle.cases.find(c => c.ticker === 'AAPL');
  await page.locator('#issuer-filter').selectOption('AAPL');
  await page.locator('#case-filter').selectOption(apple.id);
  await expect(page.locator('#case-question')).toHaveText(apple.question);
  await expect(page.locator('[data-phase="pre"] time')).toHaveText(apple.pre_as_of);
  await expect(page.locator('[data-phase="post"] time')).toHaveText(apple.post_as_of);
  await expect(page.locator('[data-phase="pre"] .pit-pill')).toHaveText('目标申报不可用');
  await expect(page.locator('[data-phase="post"] .pit-pill')).toHaveText('目标申报可用');
  await page.locator('#condition-filter').selectOption('unfiltered');
  const record = bundle.runs.find(r => r.case_id === apple.id && r.phase === 'pre' && r.condition === 'unfiltered');
  for (const id of record.presented_future_evidence_ids) await expect(page.locator('[data-phase="pre"] .pit-run-facts')).toContainText(id);
  await page.locator('.pit-evidence-details > summary').click();
  await expect(page.locator('#pit-evidence')).toContainText(apple.filing_date);
  const evidence = bundle.evidence.find(e => apple.evidence_ids.includes(e.id));
  await expect(page.locator('#pit-evidence')).toContainText(evidence.period_end);
  await expect(page.locator('#pit-evidence')).toContainText(evidence.captured_at.slice(0,10));
  await expect(page.locator('#pit-evidence')).toContainText(evidence.upstream_sha256);
  expect(await page.locator('#pit-evidence a').first().getAttribute('href')).toMatch(/^https:\/\/www.sec.gov\//);
  await expect(page.locator('#score-body tr')).toHaveCount(3);
  await expect(page.locator('#score-body tr[data-condition="pit_filtered"]')).toContainText('不适用');
  await expect(page.locator('.pit-shared-input-note')).toContainText('相同输入');
  await page.locator('.pit-evidence-details > summary').click();
  await page.screenshot({path:'test-results/pit-desktop.png',fullPage:false});
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download-records').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('finagent-pit-pilot.json');
  const downloaded = JSON.parse(fs.readFileSync(await download.path(),'utf8'));
  expect(downloaded.dataset_sha256).toBe(bundle.dataset_sha256);
  expect(errors).toEqual([]);
  expect(writes).toEqual([]);
});

test('an unrun PIT plan shows no fabricated model scores or answers', async ({ page }) => {
  const bundle = readBundle();
  bundle.mode = 'offline_plan';
  delete bundle.trace_file;
  bundle.runs = bundle.runs.map(r => ({...r,status:'not_run',answer:null,score:null,usage:null,latency_ms:null,error:null}));
  bundle.summary = {
    planned_runs:bundle.runs.length,attempted_runs:0,completed_runs:0,error_runs:0,scored_runs:0,total_tokens:0,
    by_condition:['closed_book','unfiltered','pit_filtered'].map(condition => ({
      condition,planned_runs:bundle.runs.filter(r => r.condition === condition).length,
      scored_runs:0,decision_correct:0,pre_scored:0,pre_correct_abstention:0,
      post_scored:0,post_numeric_correct:0,accepted_future_evidence_runs:0
    }))
  };
  await page.route('**/data/pit_pilot.json',route => route.fulfill({json:bundle}));
  await page.goto(URL);
  await expect(page.locator('#pit-status-banner')).toContainText('模型尚未运行');
  await expect(page.locator('.pit-answer-value')).toHaveText(['等待真实运行','等待真实运行']);
  await expect(page.locator('#score-body')).toContainText('未运行 · 计划 12');
  await expect(page.locator('#score-body strong')).toHaveCount(0);
  await expect(page.locator('#trace-records')).toBeHidden();
});

test('PIT mobile controls work with keyboard and preserve the selected deep link', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  const bundle = readBundle();
  const nvidia = bundle.cases.filter(c => c.ticker === 'NVDA');
  await page.goto(URL + '?issuer=NVDA&case=' + encodeURIComponent(nvidia[0].id) + '&condition=closed_book');
  await expect(page.locator('#pit-content')).toBeVisible();
  await expect(page.locator('#issuer-filter')).toHaveValue('NVDA');
  await expect(page.locator('#condition-filter')).toHaveValue('closed_book');
  await page.locator('#next-case').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#case-filter')).toHaveValue(nvidia[1].id);
  await expect(page).toHaveURL(new RegExp('case=' + encodeURIComponent(nvidia[1].id)));
  await page.locator('.pit-evidence-details > summary').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#pit-evidence')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.locator('#condition-filter').focus();
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Enter');
  await expect(page.locator('#condition-filter')).toHaveValue('unfiltered');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({path:'test-results/pit-mobile-top.png',fullPage:false});
  await page.screenshot({path:'test-results/pit-mobile.png',fullPage:true});
});

test('a missing PIT bundle reports the fetch failure and retry restores the page', async ({ page }) => {
  let failure = true;
  await page.route('**/data/pit_pilot.json',route => failure ? route.fulfill({status:503,body:'unavailable'}) : route.continue());
  await page.goto(URL);
  await expect(page.locator('#pit-error')).toBeVisible();
  await expect(page.locator('#pit-error-detail')).toContainText('503');
  await expect(page.locator('#pit-content')).toBeHidden();
  failure = false;
  await page.locator('#pit-retry').click();
  await expect(page.locator('#pit-content')).toBeVisible();
  await expect(page.locator('#pit-error')).toBeHidden();
});

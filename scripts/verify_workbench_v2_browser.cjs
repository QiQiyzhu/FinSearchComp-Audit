// Explicit opt-in: one two-issuer DeepSeek job; subsequent checks are read-only.
const {chromium, expect} = require('@playwright/test');
const fs = require('node:fs/promises');
const path = require('node:path');
if (!process.argv.includes('--execute')) throw new Error('Requires --execute: one comparison job can call DeepSeek twice.');
const base = process.env.FINAGENT_BASE_URL || 'http://127.0.0.1:8100';
const output = process.env.FINAGENT_BROWSER_RECEIPT || 'build/workbench-v2-browser-receipt.json';

(async () => {
  try { await fs.access(output); throw new Error('Receipt already exists; select a new output.'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const receipt = {checked_at:new Date().toISOString(), source:'actual browser, HTTP service and DeepSeek; no mocks', submitted_jobs:0, checks:{}, limitations:'One authored comparison interaction, separate from the offline question-quality evaluation.'};
  const browser = await chromium.launch({channel:process.platform === 'win32' ? 'msedge' : undefined, headless:true});
  const context = await browser.newContext({viewport:{width:1512,height:1050},recordVideo:{dir:'build/v2-browser-recordings',size:{width:1512,height:1050}}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let failure;
  try {
    await page.goto(base + '/?example=msft-aapl-comparison');
    await expect(page.locator('#comparison-card')).toBeVisible();
    await expect(page.locator('#connection-label')).toContainText('已连接');
    await page.locator('#mode-snapshot').click();
    const pending = page.waitForResponse(response => response.url().endsWith('/api/research') && response.request().method() === 'POST');
    await page.locator('#research-button').click();
    const submission = await pending;
    receipt.submitted_jobs = 1;
    receipt.submission_status = submission.status();
    expect(submission.ok()).toBeTruthy();
    const identifier = (await submission.json()).id;
    receipt.job_id = identifier;
    let job;
    await expect.poll(async () => {
      job = await (await context.request.get(base + '/api/research/' + identifier)).json();
      return job.status;
    }, {timeout:190000, intervals:[1000,1500,2000]}).toMatch(/completed|failed/);
    receipt.job_status = job.status;
    receipt.report = job.result;
    receipt.error = job.error;
    expect(job.status).toBe('completed');
    const report = job.result;
    receipt.model = report.model;
    expect(report.report_type).toBe('comparison');
    expect(report.companies).toHaveLength(2);
    expect(report.companies.every(company => company.model.used && company.model.status === 'completed')).toBe(true);
    expect(report.evidence.every(item => item.published_at <= report.as_of)).toBe(true);
    expect(report.comparison.rows.every(row => row.difference === null)).toBe(true);
    expect(new Set(report.evidence.map(item => item.id)).size).toBe(report.evidence.length);
    await expect(page.locator('#model-label')).toContainText('deepseek', {timeout:15000});
    await expect(page.locator('#comparison-table-body')).toContainText('2024-06-30');
    await expect(page.locator('#comparison-table-body')).toContainText('2024-09-28');
    await page.screenshot({path:'docs/assets/workbench/workbench-v2-comparison.png',fullPage:true});
    await page.locator('#tab-evidence').click();
    await page.locator('#evidence-search').fill('AAPL-E');
    await expect(page.locator('#evidence-table-body tr').first()).toContainText('AAPL-E');
    await page.locator('#evidence-table-body button').first().click();
    await expect(page.locator('#source-dialog')).toBeVisible();
    await expect(page.locator('#source-dialog-content')).toContainText('SHA');
    await page.screenshot({path:'docs/assets/workbench/workbench-v2-evidence.png'});
    await page.getByRole('button',{name:'关闭证据详情'}).click();
    const event = page.waitForEvent('download');
    await page.locator('#export-md').click();
    const download = await event;
    const file = await download.path();
    const markdown = await fs.readFile(file, 'utf8');
    expect(markdown).toContain('AAPL');
    expect(markdown).toContain('MSFT');
    receipt.checks = {real_model_issuer_calls:2, cutoff:true, unique_citations:true, different_periods_no_difference:true, evidence_search:true, source_drawer:true, markdown_export:true};
    await page.goto(base + '/?run=' + identifier);
    await expect(page.locator('#comparison-card')).toBeVisible();
    receipt.checks.persisted_deep_link = true;
    expect(errors).toEqual([]);
    receipt.status = 'passed';
  } catch (error) {
    failure = error;
    receipt.status = 'failed';
    receipt.failure = error.message.slice(0,2500);
  } finally {
    receipt.page_errors = errors;
    await fs.mkdir(path.dirname(output),{recursive:true});
    await fs.writeFile(output,JSON.stringify(receipt,null,2));
    const video = page.video();
    await context.close();
    if (video) await video.saveAs('docs/assets/workbench/workbench-v2-demo.webm');
    await browser.close();
  }
  console.log(JSON.stringify({status:receipt.status, job_id:receipt.job_id, model:receipt.model, checks:receipt.checks, failure:receipt.failure}));
  if (failure) process.exitCode = 1;
})().catch(error => {console.error(error.message); process.exitCode = 1;});

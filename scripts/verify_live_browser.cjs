// Explicit opt-in: one real browser-submitted snapshot/DeepSeek job, then read-only inspection.
const {chromium, expect}=require('@playwright/test');
const fs=require('node:fs/promises');
const path=require('node:path');
if (!process.argv.includes('--execute')) throw new Error('Requires --execute; this submits one potentially billed DeepSeek job.');
const base=process.env.FINAGENT_BASE_URL || 'http://127.0.0.1:8090';
const output=process.env.FINAGENT_BROWSER_RECEIPT || 'build/live-browser-receipt.json';
(async()=>{
  try { await fs.access(output); throw new Error('Receipt already exists; select a new FINAGENT_BROWSER_RECEIPT.'); } catch(error) { if(error.code!=='ENOENT') throw error; }
  const browser=await chromium.launch({channel:process.platform==='win32'?'msedge':undefined,headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1040},recordVideo:{dir:'build/browser-recordings',size:{width:1440,height:1040}}});
  const page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let job;
  try {
    await page.goto(base);
    await expect(page.locator('#report-area')).toBeVisible();
    await page.locator('#mode-snapshot').click();
    const submitted=page.waitForResponse(response=>response.url().endsWith('/api/research')&&response.request().method()==='POST');
    await page.locator('#research-button').click();
    const submission=await submitted;
    expect(submission.ok()).toBeTruthy();
    const identifier=(await submission.json()).id;
    await expect(page.locator('#model-label')).toContainText('deepseek-flash',{timeout:100000});
    job=await (await context.request.get(base+'/api/research/'+identifier)).json();
    expect(job.result.model.used).toBe(true);
    await page.screenshot({path:'docs/assets/workbench/workbench-live.png',fullPage:true});
    await page.locator('#tab-evidence').click();
    await page.locator('#evidence-table-body button').first().click();
    await expect(page.locator('#source-dialog')).toBeVisible();
    await page.screenshot({path:'docs/assets/workbench/evidence.png'});
    await page.getByRole('button',{name:'关闭证据详情'}).click();
    await page.locator('#tab-trace').click();
    await page.screenshot({path:'docs/assets/workbench/trace.png',fullPage:true});
    const event=page.waitForEvent('download');await page.locator('#export-md').click();
    const download=await event;
    expect(download.suggestedFilename()).toMatch(/FinAgent-MSFT.*md/);
    await page.locator('#tab-overview').click();
    expect(errors).toEqual([]);
    await fs.mkdir(path.dirname(output),{recursive:true});
    await fs.writeFile(output,JSON.stringify({checked_at:new Date().toISOString(),base_url:base,source:'actual browser, actual HTTP backend and actual DeepSeek; no mocks',submitted_jobs:1,job_id:identifier,model:job.result.model,checks:{real_model:true,evidence_drawer:true,trace:true,markdown_export:true,page_errors:errors},limitations:'Single authored interaction; not an investment accuracy evaluation.'},null,2));
    console.log(JSON.stringify({job_id:identifier,model:job.result.model,status:'passed',page_errors:errors}));
  } finally {
    const video=page.video();await context.close();
    if(video){await fs.mkdir('docs/assets/workbench',{recursive:true});await video.saveAs('docs/assets/workbench/workbench-demo.webm');}
    await browser.close();
  }
})().catch(error=>{console.error(error.message);process.exitCode=1});

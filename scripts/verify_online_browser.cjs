/* Public UI acceptance. --submit explicitly spends one live research quota. */
const {chromium, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const args=process.argv.slice(2);
const option=(name,fallback)=>{const i=args.indexOf(name);return i<0?fallback:args[i+1];};
const url=option('--url','https://qiqiyzhu.github.io/FinSearchComp-Audit/terminal/');
const output=option('--output','build/verification/live-browser.json');
const submit=args.includes('--submit');

(async()=>{
  if(fs.existsSync(output))throw new Error('Refusing to overwrite a browser acceptance receipt');
  const browser=await chromium.launch({channel:process.env.CI?undefined:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1080},acceptDownloads:true});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let report=null,jobId=null,posts=0;
  page.on('response',async response=>{
    if(response.request().method()==='POST'&&response.url().endsWith('/api/live/research')){posts++;if(response.status()===202)jobId=(await response.json()).id;}
    if(/\/api\/research\/research_[a-z0-9]+$/.test(response.url())&&response.ok()){const body=await response.json();if(body.status==='completed')report=body.result;}
  });
  try{
    await page.goto(url,{waitUntil:'domcontentloaded'});
    await expect(page.locator('#live-submit')).toBeEnabled({timeout:130000});
    if(submit){
      await page.locator('#live-company').selectOption('AAPL');
      await page.locator('#live-question').fill('苹果最新可得年度的营业利润率、研发费用率和经营现金流如何？请核对财务指标，并结合披露解释业务风险。');
      await page.locator('#live-submit').click();
      await expect(page.locator('#live-report')).toBeVisible({timeout:360000});
      if(!report||report.report_type!=='live_research'||!report.sources.length||!report.claims.length)throw new Error('Incomplete live report');
      if(!['plan','draft','verify'].every(stage=>report.model_receipts.some(r=>r.stage===stage&&r.status==='completed')))throw new Error('Missing completed model stage');
      await page.locator('.live-source-card').first().click();
      await expect(page.locator('#evidence-dialog')).toBeVisible();
      await expect(page.locator('#evidence-detail a')).toHaveAttribute('href',/^https:\/\//);
      await page.keyboard.press('Escape');
      const downloadEvent=page.waitForEvent('download');await page.locator('[data-live-export="json"]').click();
      const exported=JSON.parse(fs.readFileSync(await (await downloadEvent).path(),'utf8'));
      if(exported.question!==report.question)throw new Error('Export mismatched');
    }
    fs.mkdirSync(path.dirname(output),{recursive:true});
    await page.screenshot({path:output.replace(/\.json$/,'.desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:output.replace(/\.json$/,'.mobile.png'),fullPage:true});
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    if(overflow||errors.length)throw new Error(JSON.stringify({overflow,errors}));
    const receipt={checked_at:new Date().toISOString(),url,submitted:submit,posts,job_id:jobId,claims:report?.claims.length,sources:report?.sources.length,financial_answers:report?.financial_answers.length,model_receipts:report?.model_receipts,errors,mobile_overflow:overflow,passed:true};
    fs.writeFileSync(output,JSON.stringify(receipt,null,2)+'\n');
    if(report)fs.writeFileSync(output.replace(/\.json$/,'.report.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(receipt,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

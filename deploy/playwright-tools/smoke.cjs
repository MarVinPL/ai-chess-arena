const {chromium, devices} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
 const browser = await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '/usr/bin/chromium', headless:true, chromiumSandbox:true});
 try {
  const contexts = [];
  for (const options of [{viewport:{width:1280,height:800}}, devices['Pixel 7']]) {
   const context = await browser.newContext(options);
   contexts.push(context);
   await context.route('http://e2e.test/', r => r.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><button onclick="this.textContent=\'OK\'">Test</button>'}));
   const page = await context.newPage();
   await page.goto('http://e2e.test/');
   await page.getByRole('button').click();
   assert.equal(await page.getByRole('button').textContent(),'OK');
   assert.equal(await page.evaluate(()=>innerWidth),page.viewportSize().width);
   assert((await page.screenshot()).length>100);
  }
  await contexts[0].pages()[0].evaluate(()=>localStorage.setItem('isolated','desktop'));
  assert.equal(await contexts[1].pages()[0].evaluate(()=>localStorage.getItem('isolated')),null);
  assert(await contexts[1].pages()[0].evaluate(()=>navigator.maxTouchPoints)>0);
  await contexts[1].pages()[0].setViewportSize({width:800,height:393});
  assert.equal(await contexts[1].pages()[0].evaluate(()=>innerWidth),800);
  console.log('Desktop/mobile, screenshots, isolation, orientation: PASS; Chromium '+browser.version());
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

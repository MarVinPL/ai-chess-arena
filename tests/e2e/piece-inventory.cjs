const {chromium,devices}=require('playwright');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||'/usr/bin/chromium',headless:true,chromiumSandbox:true});
 try{
  for(const [name,options] of [['mobile',devices['Pixel 7']],['desktop',{viewport:{width:1280,height:800}}]]){
   const ctx=await browser.newContext(options);
   try{
    let state='planned',posts=0,reject=false,fen='4k3/8/8/8/8/8/8/3QK3 w - - 0 1';
    const page=await ctx.newPage();
    page.on('dialog',d=>d.accept());
    await ctx.route('**/*',async r=>{
     const u=new URL(r.request().url());
     if(u.hostname!=='arena.test')return r.abort();
     if(u.pathname==='/')return r.fulfill({contentType:'text/html',body:readFileSync(path.join(root,'index.html'))});
     const asset={'/assets/arena.js':'web/js/arena.js','/assets/navigation.js':'web/js/navigation.js','/assets/panel-icons.js':'web/js/panel-icons.js','/assets/arena.css':'web/css/arena.css','/assets/board-redesign.css':'web/css/board-redesign.css'}[u.pathname];
     if(asset)return r.fulfill({contentType:asset.endsWith('.css')?'text/css':'application/javascript',body:readFileSync(path.join(root,asset))});
     let data=[];
     if(u.pathname==='/api/tournaments')data=[{id:7,name:'Test E2E',status:state,players:2,games_planned:2,completed_games:0}];
     else if(u.pathname.endsWith('/preflight'))data={ready:true,entrants:[],blocked:[]};
     else if(['/api/tournaments/start','/api/tournaments/stop'].includes(u.pathname)){
      posts++;
      if(reject)return r.fulfill({status:409,json:{error:'Test odmowy'}});
      data={status:u.pathname.endsWith('/stop')?'stop_requested':'starting'};
     }else if(u.pathname==='/api/live')data={stage:'idle',moves:[],events:[]};
     else if(u.pathname==='/api/state')data={fen,moves:[],models:[]};
     else if(u.pathname==='/api/storage')data={free_bytes:1000000000,managed_free_bytes:1000000000,managed_models:[]};
     else if(u.pathname==='/api/model-admission')data={};
     return r.fulfill({json:data});
    });
    await page.goto('http://arena.test/');
    const settings=page.locator('[data-view-link="settings"]'),live=page.locator('[data-view-link="live"]');
    const select=page.locator('#zero-piece-mode');
    const check=async(fn)=>page.waitForFunction(fn,null,{timeout:6000});
    await check(()=>document.querySelectorAll('#white-piece-types [data-piece]').length===2);
    await check(()=>document.querySelectorAll('#black-piece-types [data-piece]').length===1);
    await settings.click();
    assert.equal(await select.inputValue(),'hide');
    await select.selectOption('strike');
    await check(()=>document.querySelectorAll('#white-piece-types .piece-inventory-item--zero').length===4);
    await live.click();
    const zero=page.locator('#white-piece-types [data-piece="n"]');
    assert.equal(await zero.textContent(),'♘0');
    assert.equal(await zero.evaluate(e=>getComputedStyle(e).textDecorationLine),'line-through');
    assert.equal(await zero.evaluate(e=>getComputedStyle(e).color),'rgb(255, 155, 155)');
    assert.equal(await page.locator('#black-piece-types [data-piece="k"]').textContent(),'♚✓');
    await page.reload();
    await check(()=>document.querySelectorAll('#white-piece-types .piece-inventory-item--zero').length===4);
    await settings.click();
    assert.equal(await select.inputValue(),'strike');
    await select.selectOption('hide');
    await check(()=>document.querySelectorAll('#white-piece-types [data-piece]').length===2);
    await live.click();
    fen='8/8/8/8/8/8/8/8 w - - 0 1';
    await check(()=>document.querySelector('#white-piece-types').textContent==='Brak bierek'&&document.querySelector('#black-piece-types').textContent==='Brak bierek');
    await settings.click();await select.selectOption('strike');await live.click();
    await check(()=>document.querySelectorAll('#white-piece-types .piece-inventory-item--zero').length===6);
    assert.equal(await page.locator('#white-piece-types [data-piece="k"]').textContent(),'♔0');
    fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    await check(()=>document.querySelectorAll('#white-piece-types .piece-inventory-item--zero').length===0&&document.querySelector('#white-piece-types [data-piece="p"]').textContent==='♙8');
    assert.match(await page.locator('#white-piece-summary').textContent(),/wartość materialna: 39/);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    console.log(name+': hide/strike, persistence, both colors, missing kings, empty board, restored pieces, material, viewport PASS');
   }finally{await ctx.close();}
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

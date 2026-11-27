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
    let broken=false,state='planned',posts=0,reject=false,fen='4k3/8/8/8/8/8/8/3QK3 w - - 0 1';
    const page=await ctx.newPage();
    page.on('dialog',d=>d.accept());
    await ctx.route('**/*',async r=>{
     const u=new URL(r.request().url());
     if(u.hostname!=='arena.test')return r.abort();
     if(u.pathname==='/')return r.fulfill({contentType:'text/html',body:readFileSync(path.join(root,'index.html'))});
     const asset={'/assets/arena.js':'web/js/arena.js','/assets/navigation.js':'web/js/navigation.js','/assets/panel-icons.js':'web/js/panel-icons.js','/assets/arena.css':'web/css/arena.css','/assets/board-redesign.css':'web/css/board-redesign.css'}[u.pathname];
     if(asset)return r.fulfill({contentType:asset.endsWith('.css')?'text/css':'application/javascript',body:readFileSync(path.join(root,asset))});
     if(/^[/]assets[/]pieces[/](cburnett|merida|shapes|chessnut)[/][wb][KQRBNP][.]svg$/.test(u.pathname)){
      if(broken&&u.pathname.endsWith('/wQ.svg'))return r.fulfill({status:404,body:''});
      return r.fulfill({contentType:'image/svg+xml',body:readFileSync(path.join(root,'web',u.pathname.slice(8)))});
     }
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
    const select=page.locator('#piece-style');
    const check=async(fn,arg)=>page.waitForFunction(fn,arg,{timeout:6000});
    await settings.click();
    assert.equal(await select.inputValue(),'cburnett');
    for(const style of ['cburnett','merida','shapes','chessnut','unicode']){
     await select.selectOption(style);
     await check(style=>{
      const cells=[...document.querySelectorAll('#piece-style-preview .piece-style-cell')];
      return cells.length===12&&cells.every(c=>style==='unicode'?!c.querySelector('img'):c.querySelector('img')?.naturalWidth>0&&c.querySelector('img').src.includes('/'+style+'/'));
     },style);
     await live.click();
     await check(style=>{
      const cells=[...document.querySelectorAll('#board .sq[data-piece-color]:not([data-piece-color="empty"])')];
      return cells.length===3&&cells.every(c=>style==='unicode'?!c.querySelector('img'):c.querySelector('img')?.naturalWidth>0&&c.querySelector('img').src.includes('/'+style+'/'));
     },style);
     assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
     await settings.click();
    }
    await select.selectOption('merida');
    await page.locator('#zero-piece-mode').selectOption('strike');
    await page.reload();
    await settings.click();
    assert.equal(await select.inputValue(),'merida');
    assert.equal(await page.locator('#zero-piece-mode').inputValue(),'strike');
    await live.click();
    await check(()=>document.querySelectorAll('#white-piece-types .piece-inventory-item--zero img').length===4);
    broken=true;
    await settings.click();await select.selectOption('shapes');await live.click();
    await check(()=>[...document.querySelectorAll('#board img[src$="/wQ.svg"]')].some(i=>i.hidden&&!i.nextElementSibling.hidden));
    assert.equal(await page.locator('#board .piece-fallback:not([hidden])').first().textContent(),'♕');
    await page.setViewportSize({width:844,height:390});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    console.log(name+': five styles, 12 previews, persistence, zero pieces, fallback, landscape PASS');
   }finally{await ctx.close();}
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

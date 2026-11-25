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
    let state='planned',posts=0,reject=false;
    const page=await ctx.newPage();
    page.on('dialog',d=>d.accept());
    await ctx.route('**/*',async r=>{
     const u=new URL(r.request().url());
     if(u.hostname!=='arena.test')return r.abort();
     if(u.pathname==='/')return r.fulfill({contentType:'text/html',body:readFileSync(path.join(root,'index.html'))});
     const asset={'/assets/arena.js':'web/js/arena.js','/assets/navigation.js':'web/js/navigation.js','/assets/panel-icons.js':'web/js/panel-icons.js','/assets/arena.css':'web/css/arena.css'}[u.pathname];
     if(asset)return r.fulfill({contentType:asset.endsWith('.css')?'text/css':'application/javascript',body:readFileSync(path.join(root,asset))});
     let data=[];
     if(u.pathname==='/api/tournaments')data=[{id:7,name:'Test E2E',status:state,players:2,games_planned:2,completed_games:0}];
     else if(u.pathname.endsWith('/preflight'))data={ready:true,entrants:[],blocked:[]};
     else if(['/api/tournaments/start','/api/tournaments/stop'].includes(u.pathname)){
      posts++;
      if(reject)return r.fulfill({status:409,json:{error:'Test odmowy'}});
      data={status:u.pathname.endsWith('/stop')?'stop_requested':'starting'};
     }else if(u.pathname==='/api/live')data={stage:'idle',moves:[],events:[]};
     else if(u.pathname==='/api/state')data={moves:[],models:[]};
     else if(u.pathname==='/api/storage')data={free_bytes:1000000000,managed_free_bytes:1000000000,managed_models:[]};
     else if(u.pathname==='/api/model-admission')data={};
     return r.fulfill({json:data});
    });
    await page.goto('http://arena.test/');
    await page.locator('[data-view-link="tournaments"]').click();
    const card=page.locator('.tournament-card').first();
    const waitButton=async label=>{const b=card.getByRole('button',{name:label,exact:true});await b.waitFor({state:'visible',timeout:6000});return b;};
    await (await waitButton('Uruchom turniej')).click();
    assert(await (await waitButton('Uruchamianie…')).isDisabled());
    assert.equal(posts,1);
    state='running';
    await (await waitButton('Zatrzymaj turniej')).click();
    assert(await (await waitButton('Zatrzymywanie…')).isDisabled());
    assert.equal(posts,2);
    state='stopped';
    await waitButton('Wznów turniej');
    reject=true;
    await (await waitButton('Wznów turniej')).click();
    await waitButton('Wznów turniej');
    assert.equal(posts,3);
    reject=false;
    await (await waitButton('Wznów turniej')).click();
    assert(await (await waitButton('Wznawianie…')).isDisabled());
    await page.locator('[data-view-link="live"]').click();
    state='running';
    await page.locator('[data-view-link="tournaments"]').click();
    await waitButton('Zatrzymaj turniej');
    assert.equal(posts,4);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    console.log(name+': START/STOP/RESUME, error, tab switch, viewport PASS');
   }finally{await ctx.close();}
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

// Isolated local browser; no account session. Visual fixtures are synthetic.
const fs=require('fs'), path=require('path'), assert=require('node:assert/strict');
const qa=process.argv[2], addon=path.resolve(__dirname,'..');
const {chromium}=require(path.join(qa,'node_modules/playwright'));
let templates=['analytics.xml','executive.xml'].map(f=>fs.readFileSync(path.join(addon,'static/src',f),'utf8').replace(/<\?xml[^>]*>/,'').replace(/<templates[^>]*>/,'').replace('</templates>','')).join('');
templates=templates.replace('/hexagonos_analytics/static/description/icon.svg','data:image/svg+xml;base64,'+fs.readFileSync(path.join(addon,'static/description/icon.svg')).toString('base64'));
const source=['mixed.js','charts.js','analytics.js'].map(f=>fs.readFileSync(path.join(addon,'static/src',f),'utf8').replace(/^import .*;\n/gm,'').replace(/export (class|function) /g,'$1 ')).join('\n');
const html=`<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>html,body,#mount{height:100%;margin:0}*{box-sizing:border-box}button,input,select{font:inherit}h1,h2,h3,p{margin-top:0}</style><link rel="stylesheet" href="analytics.css"></head><body><main id="mount"></main>
<script src="node_modules/@odoo/owl/dist/owl.iife.js"></script><script src="node_modules/chart.js/dist/chart.umd.js"></script><script>
const fixtures=${fs.readFileSync(path.join(qa,'fixtures.json'),'utf8')};
const {Component,onWillStart,onWillUnmount,useState,useEffect,useRef,useExternalListener}=owl;
const useService=name=>services[name];const user={userId:100};const loadBundle=async()=>{};
const registry={category:()=>({add:(key,value)=>window.Dashboard=value})};
class Dialog extends Component{static props=['*'];static template=owl.xml\`<div><t t-slot="default"/></div>\`;}
const services={orm:{call:async(model,method,args)=>{
 if(method==='get_options') return structuredClone(fixtures.options);
 if(method==='get_filter_options') return fixtures.options[args[0]+'s'];
 if(window.useDof && args[1]?.currency_id === 0 && fixtures.consolidated[args[0]]) return structuredClone(fixtures.consolidated[args[0]]);
 return structuredClone(args[1]?.currency_id === 0 && fixtures.mixed[args[0]] ? fixtures.mixed[args[0]] : fixtures.dashboards[args[0]]);
}},action:{doAction:async action=>window.lastAction=action},dialog:{add:()=>{}},notification:{add:()=>{}}};
${source}
window.app=new owl.App(window.Dashboard,{templates:${JSON.stringify('<templates>'+templates+'</templates>')}});
window.app.mount(document.querySelector('#mount')).then(c=>window.component=c);
</script></body></html>`;
fs.writeFileSync(path.join(qa,'preview.html'),html);
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('file://'+path.join(qa,'preview.html'));await page.waitForSelector('.hmx_exec_metrics');
 async function navigate(label){
  const mobile=page.getByRole('combobox',{name:'Vista del negocio',exact:true});
  if(await mobile.isVisible()){await mobile.selectOption({label});return;}
  const button=page.getByRole('button',{name:label,exact:true,includeHidden:true});
  if(!await button.isVisible())await page.locator('.hmx_nav_group').filter({has:button}).locator('summary').click();
  await button.click();
 }
 await page.waitForFunction(()=>Object.keys(Chart.instances).length===8);
 assert.deepEqual(errors,[]);
 assert.equal(await page.locator('canvas').count(),8);
 assert.equal(await page.locator('select[name=currency_id]').inputValue(),'1');
 assert.ok((await page.locator('.hmx_hero').boundingBox()).height<230);
 await page.screenshot({path:path.join(qa,'desktop.png')});
 // Text color contrast, actual computed styles, including dropdown and hover states.
 async function contrast(){return page.evaluate(()=>{
  const color=s=>(s.match(/[\d.]+/g)||[]).map(Number);
  const lum=c=>c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
  const failures=[];
  for(const el of document.querySelectorAll('.hmx_analytics *')){
   if(!el.getClientRects().length || ![...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()))continue;
   let bg=null,ancestor=el;
   while(ancestor){const c=color(getComputedStyle(ancestor).backgroundColor);if(c.length===3||c[3]===1){bg=c;break;}ancestor=ancestor.parentElement;}
   const fg=color(getComputedStyle(el).color);if(!bg)bg=[255,255,255];
   const ratio=(Math.max(lum(fg),lum(bg))+.05)/(Math.min(lum(fg),lum(bg))+.05);
   if(ratio<4.5)failures.push({text:el.textContent.trim().slice(0,50),ratio,fg,bg});
  }return failures;
 });}
 assert.deepEqual(await contrast(),[]);
 await page.locator('.hmx_more > summary').click();
 assert.deepEqual(await contrast(),[]);
 await page.getByRole('button',{name:'Cerrar filtros',exact:true}).click();
 await page.locator('.hmx_period > summary').click();
 await page.getByRole('button',{name:'Este mes',exact:true}).hover();assert.deepEqual(await contrast(),[]);
 await page.screenshot({path:path.join(qa,'header-period.png')});
 await page.locator('input[name=date_from]').focus();await page.keyboard.press('Escape');
 assert.ok(await page.locator('.hmx_period > summary').evaluate(el=>el===document.activeElement));
 assert.equal(await page.locator('.hmx_hero details[open]').count(),0);
 await page.locator('.hmx_warehouses > summary').click();
 await page.getByRole('checkbox',{name:'Almacén 2',exact:true}).check();
 await page.getByRole('button',{name:'Aplicar cambios',exact:true}).click();await page.waitForSelector('.hmx_exec_metrics');
 assert.equal(await page.locator('.hmx_filter_chip').count(),1);
 assert.deepEqual(await contrast(),[]);
 await page.getByRole('button',{name:'Quitar Almacén 2',exact:true}).click();
 await page.waitForFunction(()=>window.component.state.filters.warehouse_ids.length===0 && document.querySelectorAll('.hmx_filter_chip').length===0);
 await page.waitForSelector('.hmx_exec_metrics');
 assert.equal(await page.locator('.hmx_filter_chip').count(),0);
 for(const text of ['Actualizar','Productos y familias']){await page.getByRole('button',{name:text,exact:true}).hover();assert.deepEqual(await contrast(),[]);}
 await navigate('Clientes');await page.waitForSelector('.hmx_heatmap');
 assert.deepEqual(await contrast(),[]);
 await page.locator('.hmx_heatmap').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(qa,'customers.png')});
 await navigate('Producción por almacén');await page.waitForFunction(()=>document.querySelectorAll('canvas').length===1);
 assert.deepEqual(errors,[]);assert.deepEqual(await contrast(),[]);
 // Every operational panel is rendered through Chart.js, including currency/UOM splits.
 for(const [tab,count] of [['Calidad',4],['Entregas',4],['Abastecimiento',6],['Inventario',5],['Evidencias',1],['Asistencia',3]]){
  await navigate(tab);
  await page.waitForFunction(n=>Object.keys(Chart.instances).length===n,count);
  assert.equal(await page.locator('.hmx_bars').count(),0);
  if(tab==='Abastecimiento'){
   assert.equal(await page.getByRole('heading',{name:'Compra confirmada por moneda',exact:true}).count(),1);
   assert.equal(await page.locator('.hmx_mixed_chart canvas').count(),2);
   assert.equal(await page.locator('.hmx_mixed_chart .hmx_chart_table').count(),1);
  }
  assert.deepEqual(await contrast(),[]);assert.deepEqual(errors,[]);
  const types=await page.evaluate(()=>Object.values(Chart.instances).map(c=>c.config.type));
  if(['Calidad','Evidencias','Asistencia'].includes(tab))assert.ok(types.includes('doughnut'));
  await page.locator('.hmx_panels').first().scrollIntoViewIfNeeded();
  if(['Calidad','Inventario','Asistencia','Entregas'].includes(tab))await page.screenshot({path:path.join(qa,`charts-${tab}.png`)});
  const chart=Object.values(await page.evaluate(()=>Object.values(Chart.instances).map(c=>({unit:c.config.options.scales.x?.title?.text,type:c.config.type}))));
  assert.ok(chart.length);
 }
 await navigate('Entregas');await page.waitForFunction(()=>Object.keys(Chart.instances).length===4);
 const tooltipCheck=await page.evaluate(()=>{
  const c=Object.values(Chart.instances).find(chart=>chart.canvas.closest('.hmx_panel').querySelector('h3').textContent==='Líneas vencidas por cliente');const point=c.getDatasetMeta(0).data[0];
  c.tooltip.setActiveElements([{datasetIndex:0,index:0}],{x:point.x,y:point.y});c.update();
  c.options.onClick(null,[{datasetIndex:0,index:0}]);
  return {labels:c.data.labels,title:c.tooltip.title,tooltipRight:c.tooltip.x+c.tooltip.width,width:c.width,action:window.lastAction};
 });
 assert.ok(tooltipCheck.labels[0][1].endsWith('…'));
 assert.ok(tooltipCheck.title.join('').includes('NOMBRE COMERCIAL'));
 assert.ok(tooltipCheck.tooltipRight<=tooltipCheck.width+1);
 assert.equal(tooltipCheck.action.res_model,'sale.order.line');
 await navigate('Resumen ejecutivo');await page.waitForSelector('.hmx_exec_metrics');
 await page.locator('select[name=currency_id]').selectOption('0');
 await page.waitForFunction(()=>document.querySelectorAll('.hmx_currency_metric').length===6);
 assert.equal(await page.locator('.hmx_exec_metrics article').count(),3);
 assert.equal(await page.locator('.hmx_heatmap').count(),1);
 assert.equal(await page.locator('.hmx_currency_block').count(),0);
 assert.equal(await page.getByRole('heading',{name:'Producción por almacén de fabricación',exact:true}).count(),1);
 assert.equal(await page.getByRole('heading',{name:'¿Cómo evoluciona la venta?',exact:true}).count(),1);
 assert.equal(await page.getByRole('heading',{name:'Seguimiento comercial',exact:true}).count(),1);
 assert.deepEqual(await contrast(),[]);assert.deepEqual(errors,[]);
 await page.locator('.hmx_analytics').evaluate(el=>el.scrollTop=0);await page.screenshot({path:path.join(qa,'mixed.png')});
 const trend=page.locator('.hmx_mixed_chart').filter({has:page.getByRole('heading',{name:'¿Cómo evoluciona la venta?',exact:true})});
 assert.equal(await trend.locator('canvas').count(),2);
 assert.equal(await trend.locator('.hmx_chart_table').count(),1);
 const mixedDrill=await page.evaluate(()=>{
  const c=Object.values(Chart.instances).find(chart=>chart.canvas.getAttribute('aria-label').startsWith('¿Cómo evoluciona la venta? · USD'));
  c.options.onClick(null,[{datasetIndex:0,index:0}]);
  return {unit:c.options.scales.y.title.text,action:window.lastAction};
 });
 assert.equal(mixedDrill.unit,'USD');
 assert.ok(mixedDrill.action.domain.some(t=>t[0]==='currency_id'&&t[2]===2));
 await trend.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(qa,'mixed-charts.png')});
 await page.locator('.hmx_heatmap').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(qa,'mixed-customers.png')});
 await page.evaluate(async()=>{window.useDof=true;await window.component.load('resumen');});
 await page.waitForSelector('.hmx_fx');
 await page.waitForFunction(()=>Object.keys(Chart.instances).length===8);
 assert.equal(await page.locator('.hmx_exec_metrics .hmx_value').first().textContent(),'16,350 MXN');
 assert.equal(await page.locator('.hmx_mixed_chart').count(),0);
 assert.equal(await page.locator('.hmx_heatmap').count(),1);
 assert.deepEqual(await contrast(),[]);
 await page.locator('.hmx_analytics').evaluate(el=>el.scrollTop=0);await page.screenshot({path:path.join(qa,'dof-consolidated.png')});
 await page.locator('.hmx_fx_detail > summary').click();
 assert.equal(await page.locator('.hmx_fx_detail tbody tr').count(),12);
 assert.ok((await page.locator('.hmx_fx a').first().getAttribute('href')).startsWith('https://www.dof.gob.mx/indicadores_detalle.php?'));
 assert.deepEqual(await contrast(),[]);
 await page.locator('.hmx_fx').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(qa,'dof-rates.png')});
 await page.locator('.hmx_fx_detail > summary').click();
 for(const width of [390,768]){
  await page.setViewportSize({width,height:900});
  await navigate('Resumen ejecutivo');await page.waitForSelector('.hmx_exec_metrics');
  assert.equal(await page.getByRole('combobox',{name:'Vista del negocio',exact:true}).inputValue(),'resumen');
  // Chart.js resizes canvases in ResizeObserver/animation frames after the viewport changes.
  await page.waitForFunction(()=>{const el=document.querySelector('.hmx_analytics');return el.scrollWidth<=el.clientWidth+1;},{},{timeout:5000});
  assert.ok(await page.locator('.hmx_analytics').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  assert.deepEqual(await contrast(),[]);
  await page.locator('.hmx_analytics').evaluate(el=>el.scrollTop=0);
  await page.screenshot({path:path.join(qa,`screen-${width}.png`)});
  await page.locator('.hmx_panels > .hmx_panel').first().scrollIntoViewIfNeeded();
  assert.ok(await page.locator('.hmx_analytics').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:path.join(qa,`mixed-charts-${width}.png`)});
  await page.locator('.hmx_analytics').evaluate(el=>el.scrollTop=0);
  for(const [menu,close] of [['period','Cerrar período'],['warehouses','Cerrar almacenes'],['more','Cerrar filtros']]){
   await page.locator(`.hmx_${menu} > summary`).click();
   const box=await page.locator(`.hmx_${menu} > .hmx_dropdown_body`).boundingBox();
   assert.ok(box.x>=0 && box.x+box.width<=width && box.y+box.height<=900);
   assert.deepEqual(await contrast(),[]);
   await page.getByRole('button',{name:close,exact:true}).click();
  }
  await navigate('Entregas');await page.waitForFunction(()=>Object.keys(Chart.instances).length===4);
  assert.equal(await page.getByRole('combobox',{name:'Vista del negocio',exact:true}).inputValue(),'entregas');
  assert.ok(await page.locator('.hmx_analytics').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.locator('.hmx_panels').first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(qa,`operational-${width}.png`)});
 }
 assert.deepEqual(errors,[]);await browser.close();
 console.log('PASS Chart.js 4.4.1 in every view; identifiers/short labels/full tooltips; chart drills; mixed currencies; desktop 1440, tablet 768, mobile 390; no page overflow; inspected contrasts >=4.5:1.');
})().catch(e=>{console.error(e);process.exitCode=1;process.exit(1)});

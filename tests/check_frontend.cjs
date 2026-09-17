// Component checks using OWL 2 and jsdom, with simulated ORM services.
// Usage: node check_frontend.cjs /path/to/owl.js /path/to/jsdom /path/to/fixtures.json
const fs=require('fs');
const path=require('path');
const [owlPath, jsdomPath, fixturePath]=process.argv.slice(2);
if (!owlPath || !jsdomPath || !fixturePath) throw new Error('Pass OWL, jsdom and fixture paths.');
const addon=path.resolve(__dirname, '..');
const assert=require('node:assert/strict');
const { JSDOM }=require(path.resolve(jsdomPath));
const w=new JSDOM('<!doctype html><html><body><main id="mount"></main></body></html>',{runScripts:'outside-only',pretendToBeVisual:true,url:'http://127.0.0.1:8768'}).window;
w.eval(fs.readFileSync(owlPath,'utf8'));
const fixtures=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
const templates=fs.readFileSync(path.join(addon,'static/src/analytics.xml'),'utf8');
let calls=[], actions=[], dialogs=[], notifications=[], deferred=[];
let mode='normal';
w.services={
 orm:{call:async(model,method,args)=>{
  calls.push({method,args});
  if(method==='get_options')return fixtures.options;
  if(method==='get_dashboard'){
   if(mode==='deferred')return new Promise(resolve=>deferred.push({args,resolve}));
   return fixtures.dashboards[args[0]];
  }
  const metric=Object.values(fixtures.dashboards).flatMap(d=>d.metrics).find(m=>m.key===args[0]);
  return {metric,rows:[{id:42,name:'HMP1/0042',date:'16/09/2026 12:00',state:'En proceso'}],model:'mrp.production',date_label:'Fecha límite',action:{type:'ir.actions.act_window'},total:1,detail_note:''};
 }},
 action:{doAction:async action=>{actions.push(action)}},
 dialog:{add:(cls,props)=>{
  const target=w.document.createElement('div');w.document.body.append(target);
  const app=new w.owl.App(cls,{templates,props:{...props,close:()=>{app.destroy();target.remove()}}});
  dialogs.push(app);app.mount(target);
 }},
 notification:{add:(...args)=>notifications.push(args)},
};
w.eval(`const { Component, onWillStart, onWillUnmount, useState }=owl;
const useService=(name)=>services[name];
const registry={category:()=>({add:(key,value)=>{window.Dashboard=value}})};
class Dialog extends Component { static props=['*']; static template=owl.xml\`<div role="dialog"><h2 t-esc="props.title"/><t t-slot="default"/><t t-slot="footer"/></div>\`; }
`+fs.readFileSync(path.join(addon,'static/src/analytics.js'),'utf8').replace(/^import .*;\n/gm,'').replace(/export class /g,'class '));
const tick=()=>new Promise(resolve=>setTimeout(resolve,35));
const clickText=(selector,text)=>{const el=[...w.document.querySelectorAll(selector)].find(x=>x.textContent.trim()===text);assert.ok(el,`Missing ${text}`);el.click()};
(async()=>{
 const app=new w.owl.App(w.Dashboard,{templates});
 const root=await app.mount(w.document.querySelector('#mount'));
 assert.equal(w.document.querySelectorAll('.hmx_metric').length,fixtures.dashboards.resumen.metrics.length);
 console.log('PASS dashboard mounts with real OWL templates');
 for(const tab of fixtures.options.tabs){
  clickText('.hmx_tabs button',tab.label);await tick();
  assert.equal(w.document.querySelectorAll('.hmx_metric').length,fixtures.dashboards[tab.key].metrics.length);
 }
 console.log('PASS all 9 tabs render their metric sets');
 clickText('.hmx_tabs button','Dirección');await tick();
 w.document.querySelector('.hmx_metric').click();await tick();
 assert.ok(w.document.querySelector('[role="dialog"]'));
 assert.match(w.document.body.textContent,/HMP1\/0042/);
 clickText('[role="dialog"] button','Abrir todos los registros');await tick();
 assert.equal(actions.length,1);
 console.log('PASS drill opens and record-list action dispatches');
 clickText('.hmx_presets button','30 días');await tick();
 assert.equal(calls.filter(x=>x.method==='get_dashboard').at(-1).args[1].date_from,'2026-08-18');
 const date=w.document.querySelector('input[type=date]');date.value='2026-08-01';date.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();
 assert.equal(w.document.querySelectorAll('.hmx_metric').length,0);
 assert.match(w.document.body.textContent,/Actualizar para aplicar/);
 console.log('PASS preset inclusive range and stale-filter display prevention');
 mode='deferred';
 const older=root.load('produccion'); const newer=root.load('calidad');
 deferred[1].resolve(fixtures.dashboards.calidad);await newer;await tick();
 deferred[0].resolve(fixtures.dashboards.produccion);await older;await tick();
 assert.equal(root.state.tab,'calidad');
 assert.ok(w.document.body.textContent.includes('Inspecciones registradas'));
 console.log('PASS late response cannot overwrite current tab');
 mode='normal';
 await root.load('inventario');await tick();
 assert.match(w.document.querySelector('.hmx_table').textContent,/Reserva/);
 await root.load('comercial');await tick();
 assert.match(w.document.body.textContent,/Información restringida/);
 assert.equal(notifications.length,0);
 console.log('PASS stock units and restricted-money panel');
 w.services.orm.call=async()=>{const error=new Error('Permiso revocado');error.data={name:'odoo.exceptions.AccessError'};throw error};
 await root.load();await tick();
 assert.equal(w.document.querySelectorAll('.hmx_analytics').length,0);
 console.log('PASS revocation removes the existing dashboard on next request');
 app.destroy();
 // A direct URL without permission must fail before any dashboard DOM mounts.
 w.services.orm.call=async()=>{throw new Error('No tiene acceso a Analytics Hexágonos.')};
 let deniedError = null;
 process.once('unhandledRejection', error => { deniedError = error; });
 const denied=new w.owl.App(w.Dashboard,{templates});
 await assert.rejects(denied.mount(w.document.querySelector('#mount')));
 await tick();
 assert.match(deniedError.cause.message, /No tiene acceso/);
 assert.equal(w.document.querySelectorAll('.hmx_analytics').length,0);
 console.log('PASS unauthorized action never renders even an empty dashboard');
 w.close();
})().catch(error=>{console.error(error);w.close();process.exitCode=1});

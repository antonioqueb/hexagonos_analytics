// Real OWL templates, mocked RPC. All fixture amounts are synthetic.
// node check_executive_frontend.cjs /tmp/hmx-qa
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const qa = process.argv[2];
const {JSDOM} = require(path.join(qa, 'node_modules/jsdom'));
const root = path.resolve(__dirname, '..');
const w = new JSDOM('<html><body><main id="mount"></main></body></html>', {runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost/'}).window;
w.eval(fs.readFileSync(path.join(qa,'node_modules/@odoo/owl/dist/owl.iife.js'),'utf8'));
const fixtures = JSON.parse(fs.readFileSync(path.join(qa,'fixtures.json'),'utf8'));
const templates = ['analytics.xml','executive.xml'].map(file => fs.readFileSync(path.join(root,'static/src',file),'utf8').replace(/<\?xml[^>]*>/,'').replace(/<templates[^>]*>/,'').replace('</templates>','')).join('');
const calls=[], actions=[], deferred=[];
let mode='normal';
w.services = {orm:{call:async(model,method,args) => {
 calls.push({method,args});
 if(method === 'get_options') return structuredClone(fixtures.options);
 if(method === 'get_filter_options') return fixtures.options[args[0]+'s'];
 if(mode === 'deferred') return new Promise(resolve=>deferred.push({args,resolve}));
 if(mode === 'denied') {const e=new Error('Revocado');e.data={name:'odoo.exceptions.AccessError'};throw e;}
 return structuredClone(fixtures.dashboards[args[0]]);
}}, action:{doAction:async a=>actions.push(a)}, dialog:{add:()=>{}}, notification:{add:()=>{}}};
w.Chart=class {static version='4.4.1';constructor(canvas, config){this.config=config;}destroy(){}};
w.eval(`const {Component,onWillStart,onWillUnmount,useState,useEffect,useRef}=owl;
const useService=name=>services[name]; const user={userId:12}; const loadBundle=async()=>{};
const registry={category:()=>({add:(k,v)=>window.Dashboard=v})};
class Dialog extends Component {static props=['*'];static template=owl.xml\`<div><t t-slot="default"/></div>\`;}
`+['charts.js','analytics.js'].map(f=>fs.readFileSync(path.join(root,'static/src',f),'utf8').replace(/^import .*;\n/gm,'').replace(/export (class|function) /g,'$1 ')).join('\n'));
const tick=()=>new Promise(r=>setTimeout(r,50));
(async()=>{
 const app=new w.owl.App(w.Dashboard,{templates:`<templates>${templates}</templates>`});
 const component=await app.mount(w.document.querySelector('#mount'));
 assert.equal(w.document.querySelectorAll('.hmx_exec_metrics article').length,3);
 assert.equal(w.document.querySelectorAll('header').length,1);
 assert.ok(!w.document.querySelector('select[name=company_id]'));
 assert.equal(w.document.querySelectorAll('canvas').length,8);
 console.log('PASS real OWL compiles and mounts executive templates, 8 charts, one header');
 w.document.querySelector('.hmx_value').click();await tick();
 assert.equal(actions[0].res_model,'sale.order.line');
 assert.ok(actions[0].domain.some(t=>t[0]==='company_id'&&t[2]===1));
 console.log('PASS monetary card opens its exact server domain');
 for(const tab of fixtures.options.tabs){await component.load(tab.key);await tick();assert.ok(!w.document.body.textContent.includes('NaN'));}
 console.log('PASS all executive views render without NaN or template errors');
 await component.load('clientes');await tick();
 assert.ok(w.document.body.textContent.includes('Reactivado'));
 assert.equal(w.document.querySelectorAll('.hmx_heatmap tbody tr').length,3);
 component.changeCustomerList('customerStatus','reactivated');await tick();
 assert.equal(component.customerRows.length,1);
 console.log('PASS heatmap and cohort filtering');
 component.toggleWarehouse(2,true);await tick();
 assert.equal(component.state.data,null);
 assert.ok(w.document.body.textContent.includes('Actualizar para aplicar'));
 await component.load('produccion');await tick();
 assert.deepEqual(Array.from(calls.at(-1).args[1].warehouse_ids),[2]);
 assert.ok(w.document.body.textContent.includes('Almacén 2'));
 console.log('PASS filters persist across views and stale values disappear before apply');
 mode='deferred';const old=component.load('clientes');const latest=component.load('productos');
 deferred[1].resolve(fixtures.dashboards.productos);await latest;await tick();
 deferred[0].resolve(fixtures.dashboards.clientes);await old;await tick();
 assert.equal(component.state.tab,'productos');
 console.log('PASS late RPC cannot overwrite the active view');
 mode='denied';await component.load();await tick();assert.equal(w.document.querySelectorAll('.hmx_analytics').length,0);
 console.log('PASS access revocation removes dashboard');
 app.destroy();w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});

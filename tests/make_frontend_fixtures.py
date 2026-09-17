"""Generate fictitious UI fixtures; never loaded by the installed addon."""
import sys
import runpy, json
from datetime import date
from pathlib import Path
m = runpy.run_path(str(Path(__file__).with_name('check_logic.py')))
service=m['analytics'].HexagonosAnalytics()
f=dict(company_id=1,date_from=date(2026,9,1),date_to=date(2026,9,16),today=date(2026,9,16))
specs=service._catalog(f)
options=dict(tabs=[dict(key=k,label=l) for k,l in m['analytics'].TABS], companies=[dict(id=1,name='Hexágonos · datos de prueba')],company_id=1,today='2026-09-16',date_from='2026-09-01')
fixtures={}
for tab,label in m['analytics'].TABS:
    metrics=[]
    for i,s in enumerate(specs.values()):
        if (tab=='resumen' and not s['summary']) or (tab!='resumen' and s['tab']!=tab):continue
        item={k:s[k] for k in ['key','label','definition','scope','tone','unit','tab']}
        item.update(available=True,value=[42,7,12,136,94.4,3,0][i%7],sample=None,numerator=None)
        if s['denominator'] is not None:item.update(value=94.4,sample=90,numerator=85)
        metrics.append(item)
    panels=[]
    for j,title in enumerate(['Carga actual de producción','Entregas vencidas por almacén','OP terminadas por mes','Retenciones abiertas por proceso']):
        panels.append(dict(key='panel'+str(j),label=title,scope='actual',available=True,unit='',note='Seleccione una fila para abrir sus registros.',max=24,rows=[dict(key=str(n),label=name,value=count,action=dict(type='ir.actions.act_window',res_model='mrp.production',domain=[['company_id','=',1]])) for n,(name,count) in enumerate(zip(['Confirmadas','En proceso','Por cerrar'],[24,14,4]))]))
    if tab=='inventario':
        panels=[dict(key='stock_units',label='Existencia y reserva por unidad',scope='actual',available=True,unit='',note='No se suman unidades distintas.',max=1,rows=[dict(key='1',label='kg',value=14328.6,reserved=3480,free=10848.6,action={}),dict(key='2',label='Piezas',value=129040,reserved=24000,free=105040,action={})])]
    if tab=='comercial':
        panels.append(dict(key='restricted',label='Venta confirmada por moneda',scope='periodo',available=False,unit='moneda',rows=[],note='Importes restringidos por Control de Costos.'))
    fixtures[tab]=dict(metrics=metrics,panels=panels,company=options['companies'][0]['name'],updated_at='16/09/2026 12:00',period='2026-09-01 — 2026-09-16',today='2026-09-16')
Path(sys.argv[1]).write_text(json.dumps(dict(options=options,dashboards=fixtures),ensure_ascii=False))

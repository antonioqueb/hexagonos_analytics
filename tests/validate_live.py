"""Read-only live audit. Run in Odoo shell, after module upgrade, as an authorized viewer.

HMX_ANALYTICS_UID=42 HMX_DATE_FROM=2026-08-01 HMX_DATE_TO=2026-08-31 \
  odoo-bin shell -c /path/odoo.conf -d DATABASE --no-http < tests/validate_live.py

Uses existing data only; never creates test documents or changes permissions.
"""
import json
import os
from time import perf_counter

from odoo import fields

service = env['hexagonos.analytics']  # noqa: F821 -- Odoo shell environment
if os.environ.get('HMX_ANALYTICS_UID'):
    service = service.with_user(int(os.environ['HMX_ANALYTICS_UID']))
options = service.get_options()
filters = dict(date_from=os.environ.get('HMX_DATE_FROM', options['date_from']),
               date_to=os.environ.get('HMX_DATE_TO', options['today']),
               currency_id=int(os.environ.get('HMX_CURRENCY_ID', options['currency_id'])))
if os.environ.get('HMX_WAREHOUSE_IDS'):
    filters['warehouse_ids'] = [int(v) for v in os.environ['HMX_WAREHOUSE_IDS'].split(',')]
scoped, parsed = service._scope(filters)
start = perf_counter()
data = service.get_dashboard('resumen', filters)
if not data.get('commercial_available'):
    raise AssertionError('El usuario necesita sus permisos existentes de venta y lectura de importes.')
checks = []
rounding = scoped.env['res.currency'].browse(parsed['currency_id']).rounding

def same(name, actual, expected, tolerance=rounding):
    result = {'check': name, 'actual': actual, 'expected': expected, 'pass': abs(actual - expected) <= tolerance}
    checks.append(result)
    if not result['pass']:
        raise AssertionError(result)

sales = data['cards'][0]
for dimension in ['warehouse', 'customer', 'product', 'family']:
    rows = scoped._dimension(parsed, dimension)
    same('Ventas por ' + dimension, sum(r['current'] for r in rows), sales['current'])
    same('Comparación por ' + dimension, sum(r['previous'] for r in rows), sales['previous'])
    for row in rows:
        for key, action_key in [('current', 'action'), ('previous', 'previous_action')]:
            lines = scoped.env['sale.order.line'].search(row[action_key]['domain'])
            same('%s/%s/%s: documentos' % (dimension, row['id'], key), sum(lines.mapped('price_subtotal')), row[key])

for card in data['cards']:
    if not card['available']:
        checks.append({'check': card['label'], 'status': card['reason']})
        continue
    model = card['action']['res_model']
    records = scoped.env[model].search(card['action']['domain'])
    field, sign = {'sales': ('price_subtotal', 1), 'invoiced': ('amount_currency', -1), 'collected': ('amount', 1)}[card['key']]
    same(card['label'] + ': documentos únicos', sign * sum(records.mapped(field)), card['current'])
if data['cards'][1]['available']:
    same('Facturación por almacén con Sin asignar', sum(r['current'] for r in data['invoice_warehouses']), data['cards'][1]['current'])

delivery = scoped._catalog(parsed)['delivery_done']
try:
    groups = scoped._groups('stock.picking', delivery['domain'], ['hmx_dispatch_warehouse_id'], [])
    same('Despacho por almacén real incluyendo Sin asignar', sum(g['__count'] for g in groups),
         scoped.env['stock.picking'].search_count(delivery['domain']), 0)
except Exception as exc:
    from odoo.exceptions import AccessError
    if not isinstance(exc, AccessError):
        raise
    checks.append({'check': 'Despacho', 'status': 'Sin permiso de lectura'})

production = data['production']
if production['available']:
    for row in production['rows']:
        for key in ['current', 'previous']:
            action = row.get(key + '_action')
            amount = sum(scoped.env['stock.move'].search(action['domain']).mapped('quantity')) if action else 0
            same('Producción %s/%s' % (row['id'], key), amount, row[key], 1e-6)
    for series in production['chart_series']:
        same('Serie producción %s/%s' % (series['id'], series['unit_id']), sum(p['value'] for p in series['points']), series['current'], 1e-6)

# Compare cohort labels to actual unique order days from their exact history domains.
from odoo.addons.hexagonos_analytics.models.analytics_math import customer_status
for row in (data.get('customers') or {}).get('rows', []):
    records = scoped.env['sale.order'].search(row['history_action']['domain'])
    days = [fields.Datetime.context_timestamp(scoped, r.date_order).date() for r in records]
    expected = customer_status(days, parsed['date_from'], parsed['date_to'], parsed['inactivity_days'], parsed['cadence_factor'])
    if row['status'] != expected['status']:
        raise AssertionError('Clasificación distinta para cliente %s' % row['id'])
checks.append({'check': 'Clasificación por historial visible hasta el corte', 'pass': True})
print(json.dumps({'company': data['company'], 'user_id': scoped.env.uid, 'period': data['period'],
    'comparison': data['comparison_period'], 'currency': data['currency'], 'updated_at': data['updated_at'],
    'elapsed_seconds': round(perf_counter()-start, 2), 'checks': checks,
    'limitations': ['Una modificación concurrente puede requerir repetir la conciliación.',
                    'Validar por separado permisos y reglas con cada perfil autorizado.',
                    'Verificar la configuración histórica del tipo de fabricación frente al almacén ejecutor.']}, ensure_ascii=False, indent=2))

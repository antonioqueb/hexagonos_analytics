"""Executive analytics using ACL-aware ORM aggregates and the same domains for drills."""
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .analytics import TZ, utc_midnight
from .analytics_math import comparison, customer_status, delta, months_between, shift_month


EXEC_TABS = [('resumen', 'Resumen ejecutivo'), ('comercial', 'Desempeño comercial'),
             ('clientes', 'Clientes'), ('productos', 'Productos y familias'),
             ('produccion', 'Producción por almacén')]
SALE_PERMISSION = 'control_costos_hexagonos.group_view_sale_total'
STATUS = {'new': 'Nuevo en historial visible', 'recurring': 'Recurrente',
          'reactivated': 'Reactivado', 'lost': 'Sin recompra', 'no_purchase': 'Sin compra en el período'}
PALETTE = ['#1946BA', '#36751B', '#0F2385', '#9A4810', '#7847A8', '#006B73', '#A32C61']


class ExecutiveAnalytics(models.AbstractModel):
    _inherit = 'hexagonos.analytics'

    def _scope(self, filters):
        # Company belongs to the authenticated Odoo context, never a dashboard selector.
        if not isinstance(filters, dict):
            raise ValidationError(_('Los filtros no son válidos.'))
        if filters.get('company_id') and str(filters['company_id']) != str(self.env.company.id):
            raise AccessError(_('El tablero usa únicamente la compañía activa de la sesión.'))
        service, f = super()._scope(dict(filters, company_id=self.env.company.id))
        try:
            if not isinstance(filters.get('warehouse_ids', []), (list, tuple)):
                raise ValueError()
            f['warehouse_ids'] = sorted(set(int(v) for v in filters.get('warehouse_ids', [])))
            if any(v < 0 for v in f['warehouse_ids']):
                raise ValueError()
            for name in ('customer_id', 'product_id', 'family_id', 'seller_id'):
                f[name] = int(filters.get(name) or 0)
                if f[name] < 0:
                    raise ValueError()
            f['currency_id'] = int(filters.get('currency_id') or service.env.company.currency_id.id)
            f['inactivity_days'] = int(filters.get('inactivity_days') or 90)
            f['cadence_factor'] = float(filters.get('cadence_factor') or 2)
            if not 7 <= f['inactivity_days'] <= 730 or not 1 <= f['cadence_factor'] <= 6:
                raise ValueError()
            f['comparison'] = filters.get('comparison') or 'previous_period'
            if f['comparison'] not in ('previous_period', 'previous_month', 'previous_year'):
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise ValidationError(_('Revise filtros, inactividad (7–730 días) y frecuencia (1–6).'))
        if not service.env['res.currency'].search_count([('id', '=', f['currency_id'])]):
            raise ValidationError(_('Moneda no disponible.'))
        selected = [v for v in f['warehouse_ids'] if v]
        if selected and service.env['stock.warehouse'].with_context(active_test=False).search_count([
                ('company_id', '=', f['company_id']), ('id', 'in', selected)]) != len(selected):
            raise AccessError(_('Algún almacén seleccionado no está disponible en esta compañía.'))
        f['previous_from'], f['previous_to'] = comparison(f['date_from'], f['date_to'], f['comparison'])
        return service.with_context(hmx_analytics_filters=f), f

    @api.model
    def get_filter_options(self, kind, query='', selected=0):
        self._check_access()
        sources = {'customer': ('res.partner', [('parent_id', '=', False)]),
                   'product': ('product.product', [('sale_ok', '=', True)]),
                   'family': ('product.category', []),
                   'seller': ('res.users', [('share', '=', False)])}
        if kind not in sources:
            raise ValidationError(_('Catálogo desconocido.'))
        model, domain = sources[kind]
        domain = list(domain)
        if query:
            domain += [('display_name', 'ilike', str(query)[:100])]
        try:
            source = self.env[model].with_context(allowed_company_ids=[self.env.company.id], active_test=False)
            # Some installed product extensions pass web-only kwargs through
            # search_read. Use the regular ORM search/read path, retaining the
            # supplier search override, record rules and field access checks.
            rows = source.search(domain, limit=50, order='id').read(['display_name'])
            if selected and not any(row['id'] == int(selected) for row in rows):
                rows += source.search([('id', '=', int(selected))], limit=1).read(['display_name'])
            return [{'id': r['id'], 'name': r['display_name']} for r in rows]
        except AccessError:
            return []

    @api.model
    def get_options(self):
        options = super().get_options()
        options.pop('companies', None)
        options['tabs'] = [{'key': k, 'label': v} for k, v in EXEC_TABS] + [
            t for t in options['tabs'] if t['key'] not in dict(EXEC_TABS)]
        try:
            warehouses = self.env['stock.warehouse'].with_context(active_test=False).search_read(
                [('company_id', '=', self.env.company.id)], ['name', 'code'], order='name')
        except AccessError:
            warehouses = []
        options.update(warehouses=warehouses, currency_id=self.env.company.currency_id.id,
                       currencies=self.env['res.currency'].search_read([], ['name'], order='name'))
        options['date_from'] = str(shift_month(fields.Date.to_date(options['today']).replace(day=1), -5))
        for key in ('customer', 'product', 'family', 'seller'):
            options[key + 's'] = self.get_filter_options(key)
        return options

    def _period_domain(self, field, start, end, date_only=False):
        if date_only:
            return [(field, '>=', str(start)), (field, '<=', str(end))]
        return [(field, '>=', fields.Datetime.to_string(utc_midnight(start))),
                (field, '<', fields.Datetime.to_string(utc_midnight(end + timedelta(days=1))))]

    def _warehouse_domain(self, f, field):
        return [(field, 'in', [v or False for v in f['warehouse_ids']])] if f['warehouse_ids'] else []

    def _sale_domain(self, f, start=None, end=None):
        domain = [('company_id', '=', f['company_id']), ('state', '=', 'sale'),
                  ('display_type', '=', False), ('is_downpayment', '=', False),
                  ('currency_id', '=', f['currency_id'])]
        domain += self._warehouse_domain(f, 'hmx_warehouse_id')
        for key, field in [('customer_id', 'hmx_customer_id'), ('product_id', 'product_id'),
                           ('family_id', 'hmx_family_id'), ('seller_id', 'hmx_seller_id')]:
            if f[key]:
                domain.append((field, '=', f[key]))
        if start:
            domain += self._period_domain('hmx_date', start, end)
        return domain

    def _drill(self, model, domain, label):
        action = self._action({'model': model, 'domain': domain, 'label': label})
        action['context']['tz'] = TZ
        return action

    def _groups(self, model, domain, groupby, measures):
        source = self.env[model]
        source.check_access_rights('read')
        return source.read_group(domain, measures, groupby, lazy=False)

    def _sum(self, model, domain, field):
        source = self.env[model]
        source.check_access_rights('read')
        return source._read_group(domain, [], [field + ':sum'])[0][0] or 0

    def _card(self, key, label, model, current_domain, previous_domain, field, definition, unit, sign=1):
        try:
            current = sign * self._sum(model, current_domain, field)
            previous = sign * self._sum(model, previous_domain, field)
            return dict(key=key, label=label, available=True, unit=unit, definition=definition,
                        record_count=self.env[model].search_count(current_domain),
                        previous_record_count=self.env[model].search_count(previous_domain),
                        action=self._drill(model, current_domain, label),
                        previous_action=self._drill(model, previous_domain, label + ' · anterior'),
                        **delta(current, previous))
        except AccessError:
            return dict(key=key, label=label, available=False, definition=definition,
                        reason='Sin permiso de lectura en la fuente.', unit=unit)

    def _invoice_domain(self, f, start, end):
        domain = [('company_id', '=', f['company_id']), ('parent_state', '=', 'posted'),
                  ('move_id.move_type', 'in', ['out_invoice', 'out_refund']),
                  ('display_type', '=', 'product'), ('currency_id', '=', f['currency_id'])]
        domain += self._period_domain('move_id.invoice_date', start, end, True)
        domain += self._warehouse_domain(f, 'hmx_warehouse_id')
        for key, field in [('customer_id', 'move_id.commercial_partner_id'), ('product_id', 'product_id'),
                           ('family_id', 'product_id.categ_id'), ('seller_id', 'move_id.invoice_user_id')]:
            if f[key]:
                domain.append((field, '=', f[key]))
        return domain

    def _financial_cards(self, f, currency):
        cards = [self._card('invoiced', 'Facturación neta', 'account.move.line',
            self._invoice_domain(f, f['date_from'], f['date_to']),
            self._invoice_domain(f, f['previous_from'], f['previous_to']), 'amount_currency',
            '−Σ amount_currency de líneas de producto de facturas y notas de crédito contabilizadas; '
            'fecha de factura. Neto de impuestos y descuentos. Almacén comercial único de sale_line_ids; '
            'sin enlace o enlaces de varios almacenes: Sin asignar. Vendedor: responsable de factura.', currency, -1)]
        definition = ('Σ amount de pagos entrantes de clientes en estado paid, asiento posted y cuenta '
                      'por cobrar, por fecha de pago. Incluye impuestos y anticipos; no es venta ni '
                      'cobranza neta de devoluciones. Excluye transferencias internas. No cubre '
                      'cobros registrados directamente en extractos sin account.payment.')
        if f['warehouse_ids'] or any(f[k] for k in ('product_id', 'family_id', 'seller_id')):
            cards.append(dict(key='collected', label='Cobros registrados', available=False, unit=currency,
                reason='No atribuible a almacén, producto, familia o vendedor sin reparto verificable. Quite esos filtros para consultar.',
                definition=definition))
        else:
            domain = [('company_id', '=', f['company_id']), ('currency_id', '=', f['currency_id']),
                      ('state', '=', 'paid'), ('move_id.state', '=', 'posted'),
                      ('partner_type', '=', 'customer'), ('payment_type', '=', 'inbound'),
                      ('destination_account_id.account_type', '=', 'asset_receivable'),
                      ('paired_internal_transfer_payment_id', '=', False)]
            if f['customer_id']:
                domain.append(('partner_id.commercial_partner_id', '=', f['customer_id']))
            cards.append(self._card('collected', 'Cobros registrados', 'account.payment',
                domain + self._period_domain('date', f['date_from'], f['date_to'], True),
                domain + self._period_domain('date', f['previous_from'], f['previous_to'], True),
                'amount', definition, currency))
        return cards

    def _invoice_breakdown(self, f):
        rows = {}
        for name, start, end in [('current', f['date_from'], f['date_to']),
                                 ('previous', f['previous_from'], f['previous_to'])]:
            for group in self._groups('account.move.line', self._invoice_domain(f, start, end),
                                      ['hmx_warehouse_id'], ['amount_currency:sum']):
                wid, label = group['hmx_warehouse_id'] if group['hmx_warehouse_id'] else (0, 'Sin asignar')
                row = rows.setdefault(wid, dict(id=wid, label=label, current=0, previous=0))
                row[name] -= group['amount_currency']
        for wid, row in rows.items():
            row.update(delta(row['current'], row['previous']))
            for name, start, end in [('action', f['date_from'], f['date_to']),
                                     ('previous_action', f['previous_from'], f['previous_to'])]:
                row[name] = self._drill('account.move.line', self._invoice_domain(f, start, end)
                    + [('hmx_warehouse_id', '=', wid or False)], row['label'] + ' · facturación neta')
        return sorted(rows.values(), key=lambda r: r['current'], reverse=True)

    def _dimension(self, f, dimension):
        field = {'warehouse': 'hmx_warehouse_id', 'customer': 'hmx_customer_id',
                 'product': 'product_id', 'family': 'hmx_family_id'}[dimension]
        data = {}
        for name, start, end in [('current', f['date_from'], f['date_to']),
                                 ('previous', f['previous_from'], f['previous_to'])]:
            domain = self._sale_domain(f, start, end)
            for group in self._groups('sale.order.line', domain, [field], ['price_subtotal:sum']):
                key, label = group[field] if group[field] else (0, 'Sin asignar')
                row = data.setdefault(key, dict(id=key, label=label, current=0, previous=0))
                row[name] += group['price_subtotal']
        for row in data.values():
            row.update(delta(row['current'], row['previous']))
            row['action'] = self._drill('sale.order.line', self._sale_domain(f, f['date_from'], f['date_to'])
                                         + [(field, '=', row['id'] or False)], row['label'])
            row['previous_action'] = self._drill('sale.order.line', self._sale_domain(f, f['previous_from'], f['previous_to'])
                                         + [(field, '=', row['id'] or False)], row['label'] + ' · anterior')
            row['color'] = PALETTE[row['id'] % len(PALETTE)] if row['id'] else '#526176'
        return sorted(data.values(), key=lambda row: row['current'], reverse=True)

    def _rank(self, f, rows, dimension, limit=10, variation=False):
        rows = sorted(rows, key=lambda r: abs(r['difference']) if variation else r['current'], reverse=True)
        top, rest = rows[:limit], rows[limit:]
        if rest:
            field = {'warehouse': 'hmx_warehouse_id', 'customer': 'hmx_customer_id',
                     'product': 'product_id', 'family': 'hmx_family_id'}[dimension]
            extra = [(field, 'not in', [r['id'] or False for r in top])]
            top = top + [dict(id='others', label='Otros', color='#526176',
                **delta(sum(r['current'] for r in rest), sum(r['previous'] for r in rest)),
                action=self._drill('sale.order.line', self._sale_domain(f, f['date_from'], f['date_to']) + extra, 'Otros'),
                previous_action=self._drill('sale.order.line', self._sale_domain(f, f['previous_from'], f['previous_to']) + extra, 'Otros · anterior'))]
        total = sum(r['current'] for r in rows)
        cumulative = 0
        result = []
        for row in top:
            cumulative += row['current']
            result.append(dict(row, share=100 * row['current'] / total if total else None,
                               cumulative=100 * cumulative / total if total > 0 and all(r['current'] >= 0 for r in rows) else None))
        return result

    def _series(self, f):
        # Exact selected days in every monthly bucket; previous curve has its own date labels.
        series = []
        for name, start, end in [('current', f['date_from'], f['date_to']),
                                 ('previous', f['previous_from'], f['previous_to'])]:
            buckets = months_between(start, end)
            data = {month: dict(label=month, value=0, warehouses={}) for month in buckets}
            domain = self._sale_domain(f, start, end)
            for group in self._groups('sale.order.line', domain, ['hmx_date:month', 'hmx_warehouse_id'], ['price_subtotal:sum']):
                # read_group's UTC start converts back to the business month (not translated labels).
                raw = group['__range']['hmx_date:month']['from']
                month = fields.Datetime.context_timestamp(self, fields.Datetime.to_datetime(raw)).strftime('%Y-%m')
                if month not in data:
                    continue
                wh = group['hmx_warehouse_id'][0] if group['hmx_warehouse_id'] else 0
                data[month]['value'] += group['price_subtotal']
                data[month]['warehouses'][str(wh)] = group['price_subtotal']
            for month, row in data.items():
                first = fields.Date.to_date(month + '-01')
                low, high = max(start, first), min(end, shift_month(first, 1) - timedelta(days=1))
                row['period'] = '%s — %s' % (low, high)
                row['action'] = self._drill('sale.order.line', self._sale_domain(f, low, high), row['period'])
            series.append({'key': name, 'rows': list(data.values())})
        return series

    def _customer_analysis(self, f, rows):
        # Scope selects the customer population; classification uses the whole visible company history.
        scoped = self._sale_domain(f) + [('hmx_date', '<', fields.Datetime.to_string(utc_midnight(f['date_to'] + timedelta(days=1)))),
                                         ('product_uom_qty', '>', 0), ('price_subtotal', '>', 0)]
        population = self._groups('sale.order.line', scoped, ['hmx_customer_id'], ['price_subtotal:sum'])
        ids = [g['hmx_customer_id'][0] for g in population if g['hmx_customer_id']]
        history = defaultdict(list)
        history_domain = [('company_id', '=', f['company_id']), ('state', '=', 'sale'),
                          ('hmx_customer_id', 'in', ids), ('amount_untaxed', '>', 0),
                          ('date_order', '<', fields.Datetime.to_string(utc_midnight(f['date_to'] + timedelta(days=1))))]
        for group in self._groups('sale.order', history_domain, ['hmx_customer_id', 'date_order:day'], []):
            raw = group['__range']['date_order:day']['from']
            day = fields.Datetime.context_timestamp(self, fields.Datetime.to_datetime(raw)).date()
            history[group['hmx_customer_id'][0]].append(day)
        indexed = {row['id']: row for row in rows}
        heat_ids = {r['id'] for r in sorted(rows, key=lambda r: r['current'], reverse=True)[:15]}
        months = months_between(shift_month(f['date_to'].replace(day=1), -11), f['date_to'])
        heat = defaultdict(dict)
        heat_domain = self._sale_domain(f, fields.Date.to_date(months[0] + '-01'), f['date_to']) + [('hmx_customer_id', 'in', list(heat_ids))]
        for group in self._groups('sale.order.line', heat_domain, ['hmx_customer_id', 'hmx_date:month'], ['price_subtotal:sum']):
            if not group['hmx_customer_id']:
                continue
            month = fields.Datetime.context_timestamp(self, fields.Datetime.to_datetime(
                group['__range']['hmx_date:month']['from'])).strftime('%Y-%m')
            heat[group['hmx_customer_id'][0]][month] = group['price_subtotal']
        customers = []
        total = sum(r['current'] for r in rows)
        for group in population:
            if not group['hmx_customer_id']:
                continue
            cid, name = group['hmx_customer_id']
            row = dict(indexed.get(cid, dict(id=cid, label=name, **delta(0, 0))))
            row.update(customer_status(history[cid], f['date_from'], f['date_to'], f['inactivity_days'], f['cadence_factor']))
            row['status_label'] = STATUS[row['status']]
            row['share'] = 100 * row['current'] / total if total else None
            customer_filter = dict(f, customer_id=cid)
            row['action'] = self._drill('sale.order.line', self._sale_domain(customer_filter, f['date_from'], f['date_to']), name)
            row['previous_action'] = self._drill('sale.order.line', self._sale_domain(customer_filter, f['previous_from'], f['previous_to']), name + ' · anterior')
            row['history_action'] = self._drill('sale.order', [t for t in history_domain if t[0] != 'hmx_customer_id']
                                               + [('hmx_customer_id', '=', cid)], name + ' · historial visible')
            row['months'] = [dict(label=m, value=heat[cid].get(m, 0),
                action=self._drill('sale.order.line', self._sale_domain(customer_filter, fields.Date.to_date(m + '-01'),
                    min(f['date_to'], shift_month(fields.Date.to_date(m + '-01'), 1) - timedelta(days=1))), name + ' · ' + m)) for m in months] if cid in heat_ids else []
            customers.append(row)
        customers.sort(key=lambda r: (r['status'] != 'lost', r['difference'], -r['current']))
        return dict(rows=customers, months=months, cohorts=[dict(key=k, label=v,
                    count=sum(r['status'] == k for r in customers),
                    amount=sum(r['current'] for r in customers if r['status'] == k)) for k, v in STATUS.items()])

    def _volume(self, f, production=False):
        model = 'stock.move' if production else 'sale.order.line'
        whfield = 'hmx_production_warehouse_id' if production else 'hmx_warehouse_id'
        qtyfield = 'quantity' if production else 'product_uom_qty'
        datefield = 'date' if production else 'hmx_date'
        rows = {}
        for name, start, end in [('current', f['date_from'], f['date_to']),
                                 ('previous', f['previous_from'], f['previous_to'])]:
            domain = self._production_domain(f, start, end) if production else self._sale_domain(f, start, end)
            groupby = [whfield, 'product_id', 'hmx_family_id', 'product_uom', datefield + ':month']
            for group in self._groups(model, domain, groupby, [qtyfield + ':sum']):
                wid, warehouse = group[whfield] if group[whfield] else (0, 'Sin asignar')
                pid, product = group['product_id'] if group['product_id'] else (0, 'Sin producto')
                uid, unit = group['product_uom'] if group['product_uom'] else (0, 'Sin unidad')
                family_id, family = group['hmx_family_id'] if group['hmx_family_id'] else (0, 'Sin asignar')
                raw = group['__range'][datefield + ':month']['from']
                month = fields.Datetime.context_timestamp(self, fields.Datetime.to_datetime(raw)).strftime('%Y-%m')
                key = (wid, pid, uid)
                row = rows.setdefault(key, dict(id='%s/%s/%s' % key, warehouse_id=wid, warehouse=warehouse,
                    label=product, family=family, family_id=family_id, unit=unit, unit_id=uid, current=0, previous=0, months={}, month_actions={}))
                row[name] += group[qtyfield]
                if name == 'current':
                    row['months'][month] = group[qtyfield]
                    # Exact server-generated month domain, also used by the chart drill.
                    row['month_actions'][month] = self._drill(model, group['__domain'], product + ' · ' + month)
                row[name + '_action'] = self._drill(model, domain + [(whfield, '=', wid or False),
                    ('product_id', '=', pid or False), ('product_uom', '=', uid or False)], product + ' · ' + unit)
        for row in rows.values():
            row.update(delta(row['current'], row['previous']))
        return sorted(rows.values(), key=lambda r: (r['unit'], -r['current']))

    def _family_volume(self, f, volumes):
        rows = {}
        for item in volumes:
            key = (item['family_id'], item['unit_id'])
            row = rows.setdefault(key, dict(id='%s/%s' % key, label=item['family'],
                unit=item['unit'], current=0, previous=0))
            row['current'] += item['current']
            row['previous'] += item['previous']
        for (fid, uid), row in rows.items():
            row.update(delta(row['current'], row['previous']))
            for name, start, end in [('action', f['date_from'], f['date_to']),
                                     ('previous_action', f['previous_from'], f['previous_to'])]:
                row[name] = self._drill('sale.order.line', self._sale_domain(f, start, end)
                    + [('hmx_family_id', '=', fid or False), ('product_uom', '=', uid or False)], row['label'] + ' · ' + row['unit'])
        return sorted(rows.values(), key=lambda r: (r['unit'], -r['current']))

    def _production_domain(self, f, start, end):
        domain = [('company_id', '=', f['company_id']), ('state', '=', 'done'),
                  ('production_id', '!=', False), ('byproduct_id', '=', False), ('scrapped', '=', False),
                  ('location_id.usage', '=', 'production'), ('location_dest_id.usage', '=', 'internal')]
        domain += self._warehouse_domain(f, 'hmx_production_warehouse_id')
        if f['product_id']:
            domain.append(('product_id', '=', f['product_id']))
        if f['family_id']:
            domain.append(('hmx_family_id', '=', f['family_id']))
        return domain + self._period_domain('date', start, end)

    def _production(self, f):
        result = dict(available=True, rows=[], status=[], note='')
        try:
            result['rows'] = self._volume(f, production=True)
            result['chart_series'] = []
            for uid in sorted(set(r['unit_id'] for r in result['rows'])):
                subset = [r for r in result['rows'] if r['unit_id'] == uid]
                unit = subset[0]['unit']
                for wid in sorted(set(r['warehouse_id'] for r in subset)):
                    selected = [r for r in subset if r['warehouse_id'] == wid]
                    # Native domains built on the server retain timezone, product/family and scope.
                    points = []
                    for month in months_between(f['date_from'], f['date_to']):
                        first = fields.Date.to_date(month + '-01')
                        low, high = max(first, f['date_from']), min(shift_month(first, 1) - timedelta(days=1), f['date_to'])
                        drill_domain = self._production_domain(f, low, high) + [('hmx_production_warehouse_id', '=', wid or False), ('product_uom', '=', uid)]
                        points.append(dict(label=month, value=sum(r['months'].get(month, 0) for r in selected),
                            action=self._drill('stock.move', drill_domain, selected[0]['warehouse'] + ' · ' + month)))
                    result['chart_series'].append(dict(id=wid, label=selected[0]['warehouse'], unit=unit, unit_id=uid,
                        color=PALETTE[wid % len(PALETTE)] if wid else '#526176', points=points,
                        current=sum(r['current'] for r in selected), previous=sum(r['previous'] for r in selected)))
            domain = [('company_id', '=', f['company_id'])] + self._warehouse_domain(f, 'hmx_warehouse_id')
            if f['product_id']:
                domain.append(('product_id', '=', f['product_id']))
            if f['family_id']:
                domain.append(('product_id.categ_id', '=', f['family_id']))
            states = [('confirmed', 'Pendientes · actuales'), ('progress', 'En proceso · actuales'),
                      ('to_close', 'Por cerrar · actuales'), ('done', 'Terminadas · período')]
            for state, label in states:
                selected = domain + [('state', '=', state)]
                if state == 'done':
                    selected += self._period_domain('date_finished', f['date_from'], f['date_to'])
                for group in self._groups('mrp.production', selected, ['hmx_warehouse_id'], []):
                    wid, warehouse = group['hmx_warehouse_id'] if group['hmx_warehouse_id'] else (0, 'Sin asignar')
                    result['status'].append(dict(id='%s/%s' % (wid, state), warehouse=warehouse,
                        label=label, value=group['__count'], action=self._drill('mrp.production', group['__domain'], label)))
            result['compliance'] = []
            done_domain = domain + [('state', '=', 'done')] + self._period_domain('date_finished', f['date_from'], f['date_to'])
            grouped = self._groups('mrp.production', done_domain, ['hmx_warehouse_id', 'hmx_deadline_result'], [])
            by_wh = {}
            for group in grouped:
                wid, label = group['hmx_warehouse_id'] if group['hmx_warehouse_id'] else (0, 'Sin asignar')
                row = by_wh.setdefault(wid, dict(id=wid, label=label, on_time=0, late=0, no_date=0))
                if group['hmx_deadline_result'] in ('on_time', 'late', 'no_date'):
                    row[group['hmx_deadline_result']] += group['__count']
            for wid, row in by_wh.items():
                base = row['on_time'] + row['late']
                row.update(percent=100 * row['on_time'] / base if base else None,
                    action=self._drill('mrp.production', done_domain + [('hmx_warehouse_id', '=', wid or False)], row['label']))
            result['compliance'] = list(by_wh.values())
            late_domain = domain + [('state', 'in', ['confirmed', 'progress', 'to_close']),
                                   ('date_deadline', '<', fields.Datetime.to_string(fields.Datetime.now()))]
            result['late_count'] = self.env['mrp.production'].search_count(late_domain)
            result['late_action'] = self._drill('mrp.production', late_domain, 'OP abiertas con fecha límite vencida')
            result['note'] = ('Cantidad real en movimientos hechos de producto principal, origen Producción y destino Interno. '
                'Almacén del tipo de fabricación de la OP: ubicación de suministro y almacén comercial no lo sustituyen. '
                'Cliente, vendedor y moneda no filtran producción; producto y familia sí. Unidades exactas, sin sumar unidades distintas. '
                'Cumplimiento: cierres con fecha límite vigente; no es cumplimiento de un programa original congelado. '
                'No se calcula productividad/hora ni capacidad sin tiempos y calendarios validados.')
        except AccessError:
            result = dict(available=False, rows=[], status=[], compliance=[], note='Sin permiso para consultar producción y sus movimientos.')
        return result

    def _panels(self, tab, specs):
        panels = super()._panels(tab, specs)
        if tab == 'entregas':
            panels.append(self._group_panel(specs['delivery_done'], 'dispatch_warehouse',
                'Entregas realizadas por almacén de despacho', 'hmx_dispatch_warehouse_id'))
        return panels

    def _stock_panel(self, domain_extra=None):
        f = self.env.context.get('hmx_analytics_filters')
        extra = list(domain_extra or [])
        if f:
            extra += self._warehouse_domain(f, 'location_id.warehouse_id')
            if f['product_id']:
                extra.append(('product_id', '=', f['product_id']))
            if f['family_id']:
                extra.append(('product_id.categ_id', '=', f['family_id']))
        return super()._stock_panel(extra)

    def _catalog(self, f):
        specs = super()._catalog(f)
        # Existing operational tabs retain their sources and semantics. Filter only proven relationships.
        for spec in specs.values():
            model = spec['model']
            mapping = {'mrp.production': 'hmx_warehouse_id', 'sale.order': 'warehouse_id',
                       'sale.order.line': 'hmx_warehouse_id', 'stock.picking': 'picking_type_id.warehouse_id',
                       'quality.inspection': 'production_order_id.hmx_warehouse_id',
                       'purchase.order': 'picking_type_id.warehouse_id', 'stock.scrap': 'location_id.warehouse_id',
                       'stock.quant': 'location_id.warehouse_id'}
            extra = self._warehouse_domain(f, mapping[model]) if model in mapping else []
            if spec['key'] == 'delivery_done':
                extra = self._warehouse_domain(f, 'hmx_dispatch_warehouse_id')
            if model in ('mrp.production', 'sale.order.line', 'stock.scrap', 'stock.quant'):
                if f['product_id']:
                    extra.append(('product_id', '=', f['product_id']))
                if f['family_id']:
                    extra.append(('product_id.categ_id', '=', f['family_id']))
            if model == 'sale.order.line':
                for key, field in [('customer_id', 'hmx_customer_id'), ('seller_id', 'hmx_seller_id')]:
                    if f[key]:
                        extra.append((field, '=', f[key]))
            spec['domain'] += extra
            if spec['denominator'] is not None:
                spec['denominator'] += extra
        return specs

    @api.model
    def get_dashboard(self, tab='resumen', filters=None):
        service, f = self._scope(filters or {})
        if tab not in dict(EXEC_TABS):
            data = super().get_dashboard(tab, filters)
            data['scope_note'] = ('Vista operativa: período para resultados; pendientes y existencias al momento actual. '
                'Almacenes solo mediante relaciones verificables. Cliente/vendedor solo en líneas comerciales; '
                'asistencia y evidencias no tienen relación verificable con estos almacenes. Comparación monetaria solo en vistas ejecutivas.')
            return data
        currency = service.env['res.currency'].browse(f['currency_id']).name
        data = dict(executive=True, cards=[], dimensions={}, series=[], customers=None, volume=[], production=None,
                    company=service.env.company.name, today=str(f['today']), currency=currency,
                    currency_digits=service.env['res.currency'].browse(f['currency_id']).decimal_places,
                    updated_at=fields.Datetime.context_timestamp(service, fields.Datetime.now()).strftime('%d/%m/%Y %H:%M'),
                    period='%s — %s' % (f['date_from'], f['date_to']),
                    comparison_period='%s — %s' % (f['previous_from'], f['previous_to']),
                    months=months_between(f['date_from'], f['date_to']), insights=[], notes=[], partial=None)
        data['notes'] = [
            'Solo registros visibles de la compañía activa. Importes en %s, moneda original, sin conversión ni suma entre monedas.' % currency,
            'Facturación y cobros provienen de Contabilidad de Odoo. Las facturas externas de Evidencias no se consideran contabilizadas ni cobradas por su comprobación documental.',
            'Ventas: almacén del pedido. Fabricación: almacén del tipo de operación de la OP. Despacho: almacén único de las ubicaciones origen de movimientos realizados a cliente; sin evidencia o varios, Sin asignar. No se empatan con hmx.planta ni por nombre.',
            'Clientes: entidad comercial; historial completo visible hasta el corte, en todas las monedas y almacenes. Nuevo significa primera compra en ese historial, sujeto a migraciones y permisos. Sin recompra no significa baja confirmada.',
            'Inactividad: %s días; con ≥4 días de compra anteriores se usa el mayor entre ese umbral y %s × mediana de intervalos. El mapa cubre 12 meses hasta el corte, con los filtros comerciales.' % (f['inactivity_days'], f['cadence_factor']),
        ]
        if tab != 'produccion':
            if not service.env.user.has_group(SALE_PERMISSION):
                data['commercial_available'] = False
                data['notes'].insert(0, 'Importes y análisis comercial restringidos por Control de Costos.')
            else:
                try:
                    current = service._sale_domain(f, f['date_from'], f['date_to'])
                    previous = service._sale_domain(f, f['previous_from'], f['previous_to'])
                    data['cards'] = [service._card('sales', 'Ventas confirmadas', 'sale.order.line', current, previous,
                        'price_subtotal', 'Σ price_subtotal de líneas en pedidos sale, sin impuestos y después de descuentos; '
                        'fecha de pedido/confirmación date_order, zona Monterrey. Excluye cotizaciones, cancelados, '
                        'secciones y anticipos. No se cruza con entregas ni facturas.', currency)]
                    data['cards'] += service._financial_cards(f, currency)
                    data['invoice_warehouses'] = service._invoice_breakdown(f) if data['cards'][1]['available'] else None
                    data['commercial_available'] = True
                    data['series'] = service._series(f) if tab in ('resumen', 'comercial') else []
                    for dim in ('warehouse', 'customer', 'product', 'family'):
                        rows = service._dimension(f, dim)
                        data['dimensions'][dim] = service._rank(f, rows, dim, limit=10) if dim != 'warehouse' else service._rank(f, rows, dim, limit=len(rows))
                        data['dimensions'][dim + '_drivers'] = service._rank(f, rows, dim, variation=True)
                        if dim == 'customer' and tab in ('resumen', 'clientes'):
                            data['customers'] = service._customer_analysis(f, rows)
                    data['volume'] = service._volume(f) if tab == 'productos' else []
                    data['family_volume'] = service._family_volume(f, data['volume'])
                    warehouses = data['dimensions']['warehouse']
                    data['reconciliation'] = delta(sum(r['current'] for r in warehouses), data['cards'][0]['current'])
                    # This is arithmetic contribution, never a causal inference.
                    for row in sorted(warehouses, key=lambda r: abs(r['difference']), reverse=True)[:3]:
                        data['insights'].append(dict(label=row['label'], difference=row['difference'],
                            current=row['current'], previous=row['previous'], action=row['action']))
                    if f['date_to'] == f['today'] and f['date_from'].day == 1 and f['date_from'].month == f['today'].month:
                        prior_start = shift_month(f['date_from'], -1)
                        prior_end = shift_month(f['date_to'], -1)
                        base = service._sum('sale.order.line', service._sale_domain(f, prior_start, prior_end), 'price_subtotal')
                        data['partial'] = dict(period='%s — %s' % (prior_start, prior_end), **delta(data['cards'][0]['current'], base))
                except AccessError:
                    data.update(commercial_available=False, cards=[], dimensions={}, series=[], customers=None, volume=[])
                    data['notes'].insert(0, 'Sin permiso para alguna fuente comercial. No se sustituye el error por cero.')
        if tab in ('resumen', 'produccion'):
            data['production'] = service._production(f)
        return data

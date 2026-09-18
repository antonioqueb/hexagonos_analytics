"""Consolidated MXN analytics: convert each month's USD facts before ranking or summing."""
from decimal import Decimal

from odoo import api, fields, models
from odoo.exceptions import AccessError

from .analytics_math import shift_month
from .dof_math import DofRateError, converted_amount
from .executive import EXEC_TABS, SALE_PERMISSION


MONEY = {
    'sale.order.line': ('price_subtotal', 'hmx_date', False),
    'account.move.line': ('amount_currency', 'hmx_invoice_date', True),
    'account.payment': ('amount', 'date', True),
    'purchase.order': ('amount_untaxed', 'date_approve', False),
}


class DofAnalytics(models.AbstractModel):
    _inherit = 'hexagonos.analytics'

    def _conversion_domain(self, domain):
        conversion = self.env.context.get('hmx_dof_conversion')
        return domain + [('currency_id', 'in', conversion['currency_ids'])] if conversion else domain

    def _sale_domain(self, f, start=None, end=None):
        return self._conversion_domain(super()._sale_domain(f, start, end))

    def _invoice_domain(self, f, start, end):
        return self._conversion_domain(super()._invoice_domain(f, start, end))

    def _payment_domain(self, f):
        return self._conversion_domain(super()._payment_domain(f))

    def _groups(self, model, domain, groupby, measures):
        conversion = self.env.context.get('hmx_dof_conversion')
        monetary = MONEY.get(model)
        if not conversion or not monetary or monetary[0] + ':sum' not in measures:
            return super()._groups(model, domain, groupby, measures)
        field, date_field, date_only = monetary
        month_field = date_field + ':month'
        expanded = list(dict.fromkeys(list(groupby) + ['currency_id', month_field]))
        raw = super()._groups(model, domain, expanded, measures)
        merged = {}
        for row in raw:
            currency = row['currency_id'][0] if row['currency_id'] else 0
            raw_date = row.get('__range', {}).get(month_field, {}).get('from')
            if not raw_date:
                raise DofRateError('Hay un importe sin fecha verificable para convertir.')
            month = str(raw_date)[:7] if date_only else fields.Datetime.context_timestamp(
                self, fields.Datetime.to_datetime(raw_date)).strftime('%Y-%m')
            amount = converted_amount(row[field], currency, conversion['mxn_id'], conversion['usd_id'], month, conversion['rates'])
            key = tuple(tuple(row.get(name, row.get(name.split(':')[0]))) if isinstance(row.get(name, row.get(name.split(':')[0])), (tuple, list))
                        else row.get(name, row.get(name.split(':')[0])) for name in groupby)
            if key not in merged:
                merged[key] = {name: row.get(name, row.get(name.split(':')[0])) for name in groupby}
                merged[key].update(__range={k: v for k, v in row.get('__range', {}).items() if k in groupby}, __count=0)
                for measure in measures:
                    merged[key][measure.split(':')[0]] = Decimal(0)
            target = merged[key]
            target['__count'] += row.get('__count', 0)
            for measure in measures:
                name = measure.split(':')[0]
                target[name] += amount if name == field else Decimal(str(row.get(name) or 0))
        for row in merged.values():
            for measure in measures:
                name = measure.split(':')[0]
                row[name] = float(row[name])
        return list(merged.values())

    def _sum(self, model, domain, field):
        if self.env.context.get('hmx_dof_conversion') and model in MONEY and MONEY[model][0] == field:
            return sum(row[field] for row in self._groups(model, domain, [], [field + ':sum']))
        return super()._sum(model, domain, field)

    def _dof_context(self, tab, f):
        currencies = self.env['res.currency'].with_context(active_test=False).search([('name', 'in', ['MXN', 'USD'])])
        indexed = {currency.name: currency.id for currency in currencies}
        if not indexed.get('MXN'):
            raise DofRateError('El catálogo no tiene la moneda MXN para consolidar.')
        start = min(f['date_from'], f['previous_from'])
        if tab in ('resumen', 'clientes'):
            start = min(start, shift_month(f['date_to'].replace(day=1), -11))
        # Partial-month comparison may use a different prior month than the main comparison.
        if f['date_from'].day == 1 and f['date_to'] == f['today']:
            start = min(start, shift_month(f['date_from'], -1))
        end = max(f['date_to'], f['previous_to'])
        rates = self.env['hexagonos.analytics.dof.month']._monthly_rates(start, end, f['today'])
        conversion = dict(currency_ids=list(indexed.values()), mxn_id=indexed['MXN'], usd_id=indexed.get('USD'), rates=rates)
        return self.with_context(hmx_dof_conversion=conversion), conversion

    def _dof_metadata(self, conversion, f):
        return dict(applied=True, target='MXN', source='DOF · indicador 158',
            formula='Σ por mes [MXN del mes + USD del mes × promedio mensual DOF].',
            method='Promedio aritmético de publicaciones diarias del dólar, por fecha de publicación. '
                   'Sin repetir fines de semana ni días sin publicación. Meses cerrados: mes completo; '
                   'mes abierto: publicaciones disponibles hasta hoy, promedio provisional. '
                   'Cada fecha de venta, factura o pago determina su mes. La tasa se muestra a seis decimales; '
                   'la conversión conserva la precisión del promedio. La variación también incluye el efecto cambiario. '
                   'No modifica tasas ni asientos contables.',
            rates=[dict(row, selected=f['date_from'].strftime('%Y-%m') <= month <= f['date_to'].strftime('%Y-%m'))
                   for month, row in sorted(conversion['rates'].items())],
            supported=['MXN', 'USD'])

    @api.model
    def get_dashboard(self, tab='resumen', filters=None):
        # RPC context is user-controlled: accept rates only from our official provider below.
        service, f = self.with_context(hmx_dof_conversion=None)._scope(filters or {})
        commercial = tab in dict(EXEC_TABS) and tab != 'produccion' and service.env.user.has_group(SALE_PERMISSION)
        purchase = tab == 'compras' and service.env.user.has_group('control_costos_hexagonos.group_view_purchase_total')
        if f['currency_id'] or not (commercial or purchase):
            return super(DofAnalytics, service).get_dashboard(tab, filters)
        try:
            converted, conversion = service._dof_context(tab, f)
            if commercial:
                data = converted._executive_dashboard(tab, dict(f, display_currency_id=conversion['mxn_id']))
                data['notes'] = ['Consolidado analítico en MXN: pesos más dólares convertidos al promedio mensual DOF. Otras divisas quedan fuera.'] + [
                    note for note in data['notes'] if 'moneda original, sin conversión ni suma entre monedas' not in note]
                for card in data['cards']:
                    card['definition'] += ' USD convertidos por mes al promedio DOF; MXN conservados. Los documentos abren en su moneda original.'
                data['fx'] = service._dof_metadata(conversion, f)
                data['fx']['excluded'] = service._dof_other_currencies(f, conversion)
                return data
            # Operational counts keep their own scope; only the monetary purchase panel is converted.
            data = super(DofAnalytics, service).get_dashboard(tab, filters)
            if not any(panel['key'] == 'purchase_amount' and panel['available'] for panel in data['panels']):
                return data
            spec = service._catalog(f)['purchase_orders']
            domain = converted._conversion_domain(spec['domain'])
            amount = converted._sum('purchase.order', domain, 'amount_untaxed')
            for panel in data['panels']:
                if panel['key'] == 'purchase_amount' and panel['available']:
                    panel.update(label='Compra confirmada · consolidado MXN',
                        note='Sin impuestos. MXN + USD al promedio DOF de cada mes de aprobación. Otras divisas excluidas; los conteos conservan su alcance.',
                        rows=[dict(key='dof_mxn', id=conversion['mxn_id'], label='MXN', value=amount,
                                   action=service._drill('purchase.order', domain, 'Compras · MXN y USD originales'))])
            data['fx'] = service._dof_metadata(conversion, f)
            return data
        except DofRateError as error:
            # Preserve the usable original-currency dashboard; never publish a partial or fabricated total.
            data = super(DofAnalytics, service).get_dashboard(tab, filters)
            data['fx'] = dict(applied=False, error=str(error), rates=[], source='DOF · indicador 158')
            return data

    def _dof_other_currencies(self, f, conversion):
        other = set()
        for start, end in [(f['date_from'], f['date_to']), (f['previous_from'], f['previous_to'])]:
            domains = [('sale.order.line', self._sale_domain(f, start, end)),
                       ('account.move.line', self._invoice_domain(f, start, end))]
            if not f['warehouse_ids'] and not any(f[key] for key in ('product_id', 'family_id', 'seller_id')):
                domains.append(('account.payment', self._payment_domain(f) + self._period_domain('date', start, end, True)))
            for model, domain in domains:
                try:
                    for row in self._groups(model, domain + [('currency_id', 'not in', conversion['currency_ids'])], ['currency_id'], []):
                        if row['currency_id']:
                            other.add(row['currency_id'][1])
                except AccessError:
                    continue
        return sorted(other)

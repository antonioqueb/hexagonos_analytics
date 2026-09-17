"""Portable regression tests; fixtures are synthetic, never evidence of real Odoo validation."""
import importlib.util
import json
import sys
import types
import unittest
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import check_logic as legacy

ROOT = Path(__file__).parents[1]
package = types.ModuleType('hmx_test')
package.__path__ = [str(ROOT / 'models')]
sys.modules['hmx_test'] = package
sys.modules['hmx_test.analytics'] = legacy.analytics

def load(name):
    spec = importlib.util.spec_from_file_location('hmx_test.' + name, ROOT / 'models' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

math = load('analytics_math')
executive = load('executive')
legacy.Datetime.to_datetime = staticmethod(lambda value: datetime.fromisoformat(value) if isinstance(value, str) else value)
legacy.Datetime.context_timestamp = staticmethod(lambda record, value: value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(executive.TZ)))


def matches(domain, row):
    normalized = {k: v[0] if isinstance(v, tuple) else v for k, v in row.items()}
    converted = [('id', '!=', -1) if isinstance(t, (tuple, list)) and t[1] == 'not in' and normalized.get(t[0], False) not in t[2]
                 else ('id', '=', -1) if isinstance(t, (tuple, list)) and t[1] == 'not in' else t for t in domain]
    return legacy.matches(converted, normalized)


class Source:
    def __init__(self, rows, denied=False):
        self.rows, self.denied = rows, denied
    def check_access_rights(self, mode):
        if self.denied:
            raise legacy.AccessError('restricted')
    def search_count(self, domain):
        self.check_access_rights('read')
        return sum(matches(domain, row) for row in self.rows)
    def _read_group(self, domain, groups, measures):
        self.check_access_rights('read')
        return [tuple(sum(row.get(field.split(':')[0], 0) or 0 for row in self.rows if matches(domain, row)) for field in measures)]
    def read_group(self, domain, measures, groupby, lazy=False):
        self.check_access_rights('read')
        buckets = {}
        for row in self.rows:
            if not matches(domain, row):
                continue
            values, ranges, extra = [], {}, []
            for field in groupby:
                if ':' in field:
                    fieldname, granularity = field.split(':')
                    day = legacy.Datetime.context_timestamp(None, datetime.fromisoformat(row[fieldname])).date()
                    low = day.replace(day=1) if granularity == 'month' else day
                    high = math.shift_month(low, 1) if granularity == 'month' else low + timedelta(days=1)
                    a, b = (legacy.Datetime.to_string(legacy.analytics.utc_midnight(d)) for d in (low, high))
                    value = low.isoformat()
                    ranges[field] = {'from': a, 'to': b}
                    extra += [(fieldname, '>=', a), (fieldname, '<', b)]
                else:
                    value = row.get(field, False)
                    extra += [(field, '=', value[0] if isinstance(value, tuple) else value)]
                values.append(value)
            key = tuple(values)
            item = buckets.setdefault(key, dict(zip(groupby, values), __count=0, __domain=domain + extra, __range=ranges))
            item['__count'] += 1
            for measure in measures:
                name = measure.split(':')[0]
                item[name] = item.get(name, 0) + (row.get(name, 0) or 0)
        return list(buckets.values())


class Environment(dict):
    def __init__(self):
        super().__init__()
        self.company = types.SimpleNamespace(id=1, name='EMPRESA DE PRUEBA', currency_id=types.SimpleNamespace(id=1))
        self.companies = types.SimpleNamespace(ids=[1])
        self.user = types.SimpleNamespace(has_group=lambda group: True)
    def __missing__(self, key):
        return Source([])


class Service(executive.ExecutiveAnalytics, legacy.analytics.HexagonosAnalytics):
    def __init__(self):
        self.env = Environment()
    def _scope(self, filters):
        return self, dict(FILTERS, **filters)


FILTERS = dict(company_id=1, currency_id=1, date_from=date(2026, 9, 1), date_to=date(2026, 9, 16),
    previous_from=date(2026, 8, 1), previous_to=date(2026, 8, 16), today=date(2026, 9, 16),
    warehouse_ids=[], customer_id=0, product_id=0, family_id=0, seller_id=0,
    inactivity_days=90, cadence_factor=2, comparison='previous_month')


def sale(id, warehouse=1, amount=100, when='2026-09-10 12:00:00', customer=1, product=1, unit=1, currency=1, state='sale'):
    return dict(id=id, company_id=1, currency_id=currency, state=state, display_type=False, is_downpayment=False,
        hmx_date=when, hmx_warehouse_id=(warehouse, f'Almacén {warehouse}') if warehouse else False,
        hmx_customer_id=(customer, f'Cliente {customer}'), product_id=(product, f'Producto {product}'),
        hmx_family_id=(1, 'Panel de cartón'), hmx_seller_id=(1, 'Vendedor'),
        product_uom=(unit, 'Piezas' if unit == 1 else 'kg'), price_subtotal=amount, product_uom_qty=amount / 10)


def fixture_service():
    s = Service()
    rows = [sale(1), sale(2, 2, 200, customer=2), sale(3, 0, 50, customer=3),
            sale(4, amount=75, when='2026-08-10 12:00:00'), sale(5, 2, 300, when='2026-08-10 12:00:00', customer=2),
            sale(6, amount=800, currency=2), sale(7, amount=900, state='draft'),
            sale(8, amount=99, when='2026-05-01 12:00:00', customer=3)]
    s.env['sale.order.line'] = Source(rows)
    s.env['sale.order'] = Source([dict(id=r['id'], company_id=1, state=r['state'], hmx_customer_id=r['hmx_customer_id'],
        amount_untaxed=r['price_subtotal'], date_order=r['hmx_date']) for r in rows])
    s.env['res.currency'] = types.SimpleNamespace(browse=lambda cid: types.SimpleNamespace(name='MXN', decimal_places=2))
    s.env['stock.move'] = Source([dict(id=1, company_id=1, state='done', production_id=42,
        byproduct_id=False, scrapped=False, **{'location_id.usage': 'production', 'location_dest_id.usage': 'internal'},
        hmx_production_warehouse_id=(2, 'Almacén 2'), product_id=(1, 'Producto 1'), hmx_family_id=(1, 'Panel de cartón'),
        product_uom=(1, 'Piezas'), date='2026-09-10 12:00:00', quantity=15)])
    return s


class TestExecutive(unittest.TestCase):
    def setUp(self):
        self.service = fixture_service()
    def test_consolidated_equals_all_warehouses_including_unassigned(self):
        rows = self.service._dimension(FILTERS, 'warehouse')
        self.assertEqual(sum(r['current'] for r in rows), 350)
        self.assertEqual(sum(r['previous'] for r in rows), 375)
        self.assertEqual(next(r['current'] for r in rows if r['id'] == 0), 50)
        for row in rows:
            self.assertEqual(self.service._sum('sale.order.line', row['action']['domain'], 'price_subtotal'), row['current'])
            self.assertEqual(self.service._sum('sale.order.line', row['previous_action']['domain'], 'price_subtotal'), row['previous'])
    def test_filters_preserve_contribution_and_do_not_link_delivery_or_invoice(self):
        f = dict(FILTERS, warehouse_ids=[1, 0])
        rows = self.service._dimension(f, 'warehouse')
        self.assertEqual(sum(r['current'] for r in rows), 150)
        self.service.env['stock.picking'] = Source([{'id': i} for i in range(50)])
        self.service.env['account.move'] = Source([{'id': i} for i in range(50)])
        self.assertEqual(self.service._dimension(f, 'warehouse'), rows)
    def test_other_bucket_and_drill_concile(self):
        rows = self.service._dimension(FILTERS, 'warehouse')
        ranked = self.service._rank(FILTERS, rows, 'warehouse', limit=1)
        self.assertEqual(ranked[-1]['label'], 'Otros')
        self.assertEqual(sum(r['current'] for r in ranked), 350)
        self.assertEqual(self.service._sum('sale.order.line', ranked[-1]['action']['domain'], 'price_subtotal'), ranked[-1]['current'])
        self.assertEqual(sum(r['difference'] for r in ranked), -25)
    def test_month_series_timezone_and_details_concile(self):
        self.service.env['sale.order.line'].rows += [sale(90, amount=10, when='2026-09-01 05:59:59')]
        current = self.service._series(FILTERS)[0]['rows'][0]
        self.assertEqual(current['value'], 350)
        self.assertEqual(sum(current['warehouses'].values()), 350)
        self.assertEqual(self.service._sum('sale.order.line', current['action']['domain'], 'price_subtotal'), 350)
    def test_units_and_execution_are_independent_of_sales_and_customer(self):
        f = dict(FILTERS, customer_id=999, seller_id=999, currency_id=999, warehouse_ids=[2])
        rows = self.service._volume(f, production=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['current'], 15)
        self.assertEqual(rows[0]['warehouse_id'], 2)
        self.assertEqual(self.service._sum('stock.move', rows[0]['current_action']['domain'], 'quantity'), 15)
        self.assertEqual(self.service._volume(dict(f, warehouse_ids=[1]), production=True), [])
    def test_internal_transfers_byproducts_and_scrap_excluded(self):
        source = self.service.env['stock.move']
        original = source.rows[0]
        for change in [{'production_id': False}, {'byproduct_id': 8}, {'scrapped': True}, {'location_id.usage': 'internal'}]:
            source.rows.append(dict(original, **change))
        self.assertEqual(sum(r['current'] for r in self.service._volume(FILTERS, True)), 15)
    def test_invoices_credit_notes_count_once_and_use_their_own_date(self):
        base = dict(company_id=1, currency_id=1, parent_state='posted', display_type='product', hmx_warehouse_id=(1, 'Almacén 1'))
        def line(id, value, type='out_invoice', state='posted'):
            return dict(base, id=id, amount_currency=value, parent_state=state,
                **{'move_id.move_type': type, 'move_id.invoice_date': '2026-09-03'})
        self.service.env['account.move.line'] = Source([line(1, -60), line(2, -40), line(3, 20, 'out_refund'), line(4, -200, state='draft')])
        cards = self.service._financial_cards(FILTERS, 'MXN')
        self.assertEqual(cards[0]['current'], 80)
        self.assertEqual(cards[0]['record_count'], 3)
        self.assertEqual(self.service._financial_cards(dict(FILTERS, warehouse_ids=[2]), 'MXN')[0]['current'], 0)
        self.assertFalse(self.service._financial_cards(dict(FILTERS, warehouse_ids=[2]), 'MXN')[1]['available'])
    def test_cash_restriction_and_denied_source_not_zero(self):
        self.service.env['account.move.line'] = Source([], denied=True)
        cards = self.service._financial_cards(dict(FILTERS, product_id=1), 'MXN')
        self.assertFalse(cards[0]['available'])
        self.assertNotIn('current', cards[0])
        self.assertFalse(cards[1]['available'])
    def test_reactivation_uses_history_not_previous_month(self):
        rows = self.service._dimension(FILTERS, 'customer')
        customers = self.service._customer_analysis(FILTERS, rows)['rows']
        self.assertEqual(next(r['status'] for r in customers if r['id'] == 3), 'reactivated')
        self.assertEqual(next(r['status'] for r in customers if r['id'] == 1), 'recurring')
    def test_cadence_history_cutoff_and_global_activity(self):
        dates = [date(2025, 1, 1), date(2025, 4, 1), date(2025, 7, 1), date(2025, 10, 1), date(2026, 1, 1)]
        cohort = math.customer_status(dates, date(2025, 12, 1), date(2026, 1, 10), 90, 2)
        self.assertEqual(cohort['status'], 'recurring')
        self.assertGreater(cohort['threshold'], 180)
        new = math.customer_status([date(2026, 9, 2), date(2027, 1, 1)], date(2026, 9, 1), date(2026, 9, 16), 90, 2)
        self.assertEqual(new['status'], 'new')
        self.assertEqual(new['last_purchase'], '2026-09-02')
    def test_comparison_leap_month_end_partial_and_zero_base(self):
        self.assertEqual(math.comparison(date(2026, 9, 1), date(2026, 9, 16), 'previous_month'), (date(2026, 8, 1), date(2026, 8, 16)))
        self.assertEqual(math.comparison(date(2026, 2, 1), date(2026, 2, 28), 'previous_month'), (date(2026, 1, 1), date(2026, 1, 31)))
        self.assertEqual(math.comparison(date(2024, 2, 1), date(2024, 2, 29), 'previous_year'), (date(2023, 2, 1), date(2023, 2, 28)))
        prior = math.comparison(date(2026, 9, 1), date(2026, 9, 16), 'previous_period')
        self.assertEqual(prior, (date(2026, 8, 16), date(2026, 8, 31)))
        self.assertIsNone(math.delta(100, 0)['percent'])
        self.assertIsNone(math.delta(0, 0)['percent'])
        self.assertEqual(math.delta(0, 100)['percent'], -100)
    def test_production_chart_domains_and_totals_match(self):
        data = self.service._production(FILTERS)
        point = data['chart_series'][0]['points'][0]
        self.assertEqual(point['value'], 15)
        self.assertEqual(self.service._sum('stock.move', point['action']['domain'], 'quantity'), 15)
    def test_assignment_requires_exactly_one_verified_dimension(self):
        self.assertFalse(math.unique_assignment([]))
        self.assertFalse(math.unique_assignment([False]))
        self.assertFalse(math.unique_assignment([1, False]))
        self.assertFalse(math.unique_assignment([1, 2]))
        self.assertEqual(math.unique_assignment([2, 2, 2]), 2)

    def test_duplicate_uom_names_are_not_combined(self):
        original = self.service.env['stock.move'].rows[0]
        self.service.env['stock.move'].rows.append(dict(original, id=2, product_uom=(2, 'Piezas'), quantity=30))
        data = self.service._production(FILTERS)
        self.assertEqual(len(data['chart_series']), 2)
        self.assertEqual({r['unit_id'] for r in data['chart_series']}, {1, 2})
        self.assertEqual(sorted(r['current'] for r in data['chart_series']), [15, 30])

    def test_family_volume_and_invoice_breakdowns_reconcile(self):
        volumes = self.service._volume(FILTERS)
        families = self.service._family_volume(FILTERS, volumes)
        self.assertEqual(sum(r['current'] for r in families), 35)
        for row in families:
            self.assertEqual(self.service._sum('sale.order.line', row['action']['domain'], 'product_uom_qty'), row['current'])
        base = dict(company_id=1, currency_id=1, parent_state='posted', display_type='product',
                    **{'move_id.move_type': 'out_invoice', 'move_id.invoice_date': '2026-09-02'})
        self.service.env['account.move.line'] = Source([dict(base, id=1, amount_currency=-40, hmx_warehouse_id=(1,'Almacén 1')),
                                                       dict(base, id=2, amount_currency=-60, hmx_warehouse_id=False)])
        rows = self.service._invoice_breakdown(FILTERS)
        self.assertEqual(sum(r['current'] for r in rows), 100)
        self.assertEqual(next(r['current'] for r in rows if r['id'] == 0), 60)

    def test_mrp_factory_never_falls_back_to_another_warehouse(self):
        # Execute the real bounded factory method with a test recordset, without loading Odoo.
        import ast
        source = (ROOT.parent / 'aq_simplified_mrp/models/simplified_mrp_api.py').read_text()
        module = ast.parse(source)
        cls = next(n for n in module.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_find_picking_type')
        method.decorator_list = []
        namespace = {'_': lambda text: text, 'UserError': legacy.ValidationError, '_logger': types.SimpleNamespace(info=lambda *a: None)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<real factory method>', 'exec'), namespace)
        calls = []
        wh = types.SimpleNamespace(id=22, name='Selected', display_name='Selected', code='S22', company_id=types.SimpleNamespace(name='Company'))
        factory = types.SimpleNamespace(env={'stock.picking.type': types.SimpleNamespace(search=lambda domain, limit: calls.append(domain) or False)})
        with self.assertRaisesRegex(legacy.ValidationError, 'no tiene un tipo'):
            namespace['_find_picking_type'](factory, wh)
        self.assertEqual(len(calls), 1)
        self.assertIn(('warehouse_id', '=', 22), calls[0])

    def test_dashboard_json_contract(self):
        payload = self.service.get_dashboard('resumen', FILTERS)
        json.dumps(payload)
        self.assertEqual(payload['reconciliation']['difference'], 0)
        self.assertEqual(payload['cards'][0]['current'], 350)


def export(path):
    service = fixture_service()
    options = dict(company_id=1, company='EMPRESA DE PRUEBA', today='2026-09-16', date_from='2026-09-01', currency_id=1,
        tabs=[dict(key=k, label=v) for k, v in executive.EXEC_TABS], warehouses=[dict(id=1, name='Almacén 1'), dict(id=2, name='Almacén 2')],
        currencies=[dict(id=1, name='MXN'), dict(id=2, name='USD')], customers=[dict(id=i, name=f'Cliente {i}') for i in [1,2,3]],
        products=[dict(id=1, name='Producto 1')], familys=[dict(id=1, name='Panel de cartón')], sellers=[dict(id=1, name='Vendedor')])
    payload = dict(options=options, dashboards={tab: service.get_dashboard(tab, FILTERS) for tab, _ in executive.EXEC_TABS})
    Path(path).write_text(json.dumps(payload, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        export(sys.argv[1])
    else:
        unittest.main(verbosity=2)

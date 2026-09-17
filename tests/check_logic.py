"""Portable logic checks (no database). Not a substitute for the Odoo test suite.

Run: python3 hexagonos_analytics/tests/check_logic.py
The fake ORM evaluates actual catalog domains against small business fixtures.
"""
import importlib.util
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree


class AccessError(Exception):
    pass


class ValidationError(Exception):
    pass


class Date:
    @staticmethod
    def context_today(record):
        return date(2026, 9, 16)

    @staticmethod
    def to_date(value):
        return value if isinstance(value, date) else date.fromisoformat(value)


class Datetime:
    @staticmethod
    def now():
        return datetime(2026, 9, 16, 18)

    @staticmethod
    def to_string(value):
        return value.strftime('%Y-%m-%d %H:%M:%S')


odoo = types.ModuleType('odoo')
odoo.api = types.SimpleNamespace(model=lambda method: method, depends=lambda *a: lambda method: method)
odoo.fields = types.SimpleNamespace(Date=Date, Datetime=Datetime, Selection=lambda *a, **kw: None)
odoo.models = types.SimpleNamespace(AbstractModel=object, Model=object)
odoo._ = lambda value: value
exceptions = types.ModuleType('odoo.exceptions')
exceptions.AccessError = AccessError
exceptions.ValidationError = ValidationError
sys.modules['odoo'] = odoo
sys.modules['odoo.exceptions'] = exceptions


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'models' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analytics = load('analytics')
production = load('production')


def matches(domain, record):
    """Odoo prefix operators with implicit AND between remaining expressions."""
    tokens = iter(domain)

    def evaluate(token):
        if token == '|':
            a, b = evaluate(next(tokens)), evaluate(next(tokens))
            return a or b
        if token == '&':
            a, b = evaluate(next(tokens)), evaluate(next(tokens))
            return a and b
        if token == '!':
            return not evaluate(next(tokens))
        field, operator, expected = token
        actual = record.get(field, False)
        if operator == '=':
            return actual == expected
        if operator == '!=':
            return actual != expected
        if operator == 'in':
            return actual in expected
        if not actual:
            return False
        return {'<': lambda: actual < expected, '>': lambda: actual > expected,
                '<=': lambda: actual <= expected, '>=': lambda: actual >= expected}[operator]()

    results = [evaluate(token) for token in tokens]
    return all(results)


class Source:
    def __init__(self, rows):
        self.rows = rows

    def check_access_rights(self, mode):
        return True

    def search_count(self, domain):
        return sum(matches(domain, row) for row in self.rows)


class TestBusinessSemantics(unittest.TestCase):
    def setUp(self):
        self.service = analytics.HexagonosAnalytics()
        self.filters = dict(company_id=1, date_from=date(2026, 9, 1),
                            date_to=date(2026, 9, 16), today=date(2026, 9, 16))
        self.specs = self.service._catalog(self.filters)

    def metric(self, key, rows):
        spec = self.specs[key]
        self.service.env = {spec['model']: Source(rows)}
        return self.service._metric(spec)

    def test_dates_use_monterrey_and_exclusive_end(self):
        self.assertEqual(analytics.utc_midnight(date(2026, 9, 1)), datetime(2026, 9, 1, 6))
        spec = self.specs['mo_done']
        dates = ['2026-09-01 05:59:59', '2026-09-01 06:00:00',
                 '2026-09-17 05:59:59', '2026-09-17 06:00:00']
        rows = [dict(company_id=1, state='done', date_finished=d) for d in dates]
        self.assertEqual([matches(spec['domain'], row) for row in rows], [False, True, True, False])

    def test_backlog_is_current_not_limited_to_selected_period(self):
        other = self.service._catalog(dict(self.filters, date_from=date(2026, 7, 1), date_to=date(2026, 7, 31)))
        for key in self.specs:
            if self.specs[key]['scope'] == 'actual':
                self.assertEqual(self.specs[key]['domain'], other[key]['domain'], key)

    def test_no_denominator_is_not_zero_or_perfect(self):
        for key in ['mo_on_time', 'quality_acceptance']:
            metric = self.metric(key, [])
            self.assertIsNone(metric['value'])
            self.assertEqual(metric['sample'], 0)

    def test_quality_reinspection_and_acceptance_have_correct_population(self):
        rows = [dict(**{'production_order_id.company_id': 1}, state=state,
                     date_inspection='2026-09-10 12:00:00', retention_state=retention)
                for state, retention in [('aceptado', 'none'), ('rechazado', 'none'),
                                         ('retenido', 'retenido'), ('en_proceso', 'reinspeccion'),
                                         ('borrador', 'none')]]
        self.assertEqual(self.metric('quality_acceptance', rows)['value'], 50)
        self.assertEqual(self.metric('quality_held', rows)['value'], 2)
        rows[0]['production_order_id.company_id'] = 2
        self.assertEqual(self.metric('quality_acceptance', rows)['value'], 0)

    def test_effective_line_deadline_and_partial_deliveries(self):
        base = {'company_id': 1, 'order_id.state': 'sale', 'display_type': False,
                'is_downpayment': False, 'product_id.type': 'consu',
                'qty_to_deliver_report': 2, 'report_commitment_date': '2026-09-16 05:59:59'}
        rows = [base, dict(base, report_commitment_date='2026-09-16 06:00:00'),
                dict(base, report_commitment_date=False), dict(base, qty_to_deliver_report=0),
                dict(base, **{'order_id.state': 'cancel'}), dict(base, **{'product_id.type': 'service'}),
                dict(base, is_downpayment=True), dict(base, company_id=2)]
        self.assertEqual(self.metric('delivery_pending', rows)['value'], 3)
        self.assertEqual(self.metric('delivery_late', rows)['value'], 1)
        self.assertEqual(self.metric('delivery_soon', rows)['value'], 1)
        self.assertEqual(self.metric('delivery_no_date', rows)['value'], 1)

    def test_production_deadline_result_boundary_missing_and_cancelled(self):
        deadline = datetime(2026, 9, 12, 18)
        rows = [types.SimpleNamespace(state=state, date_deadline=limit, date_finished=finished)
                for state, limit, finished in [('done', deadline, deadline),
                    ('done', deadline, datetime(2026, 9, 12, 18, 0, 1)),
                    ('done', False, deadline), ('cancel', deadline, deadline),
                    ('confirmed', deadline, deadline)]]
        production.MrpProduction._compute_hmx_deadline_result(rows)
        self.assertEqual([r.hmx_deadline_result for r in rows], ['on_time', 'late', 'no_date', 'pending', 'pending'])

    def test_every_indicator_has_a_company_boundary_and_definition(self):
        for spec in self.specs.values():
            self.assertTrue(spec['domain'][0][0].endswith('company_id'), spec['key'])
            self.assertEqual(spec['domain'][0][1:], ('=', 1))
            self.assertTrue(spec['definition'])
            if spec['denominator'] is not None:
                self.assertEqual(spec['denominator'][0], spec['domain'][0])

    def test_documentary_proof_is_not_stock_delivery(self):
        for key in ['document_pending', 'document_verified', 'document_oficios']:
            self.assertTrue(self.specs[key]['model'].startswith('delivery.evidence.'))
        self.assertEqual(self.specs['delivery_done']['model'], 'stock.picking')

    def test_every_public_endpoint_rejects_user_without_permission(self):
        self.service.env = types.SimpleNamespace(user=types.SimpleNamespace(has_group=lambda group: False))
        for method, args in [('get_options', []), ('get_dashboard', []), ('get_detail', ['mo_open'])]:
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(self.service, method)(*args)

    def test_access_failure_is_not_reported_as_zero(self):
        source = Source([])
        source.check_access_rights = Mock(side_effect=AccessError('denied'))
        self.service.env = {'mrp.production': source}
        metric = self.service._metric(self.specs['mo_open'])
        self.assertFalse(metric['available'])
        self.assertIsNone(metric['value'])

    def test_scope_rejects_unselected_company_and_invalid_ranges(self):
        self.service.env = types.SimpleNamespace(
            user=types.SimpleNamespace(has_group=lambda group: True),
            companies=types.SimpleNamespace(ids=[1]), company=types.SimpleNamespace(id=1),
        )
        self.service.with_context = Mock(return_value=self.service)
        with self.assertRaises(AccessError):
            self.service._scope({'company_id': 2})
        for filters in [{'date_from': 'invalid'}, {'date_to': '2026-09-17'},
                        {'date_from': '2026-09-15', 'date_to': '2026-09-14'},
                        {'date_from': '2025-09-15', 'date_to': '2026-09-16'}]:
            with self.subTest(filters=filters), self.assertRaises(ValidationError):
                self.service._scope(filters)
        scoped, parsed = self.service._scope({'company_id': 1})
        self.assertEqual(parsed['company_id'], 1)
        self.service.with_context.assert_called_with(allowed_company_ids=[1], tz='America/Monterrey')

    def test_group_is_opt_in_and_every_menu_is_restricted(self):
        root = Path(__file__).parents[1]
        security = ElementTree.parse(root / 'security/analytics_security.xml')
        group = security.find(".//record[@id='group_analytics_viewer']")
        self.assertIsNotNone(group)
        self.assertIsNone(group.find("field[@name='users']"))
        self.assertEqual(group.find("field[@name='implied_ids']").get('eval'), "[(4, ref('base.group_user'))]")
        menus = ElementTree.parse(root / 'views/analytics_views.xml').findall('.//menuitem')
        self.assertEqual(len(menus), 2)
        for menu in menus:
            self.assertEqual(menu.get('groups'), 'hexagonos_analytics.group_analytics_viewer')

    def test_overtime_is_validated_only(self):
        domain = self.specs['attendance_overtime']['domain']
        row = {'employee_id.company_id': 1, 'date': '2026-09-10', 'state': 'draft'}
        self.assertFalse(matches(domain, row))
        row['state'] = 'validated'
        self.assertTrue(matches(domain, row))

    def test_operational_rank_keeps_other_groups_and_exact_drill(self):
        groups = [dict(process=(i, 'Proceso %s' % i), __count=i, quantity=i / 10,
                       __domain=[('company_id', '=', 1), ('process', '=', i)]) for i in range(1, 21)]
        model = types.SimpleNamespace(check_access_rights=lambda mode: True,
            read_group=lambda *a, **kw: groups, _fields={'process': types.SimpleNamespace(type='many2one')})
        env = type('Environment', (dict,), {})({'quality.inspection': model})
        env.companies = types.SimpleNamespace(ids=[1])
        self.service.env = env
        spec = dict(model='quality.inspection', label='Inspecciones', scope='actual', domain=[('company_id', '=', 1)])
        panel = self.service._group_panel(spec, 'test', 'Procesos', 'process')
        self.assertEqual(sum(r['value'] for r in panel['rows']), 210)
        others = panel['rows'][-1]
        self.assertEqual((others['label'], others['value']), ('Otros', 15))
        self.assertTrue(matches(others['action']['domain'], dict(company_id=1, process=1)))
        self.assertFalse(matches(others['action']['domain'], dict(company_id=1, process=20)))
        self.assertFalse(matches(others['action']['domain'], dict(company_id=2, process=1)))
        # Quantities/currencies retain every group; never create a mixed-unit Others.
        amounts = self.service._group_panel(spec, 'test', 'Cantidades', 'process', measure='quantity')
        self.assertEqual(len(amounts['rows']), 20)


if __name__ == '__main__':
    unittest.main(verbosity=2)

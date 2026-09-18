"""Portable DOF regressions: real calculation methods, synthetic ORM and HTTP responses."""
import copy
import json
import sys
import types
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import check_executive as base

math = base.load('dof_math')
analytics = base.load('dof_analytics')


def payload(rows):
    return dict(messageCode=200, TotalIndicadores=len(rows), ListaIndicadores=[
        dict(codTipoIndicador=158, fecha=day, valor=value) for day, value in rows])


class CurrencyCatalog:
    def with_context(self, **context):
        return self
    def search(self, domain):
        return [types.SimpleNamespace(id=1, name='MXN'), types.SimpleNamespace(id=2, name='USD')]
    def browse(self, cid):
        return types.SimpleNamespace(name={1: 'MXN', 2: 'USD', 3: 'EUR'}[cid], decimal_places=2)


class RateProvider:
    missing = False
    def _monthly_rates(self, start, end, today):
        result = {}
        for month in base.math.months_between(start, end):
            first = date.fromisoformat(month + '-01')
            average = 20 if month == '2026-09' else 10 if month == '2026-08' else 15
            row = math.monthly_publications(payload([(first.strftime('%d-%m-%Y'), str(average - 1)),
                ((first + timedelta(days=1)).strftime('%d-%m-%Y'), str(average + 1))]),
                first, min(base.math.shift_month(first, 1) - timedelta(days=1), today), today)[month]
            row['fetched_at'] = '2026-09-16 18:00:00'
            if self.missing and month == '2026-09':
                row.update(available=False, average=None, error='DOF no disponible')
            result[month] = row
        return result


class Service(analytics.DofAnalytics, base.Service):
    def with_context(self, **context):
        result = copy.copy(self)
        result.env = copy.copy(self.env)
        result.env.context = dict(self.env.context, **context)
        return result


def service():
    original = base.fixture_service()
    result = Service()
    result.env = original.env
    result.env.context = {}
    result.env['res.currency'] = CurrencyCatalog()
    result.env['hexagonos.analytics.dof.month'] = RateProvider()
    return result


class TestDof(unittest.TestCase):
    def test_official_response_arithmetic_mean_unique_publication_dates(self):
        source = payload([('03-08-2026', '18'), ('04-08-2026', '20'), ('04-08-2026', '20')])
        row = math.monthly_publications(source, date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 16))['2026-08']
        self.assertEqual(Decimal(row['average']), Decimal(19))
        self.assertEqual(row['count'], 2)
        self.assertFalse(row['provisional'])
        self.assertEqual(row['last_publication'], '2026-08-04')
        self.assertTrue(row['source_url'].startswith('https://sidof.segob.gob.mx/'))

    def test_invalid_incomplete_conflicting_and_foreign_indicator_responses(self):
        original = payload([('03-08-2026', '18')])
        variants = [dict(original, TotalIndicadores=99), dict(original, messageCode=400)]
        for changes in [{'valor': 'NaN'}, {'valor': '-3'}, {'codTipoIndicador': 159}, {'fecha': '03-09-2026'}]:
            altered = copy.deepcopy(original); altered['ListaIndicadores'][0].update(changes); variants.append(altered)
        variants.append(payload([('03-08-2026', '18'), ('03-08-2026', '20')]))
        for source in variants:
            with self.assertRaises(math.DofRateError):
                math.monthly_publications(source, date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 16))

    def test_no_publications_never_become_a_zero_rate(self):
        row = math.monthly_publications(payload([]), date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 1))['2026-09']
        self.assertFalse(row['available']); self.assertIsNone(row['average']); self.assertTrue(row['provisional'])
        with self.assertRaises(math.DofRateError):
            math.converted_amount(10, 2, 1, 2, '2026-09', {'2026-09': row})

    def test_consolidated_sales_dimensions_and_drills_reconcile(self):
        s = service(); data = s.get_dashboard('resumen', {'currency_id': 0})
        self.assertTrue(data['fx']['applied']); self.assertEqual(data['currency'], 'MXN')
        self.assertNotIn('mixed', data)
        self.assertEqual(data['cards'][0]['current'], 350 + 800 * 20)
        self.assertEqual(data['cards'][0]['previous'], 375)
        self.assertEqual(data['cards'][0]['difference'], 15975)
        converted, _ = s._dof_context('resumen', base.FILTERS)
        for dimension in ('warehouse', 'product', 'customer', 'family'):
            rows = data['dimensions'][dimension]
            self.assertEqual(sum(row['current'] for row in rows), data['cards'][0]['current'])
            for row in rows:
                self.assertEqual(converted._sum('sale.order.line', row['action']['domain'], 'price_subtotal'), row['current'])
        self.assertEqual(data['series'][0]['rows'][0]['value'], data['cards'][0]['current'])
        self.assertEqual(data['reconciliation']['difference'], 0)
        self.assertEqual(len(data['customers']['rows']), 3)
        self.assertEqual(data['production']['rows'][0]['current'], 15)
        self.assertEqual(data['partial']['current'], data['cards'][0]['current'])

    def test_multiple_months_use_each_month_not_one_period_rate(self):
        s = service(); s.env['sale.order.line'].rows.append(base.sale(51, amount=100, currency=2, when='2026-08-05 12:00:00'))
        data = s.get_dashboard('comercial', dict(currency_id=0, date_from=date(2026, 8, 1), date_to=date(2026, 9, 16)))
        self.assertEqual(data['cards'][0]['current'], 375 + 100 * 10 + 350 + 800 * 20)
        self.assertEqual(sum(row['value'] for row in data['series'][0]['rows']), data['cards'][0]['current'])

    def test_invoices_refunds_and_payments_use_their_own_month(self):
        s = service()
        common = dict(company_id=1, currency_id=2, parent_state='posted', display_type='product', hmx_warehouse_id=(1, 'Almacén 1'))
        s.env['account.move.line'] = base.Source([
            dict(common, id=71, amount_currency=-100, hmx_invoice_date='2026-08-31', **{'move_id.move_type': 'out_invoice', 'move_id.invoice_date': '2026-08-31'}),
            dict(common, id=72, amount_currency=20, hmx_invoice_date='2026-09-01', **{'move_id.move_type': 'out_refund', 'move_id.invoice_date': '2026-09-01'}),
        ])
        s.env['account.payment'] = base.Source([dict(id=91, company_id=1, currency_id=2, state='paid', date='2026-09-02',
            amount=5, partner_type='customer', payment_type='inbound', paired_internal_transfer_payment_id=False,
            **{'move_id.state': 'posted', 'destination_account_id.account_type': 'asset_receivable'})])
        data = s.get_dashboard('comercial', dict(currency_id=0, date_from=date(2026, 8, 1)))
        self.assertEqual(data['cards'][1]['current'], 100 * 10 - 20 * 20)
        self.assertEqual(data['cards'][2]['current'], 5 * 20)
        self.assertEqual(sum(row['current'] for row in data['invoice_warehouses']), 600)

    def test_missing_rate_falls_back_to_original_currencies_with_reason(self):
        s = service(); s.env['hexagonos.analytics.dof.month'].missing = True
        data = s.get_dashboard('resumen', {'currency_id': 0})
        self.assertTrue(data['mixed']); self.assertFalse(data['fx']['applied'])
        self.assertIn('2026-09', data['fx']['error'])
        self.assertEqual(data['currency_blocks'][1]['cards'][0]['current'], 800)

    def test_currency_filters_permissions_and_forged_context(self):
        s = service(); s.env.context = {'hmx_dof_conversion': {'rates': {}, 'currency_ids': [999]}}
        data = s.get_dashboard('resumen', {'currency_id': 1})
        self.assertEqual(data['cards'][0]['current'], 350); self.assertNotIn('fx', data)
        data = s.get_dashboard('resumen', {'currency_id': 0, 'warehouse_ids': [2]})
        self.assertEqual(data['cards'][0]['current'], 200)
        self.assertFalse(data['cards'][2]['available'])
        s.env['sale.order.line'].denied = True
        data = s.get_dashboard('resumen', {'currency_id': 0})
        self.assertFalse(data['commercial_available']); self.assertEqual(data['cards'], [])

    def test_other_currencies_excluded_and_disclosed_and_history_is_global(self):
        s = service(); s.env['sale.order.line'].rows.append(base.sale(66, amount=9999, currency=3))
        data = s.get_dashboard('clientes', {'currency_id': 0})
        self.assertEqual(data['cards'][0]['current'], 16350)
        self.assertEqual(data['fx']['excluded'], ['EUR'])
        self.assertEqual(next(row['status'] for row in data['customers']['rows'] if row['id'] == 3), 'reactivated')

    def test_utc_month_boundary_and_family_quantities(self):
        s = service(); s.env['sale.order.line'].rows.append(base.sale(90, amount=10, currency=2, when='2026-09-01 05:59:59'))
        data = s.get_dashboard('productos', dict(currency_id=0, date_from=date(2026, 8, 1)))
        self.assertEqual(data['cards'][0]['current'], 375 + 10 * 10 + 16350)
        self.assertEqual(sum(row['current'] for row in data['family_volume']), sum(row['current'] for row in data['volume']))

    def test_purchase_approval_month_conversion_and_original_document_scope(self):
        from operational_fixtures import fixtures
        s = service()
        s.env['purchase.order'] = base.Source([
            dict(id=1, company_id=1, state='purchase', currency_id=1, amount_untaxed=500, date_approve='2026-09-05 12:00:00'),
            dict(id=2, company_id=1, state='purchase', currency_id=2, amount_untaxed=100, date_approve='2026-08-05 12:00:00'),
            dict(id=3, company_id=1, state='purchase', currency_id=2, amount_untaxed=50, date_approve='2026-09-05 12:00:00'),
            dict(id=4, company_id=1, state='purchase', currency_id=3, amount_untaxed=9999, date_approve='2026-09-05 12:00:00'),
        ])
        original = fixtures()['compras']
        with patch.object(base.legacy.analytics.HexagonosAnalytics, 'get_dashboard', return_value=copy.deepcopy(original)):
            data = s.get_dashboard('compras', dict(currency_id=0, date_from=date(2026, 8, 1)))
        panel = next(row for row in data['panels'] if row['key'] == 'purchase_amount')
        self.assertTrue(data['fx']['applied']); self.assertEqual(panel['rows'][0]['value'], 2500)
        self.assertEqual(panel['rows'][0]['label'], 'MXN')
        self.assertEqual(s.env['purchase.order'].search_count(panel['rows'][0]['action']['domain']), 3)
        self.assertEqual(data['metrics'], original['metrics'])


def export(path):
    data = json.loads(Path(path).read_text()); s = service()
    data['consolidated'] = {tab: s.get_dashboard(tab, {'currency_id': 0}) for tab, _ in base.executive.EXEC_TABS}
    Path(path).write_text(json.dumps(data, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        export(sys.argv[1])
    else:
        unittest.main(verbosity=2)

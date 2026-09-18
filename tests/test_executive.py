"""Integration regressions for an installed Odoo 18 disposable database."""
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged
from ..models.analytics import utc_midnight


@tagged('post_install', '-at_install')
class TestExecutiveAnalytics(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.service = cls.env['hexagonos.analytics'].with_context(allowed_company_ids=[cls.company.id])
        cls.env.user.groups_id = [Command.link(cls.env.ref('hexagonos_analytics.group_analytics_viewer').id),
                                 Command.link(cls.env.ref('control_costos_hexagonos.group_view_sale_total').id)]
        cls.partner = cls.env['res.partner'].create({'name': 'Analytics QA customer'})
        cls.product = cls.env['product.product'].create({'name': 'Analytics QA product', 'type': 'consu'})
        cls.warehouse_a = cls.env['stock.warehouse'].create({'name': 'Analytics QA A', 'code': 'HQA', 'company_id': cls.company.id})
        cls.warehouse_b = cls.env['stock.warehouse'].create({'name': 'Analytics QA B', 'code': 'HQB', 'company_id': cls.company.id})
        cls.today = fields.Date.context_today(cls.service.with_context(tz='America/Monterrey'))
        cls.filters = {'date_from': str(cls.today.replace(day=1)), 'date_to': str(cls.today),
                       'currency_id': cls.company.currency_id.id, 'customer_id': cls.partner.id}
        cls.orders = cls.env['sale.order']
        # Direct state assignment isolates analytics from delivery / custom confirmation side effects.
        for wh, price in [(cls.warehouse_a, 100), (cls.warehouse_b, 200)]:
            cls.orders |= cls.env['sale.order'].create({'partner_id': cls.partner.id,
                'company_id': cls.company.id, 'warehouse_id': wh.id,
                'state': 'sale', 'date_order': utc_midnight(cls.today) + timedelta(hours=12),
                'order_line': [Command.create({'product_id': cls.product.id, 'name': cls.product.name,
                    'product_uom_qty': 2, 'product_uom': cls.product.uom_id.id,
                    'price_unit': price, 'discount': 10, 'tax_id': [Command.clear()]})]})
        cls.service.flush_model()

    def test_filter_options_with_broken_product_search_read(self):
        # A third-party override may still forward specification to _read_format.
        # Analytics must use the supported ORM path for both queries and saved IDs.
        with patch.object(type(self.product), 'search_read', side_effect=TypeError(
                "_read_format() got an unexpected keyword argument 'specification'")):
            options = self.service.get_options()
            self.assertIn('products', options)
            result = self.service.get_filter_options('product', self.product.name)
            self.assertIn(self.product.id, [row['id'] for row in result])
            result = self.service.get_filter_options('product', 'no-match-analytics-qa', self.product.id)
            self.assertEqual([row['id'] for row in result], [self.product.id])

    def test_stored_dimensions_and_native_read_group(self):
        service, parsed = self.service._scope(self.filters)
        rows = service._dimension(parsed, 'warehouse')
        self.assertAlmostEqual(sum(r['current'] for r in rows), 540)
        self.assertEqual({r['id'] for r in rows}, {self.warehouse_a.id, self.warehouse_b.id})
        for row in rows:
            lines = self.env['sale.order.line'].search(row['action']['domain'])
            self.assertAlmostEqual(sum(lines.mapped('price_subtotal')), row['current'])
        series = service._series(parsed)
        self.assertAlmostEqual(sum(r['value'] for r in series[0]['rows']), 540)

    def test_dof_native_month_currency_grouping(self):
        mxn = self.env.ref('base.MXN')
        usd = self.env.ref('base.USD')
        mxn.active = usd.active = True
        for order, currency in zip(self.orders, (mxn, usd)):
            pricelist = self.env['product.pricelist'].create({'name': 'QA DOF ' + currency.name, 'currency_id': currency.id})
            order.pricelist_id = pricelist
        self.env.flush_all()
        month = self.today.strftime('%Y-%m')
        rates = {month: dict(month=month, available=True, average='20', count=1,
            selected=True, provisional=True, publications=[dict(date=str(self.today), value='20')],
            through=str(self.today), fetched_at='2026-09-16 18:00:00', first_publication=str(self.today),
            last_publication=str(self.today), public_url='https://www.dof.gob.mx/', error='')}
        with patch.object(type(self.env['hexagonos.analytics.dof.month']), '_monthly_rates', return_value=rates):
            data = self.service.get_dashboard('comercial', dict(self.filters, currency_id=0))
        self.assertTrue(data['fx']['applied'])
        self.assertAlmostEqual(data['cards'][0]['current'], 180 + 360 * 20)
        self.assertAlmostEqual(sum(row['current'] for row in data['dimensions']['warehouse']), 7380)
        self.assertAlmostEqual(sum(row['value'] for row in data['series'][0]['rows']), 7380)

    def test_multi_warehouse_unassigned_and_currency_scope(self):
        all_data = self.service.get_dashboard('comercial', self.filters)
        self.assertAlmostEqual(all_data['cards'][0]['current'], 540)
        for wh, expected in [(self.warehouse_a, 180), (self.warehouse_b, 360)]:
            data = self.service.get_dashboard('comercial', dict(self.filters, warehouse_ids=[wh.id]))
            self.assertAlmostEqual(data['cards'][0]['current'], expected)
            self.assertEqual(data['reconciliation']['difference'], 0)
            self.assertFalse(data['cards'][2]['available'])
        data = self.service.get_dashboard('comercial', dict(self.filters, warehouse_ids=[0]))
        self.assertAlmostEqual(data['cards'][0]['current'], 0)

    def test_invoice_attribution_unique_missing_and_ambiguous(self):
        # New transient invoice lines exercise the compute using real relational fields.
        Line = self.env['account.move.line']
        first, second = self.orders.mapped('order_line')
        rows = [Line.new({'sale_line_ids': [Command.set(ids)]}) for ids in [[first.id], [], [first.id, second.id]]]
        for row in rows:
            row._compute_hmx_warehouse()
        self.assertEqual(rows[0].hmx_warehouse_id, self.warehouse_a)
        self.assertFalse(rows[1].hmx_warehouse_id)
        self.assertFalse(rows[2].hmx_warehouse_id)

    def test_full_history_prevents_false_new_client(self):
        old = self.orders[0].copy({'date_order': fields.Datetime.now() - timedelta(days=200), 'state': 'sale'})
        service, parsed = self.service._scope(self.filters)
        rows = service._customer_analysis(parsed, service._dimension(parsed, 'customer'))['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'reactivated')
        self.assertIn(old, self.env['sale.order'].search(rows[0]['history_action']['domain']))

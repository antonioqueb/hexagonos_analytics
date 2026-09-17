"""Run with Odoo 18, --test-tags /hexagonos_analytics on a disposable database."""
import json
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestHexagonosAnalytics(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group = cls.env.ref('hexagonos_analytics.group_analytics_viewer')
        cls.company = cls.env.company
        cls.plain = new_test_user(cls.env, login='hmx_analytics_plain', groups='base.group_user')
        cls.viewer = new_test_user(
            cls.env, login='hmx_analytics_viewer',
            groups='base.group_user,hexagonos_analytics.group_analytics_viewer',
        )
        cls.manager = new_test_user(
            cls.env, login='hmx_analytics_manager',
            groups='base.group_user,hexagonos_analytics.group_analytics_viewer,'
                   'mrp.group_mrp_manager,stock.group_stock_manager,sales_team.group_sale_manager,'
                   'purchase.group_purchase_manager,quality_management.group_quality_manager,'
                   'entregas_evidencias.group_delivery_evidence_admin,'
                   'empleados_hmx.group_hmx_attendance_manager',
        )
        cls.today = fields.Date.context_today(cls.env['hexagonos.analytics'].with_context(tz='America/Monterrey'))
        cls.filters = {'date_from': str(cls.today.replace(day=1)), 'date_to': str(cls.today),
                       'company_id': cls.company.id}

    def service(self, user):
        return self.env['hexagonos.analytics'].with_user(user).with_context(allowed_company_ids=[self.company.id])

    def test_all_entrypoints_require_explicit_permission(self):
        service = self.service(self.plain)
        for method, args in [
            ('get_options', []), ('get_dashboard', ['resumen', self.filters]),
            ('get_detail', ['mo_open', self.filters]),
            ('get_filter_options', ['customer']),
        ]:
            with self.subTest(method=method), self.assertRaises(AccessError):
                getattr(service, method)(*args)

    def test_menu_hidden_without_permission_and_revocation(self):
        root = self.env.ref('hexagonos_analytics.menu_analytics_root')
        child = self.env.ref('hexagonos_analytics.menu_analytics_dashboard')
        visible = self.env['ir.ui.menu'].with_user(self.plain)._visible_menu_ids()
        self.assertNotIn(root.id, visible)
        self.assertNotIn(child.id, visible)
        visible = self.env['ir.ui.menu'].with_user(self.viewer)._visible_menu_ids()
        self.assertIn(root.id, visible)
        self.assertIn(child.id, visible)
        self.viewer.write({'groups_id': [Command.unlink(self.group.id)]})
        with self.assertRaises(AccessError):
            self.service(self.viewer).get_options()
        self.assertNotIn(root.id, self.env['ir.ui.menu'].with_user(self.viewer)._visible_menu_ids())

    def test_permission_does_not_grant_operational_roles(self):
        for xmlid in ['mrp.group_mrp_manager', 'purchase.group_purchase_manager',
                      'quality_management.group_quality_manager',
                      'control_costos_hexagonos.group_cost_admin']:
            self.assertFalse(self.viewer.has_group(xmlid))
        self.assertEqual(self.group.implied_ids, self.env.ref('base.group_user'))

    def test_every_dashboard_and_drill_is_serializable(self):
        service = self.service(self.manager)
        for tab in service.get_options()['tabs']:
            with self.subTest(tab=tab['key']):
                payload = service.get_dashboard(tab['key'], self.filters)
                json.dumps(payload)
                self.assertTrue(payload.get('executive') or payload['metrics'])
                for metric in payload.get('metrics', []):
                    if metric['available']:
                        detail = service.get_detail(metric['key'], self.filters)
                        json.dumps(detail)
                        self.assertLessEqual(len(detail['rows']), 20)

    def test_all_domains_and_group_fields_resolve(self):
        service, parsed = self.service(self.manager)._scope(self.filters)
        for spec in service._catalog(parsed).values():
            with self.subTest(metric=spec['key']):
                model = service.env[spec['model']]
                model.search_count(spec['domain'])
                self.assertIn(spec['date_field'], model._fields)
                if spec['denominator'] is not None:
                    model.search_count(spec['denominator'])

    def test_cost_amounts_require_separate_permission(self):
        service = self.service(self.manager)
        xmlid = 'control_costos_hexagonos.group_view_sale_total'
        self.manager.write({'groups_id': [Command.unlink(self.env.ref(xmlid).id)]})
        payload = service.get_dashboard('comercial', self.filters)
        self.assertFalse(payload['commercial_available'])
        self.assertEqual(payload['cards'], [])
        self.manager.write({'groups_id': [Command.link(self.env.ref(xmlid).id)]})
        self.assertTrue(service.get_dashboard('comercial', self.filters)['commercial_available'])
        for tab, key, xmlid in [('compras', 'purchase_amount', 'control_costos_hexagonos.group_view_purchase_total')]:
            self.manager.write({'groups_id': [Command.unlink(self.env.ref(xmlid).id)]})
            panel = next(p for p in service.get_dashboard(tab, self.filters)['panels'] if p['key'] == key)
            self.assertFalse(panel['available'])
            self.assertEqual(panel['rows'], [])
            self.manager.write({'groups_id': [Command.link(self.env.ref(xmlid).id)]})
            panel = next(p for p in service.get_dashboard(tab, self.filters)['panels'] if p['key'] == key)
            self.assertTrue(panel['available'])

    def test_selected_company_cannot_escape_session(self):
        other = self.env['res.company'].create({'name': 'Analytics isolation test'})
        for method, args in [('get_dashboard', ['resumen']), ('get_detail', ['mo_open'])]:
            with self.assertRaises(AccessError):
                getattr(self.service(self.viewer), method)(*args, dict(self.filters, company_id=other.id))

    def test_invalid_periods_rejected(self):
        service = self.service(self.viewer)
        invalid = [
            {'date_from': 'not-a-date'},
            {'date_from': str(self.today + timedelta(days=1))},
            {'date_to': str(self.today + timedelta(days=1))},
            {'date_from': str(self.today - timedelta(days=366))},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                service.get_dashboard('resumen', dict(self.filters, **values))

    def test_record_rule_applies_to_count_chart_and_drill(self):
        # A global test-only rule excludes all production records for this user.
        model = self.env.ref('mrp.model_mrp_production')
        self.env['ir.rule'].create({
            'name': 'Analytics test deny production', 'model_id': model.id,
            'domain_force': "[('id', '=', 0)] if user.id == %s else []" % self.manager.id,
        })
        service = self.service(self.manager)
        dashboard = service.get_dashboard('produccion', self.filters)
        self.assertEqual(dashboard['production']['status'], [])
        self.assertEqual(service.get_detail('mo_open', self.filters)['rows'], [])
        self.assertEqual(dashboard['production']['compliance'], [])

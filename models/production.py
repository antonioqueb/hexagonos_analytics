from odoo import api, fields, models


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    hmx_deadline_result = fields.Selection(
        [('pending', 'Abierta / cancelada'), ('no_date', 'Sin fecha límite'),
         ('on_time', 'Terminada a tiempo'), ('late', 'Terminada tarde')],
        string='Cumplimiento de fecha límite', compute='_compute_hmx_deadline_result',
        store=True, index=True,
        groups='hexagonos_analytics.group_analytics_viewer',
        help='Compara el cierre real con la fecha límite vigente. No es una línea base histórica.',
    )

    @api.depends('state', 'date_finished', 'date_deadline')
    def _compute_hmx_deadline_result(self):
        for production in self:
            if production.state != 'done':
                result = 'pending'
            elif not production.date_deadline or not production.date_finished:
                result = 'no_date'
            else:
                result = 'on_time' if production.date_finished <= production.date_deadline else 'late'
            production.hmx_deadline_result = result

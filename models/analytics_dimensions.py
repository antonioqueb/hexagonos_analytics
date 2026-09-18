"""Stored dimensions for ORM aggregates; never joins monetary facts to deliveries."""
from odoo import api, fields, models
from .analytics_math import unique_assignment


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    hmx_customer_id = fields.Many2one(related='partner_id.commercial_partner_id', store=True, index=True)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    hmx_warehouse_id = fields.Many2one(related='order_id.warehouse_id', store=True, index=True)
    hmx_customer_id = fields.Many2one(related='order_id.partner_id.commercial_partner_id', store=True, index=True)
    hmx_seller_id = fields.Many2one(related='order_id.user_id', store=True, index=True)
    hmx_family_id = fields.Many2one(related='product_id.categ_id', store=True, index=True)
    hmx_date = fields.Datetime(related='order_id.date_order', store=True, index=True)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    hmx_invoice_date = fields.Date(related='move_id.invoice_date', store=True, index=True)

    hmx_warehouse_id = fields.Many2one(
        'stock.warehouse', compute='_compute_hmx_warehouse', store=True, index=True,
        help='Almacén comercial único de las líneas de venta enlazadas. Sin enlace o con varios: Sin asignar.')

    @api.depends('sale_line_ids', 'sale_line_ids.order_id.warehouse_id')
    def _compute_hmx_warehouse(self):
        for line in self:
            line.hmx_warehouse_id = unique_assignment(
                line.sale_line_ids.mapped(lambda sale: sale.order_id.warehouse_id.id or False))


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    hmx_warehouse_id = fields.Many2one(related='picking_type_id.warehouse_id', store=True, index=True)


class StockMove(models.Model):
    _inherit = 'stock.move'

    hmx_production_warehouse_id = fields.Many2one(
        related='production_id.picking_type_id.warehouse_id', store=True, index=True)
    hmx_family_id = fields.Many2one(related='product_id.categ_id', store=True, index=True)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    hmx_dispatch_warehouse_id = fields.Many2one(
        'stock.warehouse', compute='_compute_hmx_dispatch', store=True, index=True,
        help='Almacén único de las ubicaciones origen de movimientos realizados hacia cliente. Sin evidencia o varios: Sin asignar.')

    @api.depends('state', 'move_line_ids.state', 'move_line_ids.quantity',
                 'move_line_ids.location_id.warehouse_id', 'move_line_ids.location_dest_id.usage')
    def _compute_hmx_dispatch(self):
        for picking in self:
            lines = picking.move_line_ids.filtered(
                lambda line: line.state == 'done' and line.quantity > 0 and line.location_dest_id.usage == 'customer')
            picking.hmx_dispatch_warehouse_id = unique_assignment(
                lines.mapped(lambda line: line.location_id.warehouse_id.id or False))

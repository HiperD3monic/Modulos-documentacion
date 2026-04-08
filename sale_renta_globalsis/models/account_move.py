from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    is_rental_invoice = fields.Boolean(
        string="Es factura de alquiler",
        compute='_compute_is_rental_invoice',
        store=False,
    )

    @api.depends('line_ids.sale_line_ids')
    def _compute_is_rental_invoice(self):
        for move in self:
            sale_orders = move.line_ids.sale_line_ids.order_id
            move.is_rental_invoice = any(so.is_rental_order for so in sale_orders)

from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    rental_days = fields.Integer(
        string="Días de renta",
        related='order_id.rental_days',
    )

    @api.depends('order_id.rental_days')
    def _compute_amount(self):
        """Recompute amounts when rental_days changes on the parent order.

        Uses dotted path 'order_id.rental_days' to watch the order field
        directly, without a stored related field that could cause
        infinite recomputation loops.
        """
        return super()._compute_amount()

    def _prepare_base_line_for_taxes_computation(self, **kwargs):
        """Override to multiply quantity by rental_days for subtotal calculation.

        The base Odoo method uses product_uom_qty as quantity.
        We override it so the effective quantity becomes:
            product_uom_qty × rental_days

        This makes the subtotal = price_unit × qty × rental_days × (1 - discount/100)
        which is exactly the rental calculation the client needs.
        """
        res = super()._prepare_base_line_for_taxes_computation(**kwargs)
        rental_days = self.order_id.rental_days or 1
        if rental_days > 1:
            res['quantity'] = self.product_uom_qty * rental_days
        return res

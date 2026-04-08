from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    rental_days = fields.Integer(
        string="Periodo de renta",
        default=1,
        help="Número de días del periodo de renta. "
             "El importe de cada línea se calcula como: "
             "Cantidad × Días de renta × Precio unitario por día.",
    )

    @api.depends('rental_days')
    def _compute_amount(self):
        """Recompute amounts when rental_days changes on the line."""
        return super()._compute_amount()

    def _prepare_invoice_line(self, **optional_values):
        """Override to include rental_days in the invoice line quantity.

        The invoice line quantity becomes: qty_to_invoice × rental_days
        so the invoice subtotal correctly reflects:
            price_unit × qty × rental_days × (1 - discount/100)
        """
        res = super()._prepare_invoice_line(**optional_values)
        if self.order_id.is_rental_order and not self.display_type:
            rental_days = self.rental_days or 1
            if rental_days > 1:
                res['quantity'] = res.get('quantity', self.qty_to_invoice) * rental_days
        return res

    def _prepare_base_line_for_taxes_computation(self, **kwargs):
        """Override to multiply quantity by rental_days for subtotal calculation.

        Only applies to rental orders (is_rental_order = True).
        Subscriptions and regular sales use standard quantity.

        This makes the subtotal = price_unit × qty × rental_days × (1 - discount/100)
        which is exactly the rental calculation the client needs.
        """
        res = super()._prepare_base_line_for_taxes_computation(**kwargs)
        if self.order_id.is_rental_order:
            rental_days = self.rental_days or 1
            if rental_days > 1:
                res['quantity'] = self.product_uom_qty * rental_days
        return res

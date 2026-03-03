from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    rental_days = fields.Integer(
        string="Días de renta",
        default=1,
        help="Número de días del período de renta. "
             "El importe de cada línea se calcula como: "
             "Cantidad × Días de renta × Precio unitario por día.",
    )
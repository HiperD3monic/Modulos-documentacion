from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    rental_days = fields.Integer(
        string="Periodo de renta",
        default=1,
        help="Número de días del período de renta. "
             "Valor por defecto que se asigna a las nuevas líneas.",
    )
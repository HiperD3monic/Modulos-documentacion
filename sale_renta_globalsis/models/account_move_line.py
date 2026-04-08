from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    rental_days = fields.Integer(
        string="Periodo de renta",
        compute='_compute_rental_days',
        store=False,
    )

    @api.depends('sale_line_ids.rental_days')
    def _compute_rental_days(self):
        for line in self:
            sale_lines = line.sale_line_ids
            if sale_lines:
                # Take the rental_days from the first linked sale order line
                line.rental_days = sale_lines[0].rental_days
            else:
                line.rental_days = 0

# -*- coding: utf-8 -*-
# Wizard para crear Orden de Manufactura desde el pronóstico

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ForecastCreateMO(models.TransientModel):
    """
    Wizard que permite crear una Orden de Manufactura
    con los datos prellenados desde el pronóstico.
    """
    _name = 'production.forecast.create.mo'
    _description = 'Crear Orden de Manufactura desde Pronóstico'

    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
        help='Producto a fabricar',
    )
    bom_id = fields.Many2one(
        'mrp.bom',
        string='Lista de Materiales',
        required=True,
        help='BoM a utilizar para la fabricación',
    )
    qty = fields.Float(
        string='Cantidad a Fabricar',
        required=True,
        default=1.0,
        help='Cantidad sugerida basada en el pronóstico. Puede ajustarla.',
    )

    def action_create(self):
        """Crea la orden de manufactura y abre el formulario."""
        self.ensure_one()
        if self.qty <= 0:
            raise UserError(_('La cantidad a fabricar debe ser mayor a 0.'))

        mo = self.env['mrp.production'].create({
            'product_id': self.product_id.id,
            'bom_id': self.bom_id.id,
            'product_qty': self.qty,
            'product_uom_id': self.product_id.uom_id.id,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.production',
            'res_id': mo.id,
            'views': [[False, 'form']],
            'target': 'current',
            'name': _('Orden de Manufactura'),
        }

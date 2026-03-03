# coding: utf-8
# ============================================================================
# MODELO EXTENDIDO: account.move
# ============================================================================
# Extensión del modelo de Asientos Contables para agregar automáticamente
# números de pedimento aduanal a las líneas de factura al publicar.
#
# Cuando se publica una factura, busca el número de pedimento a través de:
#   1. Los movimientos de stock originales → costos en destino validados
#   2. Si no encuentra, busca en líneas de factura previas del mismo producto
# ============================================================================

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    """
    Extensión de account.move para integración con pedimentos aduanales.

    Al publicar una factura de venta, busca automáticamente los números
    de pedimento asociados a los productos a través de los costos en destino
    (landed costs) y los asigna a las líneas de factura para cumplimiento
    fiscal mexicano.
    """

    _inherit = 'account.move'

    def _post(self, soft=True):
        """
        Override de la publicación para asignar números de pedimento.

        Para cada línea de factura sin número de pedimento:
        1. Busca movimientos de stock asociados (ventas)
        2. Busca costos en destino (landed costs) validados con los pickings
           de los movimientos FIFO de origen
        3. Si encuentra, asigna el número de pedimento y la fecha
        4. Si no, busca en líneas de factura previas del mismo producto

        Args:
            soft (bool): Si True, validación suave (por defecto).

        Returns:
            El resultado del super()._post(soft).
        """
        AccountMoveLine = self.env['account.move.line']
        StockLandedCost = self.env['stock.landed.cost']

        for move in self.filtered(lambda move: move.is_invoice()):
            for line in move.line_ids:
                # Omitir líneas que ya tienen número de pedimento
                if line.l10n_mx_edi_customs_number:
                    continue

                # ========== BÚSQUEDA VÍA MOVIMIENTOS DE STOCK ==========
                # Buscar movimientos de stock completados asociados a ventas
                stock_moves = line.mapped(
                    'sale_line_ids.move_ids'
                ).filtered(
                    lambda r: r.state == 'done' and not r.scrapped
                )

                if not stock_moves and line and line[0].move_id.pos_order_ids:
                    stock_moves = line.move_id.mapped(
                    'pos_order_ids.picking_ids.move_ids'
                    ).filtered(
                        lambda r: r.state == 'done' and not r.scrapped
                    )
                    

                landed_costs = False
                if stock_moves:
                    # Buscar costos en destino validados que contengan los
                    # pickings de los movimientos FIFO de origen
                    landed_costs = StockLandedCost.sudo().search([
                        ('picking_ids', 'in',
                         stock_moves.mapped('move_orig_fifo_ids.picking_id').ids),
                        ('l10n_mx_edi_customs_number', '!=', False),
                        ('state', '=', 'done'),
                    ])

                if landed_costs:
                    # Extraer números de pedimento sin duplicados
                    customs_numbers = ','.join({
                        lc.l10n_mx_edi_customs_number
                        for lc in landed_costs
                    })
                    line.l10n_mx_edi_customs_number = customs_numbers

                    _logger.info(
                        "Pedimento asignado a línea de factura: %s → %s",
                        line.display_name, customs_numbers,
                    )

                else:

                    # ========== BÚSQUEDA EN FACTURAS PREVIAS ==========
                    # Si no se encontró por stock, buscar en líneas de factura
                    # previas del mismo producto
                    customs_lines = AccountMoveLine.search([
                        ('product_id', '=', line.product_id.id),
                        ('l10n_mx_edi_customs_number', '!=', False),
                        ('move_id.state', '=', 'posted'),
                    ], order='invoice_date DESC')

                    if customs_lines:
                        line.l10n_mx_edi_customs_number = (
                            customs_lines[0].l10n_mx_edi_customs_number
                        )



        return super()._post(soft)

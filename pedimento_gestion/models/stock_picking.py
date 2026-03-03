# coding: utf-8
# ============================================================================
# MODELO EXTENDIDO: stock.picking
# ============================================================================
# Extensión del modelo de Transferencias para integración con pedimentos.
# Maneja automáticamente la adición y eliminación de transferencias en
# costos en destino cuando se crean, cancelan o eliminan.
# ============================================================================

import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    """
    Extensión de stock.picking para integración automática con pedimentos.

    Cuando se crea una transferencia desde una orden de compra con pedimento,
    se agrega automáticamente al costo en destino. Cuando se cancela o
    elimina, se remueve (solo si el pedimento está en borrador).
    """

    _inherit = 'stock.picking'

    # ========== CREACIÓN ==========

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override para agregar automáticamente la transferencia al costo en
        destino cuando se crea desde una orden de compra con pedimento.

        Args:
            vals_list: Lista de diccionarios con valores para crear.

        Returns:
            Recordset con las transferencias creadas.
        """
        pickings = super().create(vals_list)

        for picking in pickings:
            self._add_to_landed_cost(picking)

        return pickings

    # ========== GESTIÓN DE COSTOS EN DESTINO ==========

    def _add_to_landed_cost(self, picking):
        """
        Agrega una transferencia al costo en destino de su orden de compra.

        Busca la orden de compra relacionada a través de los movimientos
        de la transferencia y, si la OC tiene un pedimento asociado, agrega
        esta transferencia al costo en destino.

        Args:
            picking: Registro stock.picking a agregar.
        """
        # Buscar la orden de compra desde los movimientos
        purchase_order = False
        if picking.move_ids_without_package:
            for move in picking.move_ids_without_package:
                if move.purchase_line_id and move.purchase_line_id.order_id:
                    purchase_order = move.purchase_line_id.order_id
                    break

        if not purchase_order:
            return

        # Agregar al pedimento si existe y no está ya incluida
        if (purchase_order.pedimiento_id
                and picking.id not in purchase_order.pedimiento_id.picking_ids.ids):
            purchase_order.pedimiento_id.write({
                'picking_ids': [(4, picking.id)]
            })

    def _remove_from_landed_cost(self, picking):
        """
        Remueve una transferencia del costo en destino, solo si el
        pedimento está en estado borrador.

        Una vez validado o cancelado, las transferencias deben permanecer
        para trazabilidad.

        Args:
            picking: Registro stock.picking a remover.
        """
        purchase_order = False
        if picking.move_ids_without_package:
            for move in picking.move_ids_without_package:
                if move.purchase_line_id and move.purchase_line_id.order_id:
                    purchase_order = move.purchase_line_id.order_id
                    break

        if not purchase_order:
            return

        # Solo remover si el pedimento está en borrador
        if (purchase_order.pedimiento_id
                and purchase_order.pedimiento_id.state == 'draft'):
            if picking.id in purchase_order.pedimiento_id.picking_ids.ids:
                purchase_order.pedimiento_id.write({
                    'picking_ids': [(3, picking.id)]
                })

    # ========== CANCELACIÓN Y ELIMINACIÓN ==========

    def action_cancel(self):
        """
        Override para remover la transferencia del costo en destino
        al cancelarla (solo si el pedimento no está validado).
        """
        for picking in self:
            self._remove_from_landed_cost(picking)
        return super().action_cancel()

    def unlink(self):
        """
        Override para remover la transferencia del costo en destino
        antes de eliminarla (solo si el pedimento no está validado).
        """
        for picking in self:
            self._remove_from_landed_cost(picking)
        return super().unlink()

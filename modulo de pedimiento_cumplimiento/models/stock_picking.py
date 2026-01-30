from odoo import api, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.model_create_multi
    def create(self, vals_list):
        """Override to automatically add picking to landed cost when created from a purchase order."""
        pickings = super().create(vals_list)
        
        for picking in pickings:
            self._add_to_landed_cost(picking)
        
        return pickings

    def _add_to_landed_cost(self, picking):
        """Add a picking to the related purchase order's landed cost."""
        # Get the purchase order from the picking's moves
        purchase_order = False
        if picking.move_ids_without_package:
            for move in picking.move_ids_without_package:
                if move.purchase_line_id and move.purchase_line_id.order_id:
                    purchase_order = move.purchase_line_id.order_id
                    break
        
        if not purchase_order:
            return
        
        # If the purchase order has a pedimiento (landed cost), add this picking to it
        if purchase_order.pedimiento_id and picking.id not in purchase_order.pedimiento_id.picking_ids.ids:
            purchase_order.pedimiento_id.write({
                'picking_ids': [(4, picking.id)]
            })

    def _remove_from_landed_cost(self, picking):
        """Remove a picking from landed cost only if pedimento is in 'draft' state.
        
        Once validated or cancelled, pickings should remain for traceability.
        """
        # Get the purchase order from the picking's moves
        purchase_order = False
        if picking.move_ids_without_package:
            for move in picking.move_ids_without_package:
                if move.purchase_line_id and move.purchase_line_id.order_id:
                    purchase_order = move.purchase_line_id.order_id
                    break
        
        if not purchase_order:
            return
        
        # Only remove picking if pedimento is still in draft (not validated or cancelled)
        if purchase_order.pedimiento_id and purchase_order.pedimiento_id.state == 'draft':
            if picking.id in purchase_order.pedimiento_id.picking_ids.ids:
                purchase_order.pedimiento_id.write({
                    'picking_ids': [(3, picking.id)]  # Remove picking from landed cost
                })

    def action_cancel(self):
        """Override to remove picking from landed cost when cancelled (if landed cost not validated)."""
        for picking in self:
            self._remove_from_landed_cost(picking)
        
        return super().action_cancel()

    def unlink(self):
        """Override to remove picking from landed cost before deletion (if landed cost not validated)."""
        for picking in self:
            self._remove_from_landed_cost(picking)
        
        return super().unlink()

    def action_revert_pedimento(self):
        """Call reversion logic on the associated purchase order."""
        self.ensure_one()
        
        # Find related purchase order
        po = self.purchase_id
        if not po and self.move_ids:
             for move in self.move_ids:
                 if move.purchase_line_id and move.purchase_line_id.order_id:
                     po = move.purchase_line_id.order_id
                     break
        
        if not po:
             from odoo.exceptions import ValidationError
             from odoo import _
             raise ValidationError(_("This transfer is not associated with a Purchase Order."))
             
        # Call safe revert logic on PO
        return po.action_revert_pedimento()
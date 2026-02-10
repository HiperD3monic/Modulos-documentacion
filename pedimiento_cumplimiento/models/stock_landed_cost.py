# coding: utf-8
import re
from odoo import api, models

# Pattern for Mexican customs numbers (pedimentos): YY  AA  PPPP  NNNNNNN
# Same pattern used in l10n_mx_edi_extended
CUSTOM_NUMBERS_PATTERN = re.compile(r'[0-9]{2}  [0-9]{2}  [0-9]{4}  [0-9]{7}')


class StockLandedCost(models.Model):
    _inherit = 'stock.landed.cost'

    def _auto_init(self):
        """Drop the unique constraint on l10n_mx_edi_customs_number during model initialization.
        
        This runs every time the module is loaded/updated and ensures the constraint
        is removed from the database, allowing multiple landed costs with the same customs number.
        """
        # Drop the constraint before parent _auto_init runs
        self.env.cr.execute("""
            ALTER TABLE stock_landed_cost 
            DROP CONSTRAINT IF EXISTS stock_landed_cost_l10n_mx_edi_customs_number;
        """)
        return super()._auto_init()

    @api.constrains('l10n_mx_edi_customs_number', 'state')
    def _check_l10n_mx_edi_customs_number(self):
        """Override to skip the uniqueness validation from l10n_mx_edi_landing.
        
        We allow multiple landed costs with the same customs number as long as
        they are for the same vendor. The validation is handled in purchase_order.py
        which allows reusing draft pedimentos but blocks already validated ones.
        
        We only keep the format validation from the original method.
        """
        from odoo.exceptions import ValidationError
        
        help_message = self._fields['l10n_mx_edi_customs_number'].help
        if help_message:
            help_message = help_message.split('\n', 1)[1] if '\n' in help_message else help_message
        else:
            help_message = ""
        
        for landed_cost in self:
            if not landed_cost.l10n_mx_edi_customs_number:
                continue
            custom_number = landed_cost.l10n_mx_edi_customs_number.strip()
            if not CUSTOM_NUMBERS_PATTERN.match(custom_number):
                raise ValidationError(self.env._(
                    "Error!, The format of the customs number is incorrect. \n%s\n"
                    "For example: 15  48  3009  0001234", help_message))
            
            # Check for uniqueness if the landed cost is validated (done)
            if landed_cost.state == 'done':
                existing_done = self.search([
                    ('l10n_mx_edi_customs_number', '=', landed_cost.l10n_mx_edi_customs_number),
                    ('state', '=', 'done'),
                    ('id', '!=', landed_cost.id)
                ], limit=1)
                
                if existing_done:
                    raise ValidationError(self.env._(
                        "El número de pedimento '%s' ya está validado en el costo en destino '%s'. "
                        "No puede haber dos pedimentos validados con el mismo número.",
                        landed_cost.l10n_mx_edi_customs_number,
                        existing_done.name
                    ))

    def action_landed_cost_cancel(self):
        """Override to clear pedimiento_id in purchase orders when canceling."""
        self._clear_purchase_order_reference()
        return super().action_landed_cost_cancel()

    def action_landed_cost_cancel_draft(self):
        """Override to clear pedimiento_id in purchase orders when resetting to draft."""
        self._clear_purchase_order_reference()
        return super().action_landed_cost_cancel_draft()

    def action_landed_cost_cancel_delete(self):
        """Override to clear pedimiento_id in purchase orders before deleting."""
        self._clear_purchase_order_reference()
        return super().action_landed_cost_cancel_delete()

    def _clear_purchase_order_reference(self):
        """Clear pedimiento_id in all purchase orders that reference these landed costs."""
        PurchaseOrder = self.env['purchase.order']
        for landed_cost in self:
            purchase_orders = PurchaseOrder.search([
                ('pedimiento_id', '=', landed_cost.id)
            ])
            if purchase_orders:
                purchase_orders.write({'pedimiento_id': False})

    def action_revert_pedimento(self):
        """Revert the pedimiento by reverting all associated purchase orders."""
        self.ensure_one()
        from odoo import _
        from odoo.exceptions import ValidationError

        # Find associated purchase orders
        purchase_orders = self.env['purchase.order'].search([
            ('pedimiento_id', '=', self.id)
        ])
        
        if not purchase_orders:
            raise ValidationError(_("No purchase orders found associated with this pedimento."))

        reverted_count = 0
        all_return_pickings = self.env['stock.picking']

        for po in purchase_orders:
             # Call PO revert logic. 
             # Since we refactored PO logic, it will only cancel the LC if it's the last PO.
             # So this loop is safe.
             res = po.action_revert_pedimento()
             reverted_count += 1
             
             # Collect return pickings if any
             if isinstance(res, dict) and res.get('res_model') == 'stock.picking':
                 domain = res.get('domain')
                 if domain:
                     # Extract ids from domain [['id', 'in', [ids]]]
                     for leaf in domain:
                         if len(leaf) == 3 and leaf[0] == 'id' and leaf[1] == 'in':
                             all_return_pickings |= self.env['stock.picking'].browse(leaf[2])

        message = _("%s purchase orders reverted.", reverted_count)
        # Check if record still exists in DB?
        record_exists = bool(self.exists())
        
        if record_exists:
            self.message_post(body=message)
        
        if all_return_pickings:
            return {
                'name': _('Returns Created'),
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'view_mode': 'list,form',
                'domain': [('id', 'in', all_return_pickings.ids)],
                'target': 'current',
            }
            
        if not record_exists:
            # If record was deleted and no returns to show, redirect to list view
            return {
                'name': _('Landed Costs'),
                'type': 'ir.actions.act_window',
                'res_model': 'stock.landed.cost',
                'view_mode': 'list,form',
                'target': 'current',
            }
            
        return True

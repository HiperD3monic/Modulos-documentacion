# coding: utf-8
import re
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Pattern for Mexican customs numbers (Pedimentos)
# Format: 2 digits (year) + 2 spaces + 2 digits (customs) + 2 spaces + 4 digits (serial) + 2 spaces + 7 digits (progressive)
# Example: "15  48  3009  0001234"
CUSTOM_NUMBERS_PATTERN = re.compile(r'[0-9]{2}  [0-9]{2}  [0-9]{4}  [0-9]{7}')



class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    l10n_mx_edi_customs_number = fields.Char(
        help='Optional field for entering the customs information in the case '
        'of first-hand sales of imported goods or in the case of foreign trade'
        ' operations with goods or services.\n'
        'The format must be:\n'
        ' - 2 digits of the year of validation followed by two spaces.\n'
        ' - 2 digits of customs clearance followed by two spaces.\n'
        ' - 4 digits of the serial number followed by two spaces.\n'
        ' - 1 digit corresponding to the last digit of the current year, '
        'except in case of a consolidated customs initiated in the previous '
        'year of the original request for a rectification.\n'
        ' - 6 digits of the progressive numbering of the custom.',
        string='Número de pedimiento', size=21, copy=False)


    fiscal_country_codes = fields.Char(related="company_id.country_code")

    @api.constrains('l10n_mx_edi_customs_number')
    def _check_l10n_mx_edi_customs_number(self):
        help_text = self._fields['l10n_mx_edi_customs_number'].help or ''
        help_message = help_text.split('\n', 1)[1] if '\n' in help_text else ''
        for purchase_order in self:
            if not purchase_order.l10n_mx_edi_customs_number:
                continue
            custom_number = purchase_order.l10n_mx_edi_customs_number.strip()
            if not CUSTOM_NUMBERS_PATTERN.match(custom_number):
                raise ValidationError(self.env._(
                    "Error!, El formato de pedimiento es incorrecto. \n%s\n"
                    "Ejemplo: 15  48  3009  0001234", help_message))
    
    pedimiento_id = fields.Many2one(comodel_name='stock.landed.cost', string='Pedimiento')

    def button_confirm(self):
        """Override to create or reuse stock.landed.cost when confirming the purchase order.
        
        Logic:
        1. Block if customs number is already used in a validated (done) landed cost
        2. Reuse existing draft landed cost with same customs number and same partner
        3. Create new landed cost if none exists
        """
        StockLandedCost = self.env['stock.landed.cost']
        
        # VALIDATION: Block pedimentos that are already validated or belong to different partner
        for order in self:
            if not order.l10n_mx_edi_customs_number or order.pedimiento_id:
                continue
            
            # Check if there's a validated pedimento with this number
            validated_pedimento = StockLandedCost.search([
                ('l10n_mx_edi_customs_number', '=', order.l10n_mx_edi_customs_number),
                ('state', '=', 'done'),
            ], limit=1)
            
            if validated_pedimento:
                raise ValidationError(_(
                    "El número de pedimento '%s' ya ha sido validado en el costo en destino '%s'. "
                    "No se puede reutilizar un número de pedimento que ya fue procesado.",
                    order.l10n_mx_edi_customs_number,
                    validated_pedimento.name
                ))
            
            # Check if there's a draft pedimento with this number but different partner
            draft_pedimento = StockLandedCost.search([
                ('l10n_mx_edi_customs_number', '=', order.l10n_mx_edi_customs_number),
                ('state', '=', 'draft'),
            ], limit=1)
            
            if draft_pedimento:
                existing_partners = draft_pedimento.picking_ids.mapped('partner_id')
                if existing_partners and order.partner_id not in existing_partners:
                    partner_names = ', '.join(existing_partners.mapped('name'))
                    raise ValidationError(_(
                        "El número de pedimento '%s' ya está siendo utilizado en el costo en destino '%s' "
                        "con el proveedor '%s'. No se puede usar el mismo número de pedimento con un proveedor diferente.",
                        order.l10n_mx_edi_customs_number,
                        draft_pedimento.name,
                        partner_names
                    ))
        
        res = super().button_confirm()
        
        for order in self:
            if not order.l10n_mx_edi_customs_number:
                continue
            
            # If already has a pedimento, just add new pickings
            if order.pedimiento_id:
                order._add_pickings_to_pedimiento()
                continue
            
            # Search for existing draft pedimento with same customs number (already validated same partner above)
            existing_pedimento = StockLandedCost.search([
                ('l10n_mx_edi_customs_number', '=', order.l10n_mx_edi_customs_number),
                ('state', '=', 'draft'),
            ], limit=1)
            
            if existing_pedimento:
                # Reuse existing pedimento (same partner already validated)
                order.pedimiento_id = existing_pedimento.id
                order._add_pickings_to_pedimiento()
                continue
            
            # Create new pedimento immediately
            picking_ids = order.picking_ids.ids if order.picking_ids else []
            pedimento = StockLandedCost.with_company(order.company_id).create({
                'l10n_mx_edi_customs_number': order.l10n_mx_edi_customs_number,
                'target_model': 'picking',
                'picking_ids': [(6, 0, picking_ids)],
            })
            order.pedimiento_id = pedimento.id
        
        return res

    def _add_pickings_to_pedimiento(self):
        """Add pickings from this order to the associated pedimento."""
        self.ensure_one()
        if not self.pedimiento_id or not self.picking_ids:
            return
        
        existing_picking_ids = set(self.pedimiento_id.picking_ids.ids)
        for picking in self.picking_ids:
            if picking.id not in existing_picking_ids:
                self.pedimiento_id.write({
                    'picking_ids': [(4, picking.id)]
                })

    def action_open_pedimiento(self):
        self.ensure_one()

        return {
            'name': 'Pedimiento',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.landed.cost',
            'view_mode': 'form',
            'target': 'current',
            'res_id': self.pedimiento_id.id,
        }

    def action_revert_pedimento(self):
        """Revert the order to its initial state by:
        1. Reverting pickings (Cancel/Return)
        2. Unlinking from landed cost
        3. Cancelling the landed cost ONLY if no other orders use it
        """
        self.ensure_one()
        
        if not self.pedimiento_id:
            raise ValidationError(_("Esta orden no tiene un pedimento asociado para revertir."))
        
        # Validations before proceeding
        self._check_can_revert_pedimento()
        
        # Determine if we should cancel the pedimento (if no other POs rely on it)
        pedimento = self.pedimiento_id
        other_orders = self.search([
            ('pedimiento_id', '=', pedimento.id),
            ('id', '!=', self.id)
        ])
        should_cancel_pedimento = not other_orders
        
        # Step 1: Handle pickings based on their state
        # We do this FIRST to ensure pickings are clean before touching the LC
        done_pickings = self.picking_ids.filtered(lambda p: p.state == 'done')
        non_done_pickings = self.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
        
        # Cancel non-done pickings (safe operation, also triggers removal from LC via picking action_cancel override)
        if non_done_pickings:
            non_done_pickings.action_cancel()
        
        # Create returns for done pickings
        return_pickings = self.env['stock.picking']
        for picking in done_pickings:
            return_picking = self._create_return_for_picking(picking)
            if return_picking:
                return_pickings |= return_picking
                # Manually remove the original picking from LC if it wasn't removed automatically
                # (since done pickings aren't auto-removed by logic in stock_picking.py usually)
                if picking.id in pedimento.picking_ids.ids:
                     pedimento.write({'picking_ids': [(3, picking.id)]})

        # Step 2: Make sure we have the name before potentially cancelling/deleting it
        old_pedimento_name = pedimento.name
        
        # Cancel landed cost if we are the last/only order
        if should_cancel_pedimento:
            if pedimento.state == 'done':
                # Use sh_landed_cost_cancel module if available
                if hasattr(pedimento, 'sh_cancel'):
                    pedimento.sh_cancel()  # type: ignore[attr-defined]
                else:
                    raise ValidationError(_(
                        "El pedimento '%s' está validado y no se puede cancelar automáticamente. "
                        "Instale el módulo 'sh_landed_cost_cancel' o cancele el pedimento manualmente.",
                        pedimento.name
                    ))
            elif pedimento.state == 'draft':
                # Just cancel the draft landed cost
                pedimento.button_cancel()
        
        # Step 3: Clear pedimento reference from THIS order
        self.pedimiento_id = False
        
        # Post message in chatter
        message = _("Pedimento '%s' revertido. ", old_pedimento_name)
        if should_cancel_pedimento:
            message += _("El documento de pedimento fue cancelado. ")
        else:
            message += _("El documento de pedimento se conservó porque es utilizado por otras órdenes (%s). ", ', '.join(other_orders.mapped('name')))
            
        if non_done_pickings:
            message += _("Recibimientos cancelados: %s. ", ', '.join(non_done_pickings.mapped('name')))
        if return_pickings:
            message += _("Devoluciones creadas: %s.", ', '.join(return_pickings.mapped('name')))
        
        self.message_post(body=message)
        
        # Show returns if any were created
        if return_pickings:
            return {
                'name': _('Devoluciones Creadas'),
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'view_mode': 'list,form',
                'domain': [('id', 'in', return_pickings.ids)],
                'target': 'current',
            }
        
        return True

    def _check_can_revert_pedimento(self):
        """Check if the order can be reverted. Raises ValidationError if not."""
        self.ensure_one()
        
        # Check for paid invoices
        paid_invoices = self.invoice_ids.filtered(
            lambda i: i.payment_state in ('paid', 'in_payment', 'partial')
        )
        if paid_invoices:
            raise ValidationError(_(
                "No se puede revertir el pedimento: hay facturas pagadas (%s). "
                "Cancele o revierta los pagos primero.",
                ', '.join(paid_invoices.mapped('name'))
            ))
        
        # Check for posted invoices (warning but allow with confirmation already handled by button)
        posted_invoices = self.invoice_ids.filtered(
            lambda i: i.state == 'posted' and i.payment_state not in ('paid', 'in_payment', 'partial')
        )
        if posted_invoices:
            raise ValidationError(_(
                "No se puede revertir el pedimento: hay facturas publicadas (%s). "
                "Cancele las facturas primero.",
                ', '.join(posted_invoices.mapped('name'))
            ))
        
        # Check if stock from done pickings has been consumed or moved
        for picking in self.picking_ids.filtered(lambda p: p.state == 'done'):
            for move in picking.move_ids.filtered(lambda m: m.state == 'done'):
                # Check if there are outgoing moves from this stock
                outgoing_moves = self.env['stock.move'].search([
                    ('product_id', '=', move.product_id.id),
                    ('location_id', '=', move.location_dest_id.id),
                    ('state', '=', 'done'),
                    ('id', '!=', move.id),
                    ('date', '>=', move.date),
                ])
                
                # Check if the product quantity in that location is sufficient for return
                quant = self.env['stock.quant'].search([
                    ('product_id', '=', move.product_id.id),
                    ('location_id', '=', move.location_dest_id.id),
                ])
                available_qty = sum(quant.mapped('quantity'))
                
                if available_qty < move.quantity:
                    raise ValidationError(_(
                        "No se puede revertir: el producto '%s' no tiene suficiente stock disponible "
                        "en la ubicación '%s' (disponible: %s, requerido: %s). "
                        "Es posible que el stock ya haya sido consumido o vendido.",
                        move.product_id.display_name,
                        move.location_dest_id.display_name,
                        available_qty,
                        move.quantity
                    ))

    def _create_return_for_picking(self, picking):
        """Create and validate a return for a done picking."""
        if picking.state != 'done':
            return False
        
        # Check if there are moves to return
        returnable_moves = picking.move_ids.filtered(
            lambda m: m.state == 'done' and not m.scrapped and m.quantity > 0
        )
        if not returnable_moves:
            return False
        
        # Use the stock.return.picking wizard
        ReturnPickingWizard = self.env['stock.return.picking'].with_context(
            active_id=picking.id,
            active_model='stock.picking'
        )
        
        try:
            return_wizard = ReturnPickingWizard.create({
                'picking_id': picking.id,
            })
            
            # Set full quantities for all return lines
            for line in return_wizard.product_return_moves:
                line.quantity = line.move_id.quantity
            
            # Create the return picking
            return_picking = return_wizard._create_return()
            
            # Auto-validate the newly created return picking
            # We need to assign (reserve) quantities first, then validate
            if return_picking:
                return_picking.action_assign()
                return_picking.button_validate()
            
            return return_picking
            
        except Exception as e:
            # If return creation fails, log it but don't block
            self.message_post(body=_(
                "No se pudo crear la devolución para %s: %s",
                picking.name, str(e)
            ))
            return False

    def action_validate_pedimentos_bulk(self):
        """Validate pedimentos for multiple selected purchase orders.
        
        Handles various cases:
        - Orders without customs number: skipped with message
        - Orders without associated pedimento: skipped with message
        - Orders already validated (pedimento in 'done' state): skipped with message
        - Orders with same pedimento number: validates once
        - Orders with different pedimento numbers: validates each
        - Partial success: validates what can be validated, reports errors
        
        Returns:
            dict: Action window with validation results summary
        """
        if not self:
            return {'type': 'ir.actions.act_window_close'}
        
        # Categorize orders
        orders_without_customs = self.filtered(lambda o: not o.l10n_mx_edi_customs_number)
        orders_without_pedimento = self.filtered(
            lambda o: o.l10n_mx_edi_customs_number and not o.pedimiento_id
        )
        orders_already_validated = self.filtered(
            lambda o: o.pedimiento_id and o.pedimiento_id.state == 'done'
        )
        orders_to_validate = self.filtered(
            lambda o: o.pedimiento_id and o.pedimiento_id.state == 'draft'
        )
        
        # Collect unique pedimentos to validate (avoid duplicates)
        pedimentos_to_validate = orders_to_validate.mapped('pedimiento_id')
        
        # Validation results
        validated_pedimentos = self.env['stock.landed.cost']
        validation_errors = []
        
        for pedimento in pedimentos_to_validate:
            try:
                # Check if pedimento has pickings
                if not pedimento.picking_ids:
                    validation_errors.append(_(
                        "Pedimento '%s': No tiene transferencias asociadas.",
                        pedimento.name
                    ))
                    continue
                
                # Note: Pickings are not validated automatically
                # The pedimento will be validated regardless of picking state
                
                # Compute button to ensure costs are calculated (if any cost lines exist)
                if pedimento.cost_lines:
                    pedimento.compute_landed_cost()
                
                # Validate the pedimento
                pedimento.button_validate()
                validated_pedimentos |= pedimento
                
            except ValidationError as e:
                validation_errors.append(_(
                    "Pedimento '%s': %s",
                    pedimento.name, str(e.args[0]) if e.args else str(e)
                ))
            except Exception as e:
                validation_errors.append(_(
                    "Pedimento '%s': Error inesperado - %s",
                    pedimento.name, str(e)
                ))
        
        # Build result message
        message_parts = []
        
        if validated_pedimentos:
            message_parts.append(_(
                "✓ %s pedimento(s) validado(s): %s",
                len(validated_pedimentos),
                ', '.join(validated_pedimentos.mapped('name'))
            ))
        
        if orders_without_customs:
            message_parts.append(_(
                "⊘ %s orden(es) omitida(s) sin número de pedimento: %s",
                len(orders_without_customs),
                ', '.join(orders_without_customs.mapped('name'))
            ))
        
        if orders_without_pedimento:
            message_parts.append(_(
                "⊘ %s orden(es) omitida(s) sin documento de pedimento asociado: %s",
                len(orders_without_pedimento),
                ', '.join(orders_without_pedimento.mapped('name'))
            ))
        
        if orders_already_validated:
            message_parts.append(_(
                "⊘ %s orden(es) omitida(s) con pedimento ya validado: %s",
                len(orders_already_validated),
                ', '.join(orders_already_validated.mapped('name'))
            ))
        
        # Add errors at the bottom with a separator
        if validation_errors:
            message_parts.append("")  # Blank line separator
            message_parts.append(_("✗ Errores durante la validación:"))
            for error in validation_errors:
                message_parts.append("  • " + error)
        
        # Show result notification
        message = "\n".join(message_parts) if message_parts else _("No hay pedimentos para validar.")
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Validación de Pedimentos'),
                'message': message,
                'type': 'success' if validated_pedimentos and not validation_errors else (
                    'warning' if validated_pedimentos or not validation_errors else 'danger'
                ),
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }


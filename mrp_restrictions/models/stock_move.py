# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockMove(models.Model):
    _inherit = 'stock.move'

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override create to:
        1. Validate authorization for adding components to manufacturing orders.
        2. Send notifications when new components are added.
        """
        # 1. Authorization Check
        for vals in vals_list:
            production_id = vals.get('raw_material_production_id')
            if production_id:
                if not self.env['mrp.production']._is_current_user_authorized():
                    production = self.env['mrp.production'].browse(production_id)
                    raise UserError(_(
                        'No tiene permiso para agregar componentes a órdenes de fabricación. '
                        'Solo los empleados autorizados pueden realizar esta acción. '
                        'Orden afectada: %s'
                    ) % production.name)
        
        # 2. Limit creation
        moves = super(StockMove, self).create(vals_list)
        
        # 3. Notification for new components
        changes_to_notify = []
        for move in moves:
            if move.raw_material_production_id:
                # Check if this product already exists in the production order (excluding this new move)
                # This prevents "New Component" alerts when just adding more quantity to an existing component
                existing_moves_count = self.env['stock.move'].search_count([
                    ('raw_material_production_id', '=', move.raw_material_production_id.id),
                    ('product_id', '=', move.product_id.id),
                    ('id', '!=', move.id)
                ])
                
                if existing_moves_count == 0:
                    change_info = {
                        'move_id': move.id,
                        'product_name': move.product_id.display_name,
                        'production': move.raw_material_production_id,
                        'lines': [{
                            'field': 'new_component',
                            'label': 'Componente',
                            'old': 'Inexistente',
                            'new': 'Agregado (Cant: %s %s)' % (move.product_uom_qty, move.product_uom.name)
                        }]
                    }
                    changes_to_notify.append(change_info)
        
        if changes_to_notify:
            self._send_consumption_notifications_from_move(changes_to_notify)
            
        return moves

    def write(self, vals):
        """
        Override write to:
        1. Validate authorization for consumption QUANTITY changes
        2. Send notifications for changes made from the workshop
        """
        # Fields that are restricted (only authorized users can change)
        restricted_quantity_fields = ['product_uom_qty', 'quantity']
        quantity_fields_changed = [f for f in restricted_quantity_fields if f in vals]
        
        # Fields that trigger notifications (all consumption-related changes)
        notification_fields = ['product_uom_qty', 'quantity', 'picked', 'manual_consumption']
        notification_fields_changed = [f for f in notification_fields if f in vals]
        
        # Filter moves that are raw materials in manufacturing orders
        raw_material_moves = self.filtered(lambda m: m.raw_material_production_id)
        
        # Check authorization for quantity changes
        if quantity_fields_changed and raw_material_moves:
            if not self.env['mrp.production']._is_current_user_authorized():
                production_names = ', '.join(raw_material_moves.mapped('raw_material_production_id.name'))
                raise UserError(_(
                    'No tiene permiso para modificar las cantidades de consumo. '
                    'Solo los empleados autorizados pueden cambiar "Por consumir" o "Consumido". '
                    'Órdenes afectadas: %s'
                ) % production_names)
        
        # Capture old values for notification BEFORE the write
        changes_to_notify = []
        if notification_fields_changed and raw_material_moves:
            for move in raw_material_moves:
                change_info = {
                    'move_id': move.id,
                    'product_name': move.product_id.display_name,
                    'production': move.raw_material_production_id,
                    'lines': []
                }
                
                for field in notification_fields_changed:
                    old_val = getattr(move, field, None)
                    new_val = vals.get(field)
                    
                    if old_val != new_val:
                        line_data = {'field': field, 'old': old_val, 'new': new_val}
                        
                        if field == 'picked':
                            line_data.update({
                                'label': 'Estado',
                                'old': '○ Pendiente' if not old_val else '✓ Consumido',
                                'new': '○ Pendiente' if not new_val else '✓ Consumido'
                            })
                        elif field == 'manual_consumption':
                            line_data.update({
                                'label': 'Consumo',
                                'old': 'Automático' if not old_val else 'Manual',
                                'new': 'Automático' if not new_val else 'Manual'
                            })
                        elif field == 'product_uom_qty':
                            line_data.update({
                                'label': 'Por consumir',
                                'old': str(old_val),
                                'new': str(new_val)
                            })
                        elif field == 'quantity':
                            line_data.update({
                                'label': 'Cantidad',
                                'old': str(old_val),
                                'new': str(new_val)
                            })
                        
                        change_info['lines'].append(line_data)
                
                if change_info['lines']:
                    changes_to_notify.append(change_info)
        
        # Perform the actual write
        result = super(StockMove, self).write(vals)
        
        # Send notifications if changes were detected
        if changes_to_notify:
            self._send_consumption_notifications_from_move(changes_to_notify)
        
        return result

    def _send_consumption_notifications_from_move(self, changes_to_notify):
        """Send notifications for changes made directly to moves (e.g., from workshop)."""
        notification_users = self.env['mrp.production']._get_notification_users()
        
        if not notification_users:
            return
        
        partner_ids = notification_users.mapped('partner_id').ids
        
        # Group changes by production order
        changes_by_production = {}
        for change in changes_to_notify:
            prod_id = change['production'].id
            if prod_id not in changes_by_production:
                changes_by_production[prod_id] = {
                    'production': change['production'],
                    'product_items': []
                }
            changes_by_production[prod_id]['product_items'].append({
                'product': change['product_name'],
                'lines': change['lines']
            })
        
        # Get current user/employee name for the notification
        from odoo.http import request
        user_name = self.env.user.name
        
        # Update user name resolution to use new logic (checking connected employees)
        # We try to get the most specific employee applicable
        employee_name = None
        if request and hasattr(request, 'session'):
            # Check single employee_id first (active operator)
            session_employee_id = request.session.get('employee_id')
            if session_employee_id:
                employee = self.env['hr.employee'].sudo().browse(session_employee_id)
                if employee.exists():
                     employee_name = employee.name
            
            # If not found, check if we can get name from connected employees?
            # Usually the action is performed by the active one (employee_id in session), 
            # or the user. If employee_id is set in session, that's likely the actor.
        
        if employee_name:
            user_name = employee_name

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        
        for prod_id, data in changes_by_production.items():
            production = data['production']
            product_items = data['product_items']
            
            production_url = "%s/web#id=%s&model=mrp.production&view_type=form" % (base_url, production.id)
            
            # Prepare render context
            render_context = {
                'user_name': user_name,
                'production_name': production.name,
                'production_url': production_url,
                'changes': product_items,
            }
            
            try:
                body_html = self.env['ir.qweb']._render(
                    'mrp_restrictions.notification_consumption_change', 
                    render_context
                )
                
                message = production.sudo().message_post(
                    body=body_html,
                    subject=_('🔔 Modificación en consumo - %s') % production.name,
                    partner_ids=partner_ids,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
                
                if message:
                    notifications = self.env['mail.notification'].sudo().search([
                        ('mail_message_id', '=', message.id),
                        ('res_partner_id', 'in', partner_ids),
                    ])
                    notifications.write({
                        'is_read': False,
                        'notification_status': 'sent',
                    })
                    _logger.info("MRP Restrictions: Notification sent from workshop for %s", production.name)
                    
            except Exception as e:
                _logger.error("MRP Restrictions: Failed to send workshop notification: %s", e)

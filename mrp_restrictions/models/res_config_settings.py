# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    allowed_consumption_editors = fields.Many2many(
        'hr.employee',
        string='Empleados autorizados para modificar consumos',
        help='Empleados que pueden modificar las cantidades de consumo en órdenes de fabricación'
    )

    consumption_change_notification_users = fields.Many2many(
        'res.users',
        string='Usuarios a notificar por cambios en consumos',
        help='Estos usuarios recibirán alertas cuando se modifiquen cantidades de consumo'
    )

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        
        # Store allowed consumption editors
        allowed_ids = self.allowed_consumption_editors.ids if self.allowed_consumption_editors else []
        IrConfigParameter.set_param(
            'mrp_restrictions.allowed_consumption_editors',
            json.dumps(allowed_ids)
        )
        _logger.info("MRP Restrictions: Saved allowed editors: %s", allowed_ids)
        
        # Store notification users
        notification_ids = self.consumption_change_notification_users.ids if self.consumption_change_notification_users else []
        IrConfigParameter.set_param(
            'mrp_restrictions.consumption_change_notification_users',
            json.dumps(notification_ids)
        )
        _logger.info("MRP Restrictions: Saved notification users: %s", notification_ids)

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        
        # Get allowed consumption editors
        allowed_param = IrConfigParameter.get_param('mrp_restrictions.allowed_consumption_editors')
        allowed_str = allowed_param if isinstance(allowed_param, str) else '[]'
        try:
            allowed_ids = json.loads(allowed_str)
        except (ValueError, TypeError):
            allowed_ids = []
        
        # Verify employees exist
        valid_employees = self.env['hr.employee'].sudo().browse(allowed_ids).exists()
        
        # Get notification users
        notification_param = IrConfigParameter.get_param('mrp_restrictions.consumption_change_notification_users')
        notification_str = notification_param if isinstance(notification_param, str) else '[]'
        try:
            notification_ids = json.loads(notification_str)
        except (ValueError, TypeError):
            notification_ids = []
        
        # Verify users exist
        valid_users = self.env['res.users'].sudo().browse(notification_ids).exists()
        
        res.update(
            allowed_consumption_editors=[(6, 0, valid_employees.ids)],
            consumption_change_notification_users=[(6, 0, valid_users.ids)],
        )
        return res

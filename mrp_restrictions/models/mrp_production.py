# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    is_consumption_editable = fields.Boolean(
        compute='_compute_is_consumption_editable',
        string='Can Edit Consumption',
        help='Technical field to determine if current user can edit consumption quantities'
    )

    @api.depends_context('uid')
    def _compute_is_consumption_editable(self):
        """Compute if the current user is authorized to edit consumption quantities."""
        is_authorized = self._is_current_user_authorized()
        for record in self:
            record.is_consumption_editable = is_authorized

    def _is_current_user_authorized(self):
        """Check if the current user/employee is authorized to edit consumption quantities.
        
        This method checks multiple sources for the current employee:
        1. Shop floor connected operators (request.session.employees_connected)
        2. Single shop floor session employee (request.session.employee_id) - legacy
        3. Web session user's employee (self.env.user.employee_id)
        
        Authorization is granted if ANY connected operator is in the allowed list.
        """
        from odoo.http import request
        
        employee_ids_to_check = []
        
        # Try to get employees from shop floor session (operators panel)
        if request and hasattr(request, 'session'):
            # Check for connected employees list (from operator panel)
            connected_employees = request.session.get('employees_connected', [])
            if connected_employees:
                employee_ids_to_check.extend(connected_employees)
            
            # Also check single employee_id (legacy/fallback)
            session_employee_id = request.session.get('employee_id')
            if session_employee_id and session_employee_id not in employee_ids_to_check:
                employee_ids_to_check.append(session_employee_id)
        
        # If no shop floor employees, fall back to web user's employee
        if not employee_ids_to_check:
            user = self.env.user
            employee = user.employee_id
            if not employee:
                employee = self.env['hr.employee'].sudo().search([
                    ('user_id', '=', user.id)
                ], limit=1)
            if employee:
                employee_ids_to_check.append(employee.id)
        
        if not employee_ids_to_check:
            return False
        
        # Get allowed editors list
        allowed_param = self.env['ir.config_parameter'].sudo().get_param(
            'mrp_restrictions.allowed_consumption_editors'
        )
        allowed_str = allowed_param if isinstance(allowed_param, str) else '[]'
        
        try:
            allowed_ids = json.loads(allowed_str)
        except (ValueError, TypeError):
            allowed_ids = []
        
        if not allowed_ids:
            return False
        
        # Check if ANY of the connected employees is authorized
        return any(emp_id in allowed_ids for emp_id in employee_ids_to_check)

    def _check_consumption_edit_permission(self):
        """Wrapper for compatibility."""
        return self._is_current_user_authorized()

    def _get_notification_users(self):
        """Get the list of users configured to receive consumption change notifications."""
        notification_param = self.env['ir.config_parameter'].sudo().get_param(
            'mrp_restrictions.consumption_change_notification_users'
        )
        notification_str = notification_param if isinstance(notification_param, str) else '[]'
        
        try:
            notification_ids = json.loads(notification_str)
        except (ValueError, TypeError):
            notification_ids = []
        
        if not notification_ids:
            return self.env['res.users']
        
        return self.env['res.users'].sudo().browse(notification_ids).exists()

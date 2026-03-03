# coding: utf-8
# ============================================================================
# WIZARD: Resultados de Operación de Pedimentos
# ============================================================================
# Muestra el resumen de resultados después de ejecutar una operación
# masiva de validación de pedimentos.
# ============================================================================

import json
import logging

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class PedimentoOperationResult(models.TransientModel):
    """
    Wizard para mostrar los resultados de una operación masiva.

    Se abre automáticamente después de ejecutar una operación desde el
    wizard de preview (PedimentoOperationWizard.action_execute).
    """

    _name = 'pedimento.operation.result'
    _description = 'Resultado de Operación de Pedimentos'

    # ========== CAMPOS ==========

    operation_type = fields.Selection(
        selection=[
            ('validacion', 'Validación'),
            ('cambio_numero', 'Cambio de Número'),
        ],
        string='Tipo de Operación',
        readonly=True,
    )

    total_count = fields.Integer(string='Total', readonly=True)
    success_count = fields.Integer(string='Exitosos', readonly=True)
    error_count = fields.Integer(string='Con Errores', readonly=True)
    skipped_count = fields.Integer(string='Omitidos', readonly=True)

    results_html = fields.Html(
        string='Detalle de Resultados',
        sanitize=False,
        readonly=True,
    )

    log_id = fields.Many2one(
        comodel_name='pedimento.operation.log',
        string='Log de Auditoría',
        readonly=True,
    )

    # ========== ACCIONES ==========

    @api.model
    def action_show_results(self, operation_type, result_details, log_id):
        """
        Crea el wizard de resultados y lo muestra.

        Args:
            operation_type: Tipo de operación ejecutada.
            result_details: Lista de diccionarios con detalles por registro.
            log_id: ID del log de auditoría creado.

        Returns:
            dict: Acción de ventana para mostrar el wizard.
        """
        success = sum(1 for d in result_details if d.get('result') == 'exito')
        errors = sum(1 for d in result_details if d.get('result') == 'error')
        skipped = sum(1 for d in result_details if d.get('result') == 'omitido')

        results_html = self._generate_results_html(result_details)

        wizard = self.create({
            'operation_type': operation_type,
            'total_count': len(result_details),
            'success_count': success,
            'error_count': errors,
            'skipped_count': skipped,
            'results_html': results_html,
            'log_id': log_id,
        })

        return {
            'name': _('Resultados de la Operación'),
            'type': 'ir.actions.act_window',
            'res_model': 'pedimento.operation.result',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @classmethod
    def _generate_results_html(cls, result_details):
        """
        Genera tabla HTML con los resultados de la operación.

        Args:
            result_details: Lista de diccionarios con detalles.

        Returns:
            Markup: HTML seguro para renderizar.
        """
        if not result_details:
            return Markup('<p>No hay resultados para mostrar.</p>')

        # Función helper para crear links
        def make_link(name, model, res_id):
            if not res_id or not model:
                return name
            # URL para abrir el registro en el backend
            url = f"/web#id={res_id}&model={model}&view_type=form"
            return f'<a href="{url}" target="_blank" style="font-weight: bold; color: #017e84; text-decoration: none;">{name}</a>'

        rows = []
        for item in result_details:
            rec_name = item.get('record_name', 'N/A')
            rec_model = item.get('record_model', '')
            rec_id = item.get('record_id')
            lc_name = item.get('landed_cost_name', '')
            customs = item.get('customs_number', '')
            result = item.get('result', 'error')
            message = item.get('message', '')

            # Generar link para el registro principal
            link_html = make_link(rec_name, rec_model, rec_id)

            if result == 'exito':
                color = '#28a745'
                bg_color = '#ffffff' # Fondo blanco limpio
                icon = '✅'
                label = 'Exitoso'
            elif result == 'omitido':
                color = '#6c757d'
                bg_color = '#f8f9fa' # Fondo gris muy claro
                icon = '⏭️'
                label = 'Omitido'
            else:
                color = '#dc3545'
                bg_color = '#fff5f5' # Fondo rojo muy claro
                icon = '❌'
                label = 'Error'

            rows.append(
                f'<tr style="background-color: {bg_color}; border-bottom: 1px solid #dee2e6;">'
                f'<td style="padding: 10px 12px;">{link_html}</td>'
                f'<td style="padding: 10px 12px;">{lc_name}</td>'
                f'<td style="padding: 10px 12px;">{customs}</td>'
                f'<td style="padding: 10px 12px; color: {color}; font-weight: bold;">'
                f'{icon} {label}</td>'
                f'<td style="padding: 10px 12px; color: #000;">{message}</td>'
                f'</tr>'
            )

        html = (
            '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
            '<thead><tr style="background-color: #000000; border-bottom: 2px solid #dee2e6;">'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Registro</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Referencia</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Pedimento</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Resultado</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Mensaje</th>'
            '</tr></thead>'
            '<tbody>'
            + ''.join(rows)
            + '</tbody></table>'
        )

        return Markup(html)

    def action_view_log(self):
        """Abre el log de auditoría asociado a esta operación."""
        self.ensure_one()
        if not self.log_id:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'name': _('Log de Auditoría'),
            'type': 'ir.actions.act_window',
            'res_model': 'pedimento.operation.log',
            'res_id': self.log_id.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_close(self):
        """Cierra el wizard de resultados."""
        return {'type': 'ir.actions.act_window_close'}

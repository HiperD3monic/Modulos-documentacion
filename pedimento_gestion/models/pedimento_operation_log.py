# coding: utf-8
# ============================================================================
# MODELO DE AUDITORÍA - HISTORIAL DE OPERACIONES DE PEDIMENTOS
# ============================================================================
# Este archivo define el modelo persistente para registrar cada operación
# de validación, cambio de número o reversión de pedimentos, proporcionando
# trazabilidad completa para auditoría.
# ============================================================================

import json
import logging
from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class PedimentoOperationLog(models.Model):
    """
    Modelo de auditoría para operaciones de pedimentos.

    Registra cada operación de validación o cambio de número ejecutada sobre
    pedimentos (costos en destino), almacenando el usuario, fecha, tipo
    de operación, resultados detallados en JSON y métricas de ejecución.

    El campo 'details_json' almacena una lista de diccionarios con la
    estructura:
        [
            {
                "record_name": "LC/00001",
                "record_model": "stock.landed.cost",
                "record_id": 42,
                "result": "exito" | "error" | "omitido",
                "message": "Descripción del resultado"
            },
            ...
        ]

    El campo 'details_html' es un campo computado que renderiza el JSON
    en una tabla HTML legible con colores por estado.
    """

    _name = 'pedimento.operation.log'
    _description = 'Historial de Operaciones de Pedimentos'
    _order = 'operation_date desc, id desc'
    _rec_name = 'name'

    # ========== CAMPOS DE IDENTIFICACIÓN ==========

    name = fields.Char(
        string='Referencia',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
        help='Referencia única auto-generada para esta entrada de log.',
    )

    # ========== CAMPOS DE OPERACIÓN ==========

    user_id = fields.Many2one(
        comodel_name='res.users',
        string='Usuario',
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
        help='Usuario que ejecutó la operación.',
    )

    operation_date = fields.Datetime(
        string='Fecha y Hora',
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        help='Fecha y hora exacta en que se ejecutó la operación.',
    )

    operation_type = fields.Selection(
        selection=[
            ('validacion', 'Validación'),
            ('reversion', 'Reversión'),
            ('cambio_numero', 'Cambio de Número'),
        ],
        string='Tipo de Operación',
        required=True,
        readonly=True,
        help='Indica si la operación fue una validación, reversión o cambio de número de pedimentos.',
    )

    is_bulk = fields.Boolean(
        string='Operación Masiva',
        default=False,
        readonly=True,
        help='Indica si la operación fue ejecutada sobre múltiples registros.',
    )

    # ========== RELACIONES DE TRAZABILIDAD ==========

    reversion_ids = fields.Many2many(
        comodel_name='pedimento.operation.log',
        relation='pedimento_log_reversion_rel',
        column1='validation_id',
        column2='reversion_id',
        string='Reversiones Asociadas',
        help='Logs de reversión que han afectado a los registros de este log de validación.',
    )

    origin_log_ids = fields.Many2many(
        comodel_name='pedimento.operation.log',
        relation='pedimento_log_reversion_rel',
        column1='reversion_id',
        column2='validation_id',
        string='Validaciones Originales',
        help='Logs de validación originales que fueron revertidos por este log.',
    )

    is_reverted = fields.Boolean(
        string='Revertido',
        compute='_compute_is_reverted',
        store=True,
        help='Indica si este registro de validación ha sido revertido.',
    )

    @api.depends('reversion_ids')
    def _compute_is_reverted(self):
        for record in self:
            record.is_reverted = bool(record.reversion_ids)

    hide_traceability = fields.Boolean(
        compute='_compute_hide_traceability',
        store=False,
        help='Campo técnico para ocultar la pestaña de trazabilidad según el contexto.',
    )

    def _compute_hide_traceability(self):
        """Oculta la trazabilidad si viene en el contexto (para evitar bucles)."""
        hide = self.env.context.get('hide_traceability', False)
        for record in self:
            record.hide_traceability = hide

    # ========== CAMPOS DE RESULTADOS ==========

    affected_count = fields.Integer(
        string='Total Procesados',
        readonly=True,
        help='Cantidad total de registros procesados en esta operación.',
    )

    success_count = fields.Integer(
        string='Exitosos',
        readonly=True,
        help='Cantidad de registros procesados exitosamente.',
    )

    error_count = fields.Integer(
        string='Con Errores',
        readonly=True,
        help='Cantidad de registros que presentaron errores.',
    )

    skipped_count = fields.Integer(
        string='Omitidos',
        readonly=True,
        help='Cantidad de registros omitidos (ya procesados, sin datos, etc.).',
    )

    final_state = fields.Selection(
        selection=[
            ('exito', 'Éxito Completo'),
            ('parcial', 'Éxito Parcial'),
            ('fallo', 'Fallo Total'),
        ],
        string='Estado Final',
        readonly=True,
        help=(
            'Estado final de la operación:\n'
            '- Éxito Completo: todos los registros se procesaron correctamente.\n'
            '- Éxito Parcial: algunos registros se procesaron y otros fallaron.\n'
            '- Fallo Total: ningún registro pudo ser procesado.'
        ),
    )

    execution_time = fields.Float(
        string='Tiempo de Ejecución (s)',
        readonly=True,
        digits=(10, 3),
        help='Tiempo total de ejecución de la operación en segundos.',
    )

    # ========== CAMPOS DE DETALLE ==========

    details_json = fields.Text(
        string='Detalles (JSON)',
        readonly=True,
        help=(
            'Detalles de cada registro procesado en formato JSON. '
            'Cada elemento contiene: record_name, record_model, record_id, result, message.'
        ),
    )

    details_html = fields.Html(
        string='Detalles de la Operación',
        compute='_compute_details_html',
        sanitize=False,
        help='Visualización HTML legible de los detalles de la operación.',
    )

    notes = fields.Text(
        string='Notas',
        readonly=True,
        help='Notas adicionales sobre la operación.',
    )

    # ========== MÉTODOS DE CREACIÓN ==========

    @api.model_create_multi
    def create(self, vals_list):
        """
        Sobrescribe create para asignar secuencia automática al nombre.

        Args:
            vals_list: Lista de diccionarios con valores para crear registros.

        Returns:
            Recordset con los registros creados.
        """
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'pedimento.operation.log'
                ) or _('Nuevo')
        return super().create(vals_list)

    # ========== CAMPOS COMPUTADOS ==========

    @api.depends('details_json', 'reversion_ids')
    def _compute_details_html(self):
        """
        Genera una tabla HTML a partir del JSON de detalles.
        
        Cada línea se muestra con un color según su resultado.
        Si es una validación y el registro fue revertido posteriormente,
        se muestra un badge indicándolo.
        """
        for record in self:
            if not record.details_json:
                record.details_html = Markup(
                    '<p style="color: #6c757d;"><em>Sin detalles disponibles.</em></p>'
                )
                continue

            try:
                details = json.loads(record.details_json)
            except (json.JSONDecodeError, TypeError):
                record.details_html = Markup(
                    '<p style="color: #dc3545;"><em>Error al leer los detalles.</em></p>'
                )
                continue

            if not details:
                record.details_html = Markup(
                    '<p style="color: #6c757d;"><em>Sin detalles disponibles.</em></p>'
                )
                continue

            # Obtener IDs revertidos de los logs vinculados
            reverted_ids_in_linked_logs = set()
            if record.operation_type == 'validacion' and record.reversion_ids:
                for rev_log in record.reversion_ids:
                    try:
                        if not rev_log.details_json:
                            continue
                        rev_details = json.loads(rev_log.details_json)
                        for d in rev_details:
                            if d.get('result') == 'exito' and d.get('record_id'):
                                reverted_ids_in_linked_logs.add(d.get('record_id'))
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Construir tabla HTML
            rows = []
            for item in details:
                rec_name = item.get('record_name', 'N/A')
                rec_id = item.get('record_id')
                rec_model = item.get('record_model')
                
                # Enriquecer nombre con referencia de proveedor si es una OC
                # Esto asegura que aparezca incluso en logs antiguos
                if rec_id and rec_model == 'purchase.order':
                    try:
                        po = self.env['purchase.order'].browse(rec_id)
                        if po.exists() and po.partner_ref:
                            ref_str = f"({po.partner_ref})"
                            # Evitar duplicar si ya viene en el JSON
                            if ref_str not in rec_name:
                                rec_name = f"{po.name} {ref_str}"
                    except Exception:
                        pass

                lc_name = item.get('landed_cost_name', '')
                customs = item.get('customs_number', '')
                result = item.get('result', 'error')
                message = item.get('message', '')

                # Determinar colores e iconos
                if result == 'exito':
                    color = '#28a745'  # Verde
                    bg_color = 'white'
                    icon = '✅'
                    label = 'Exitoso'
                elif result == 'omitido':
                    color = '#6c757d'  # Gris
                    bg_color = '#f8f9fa'
                    icon = '⏭️'
                    label = 'Omitido'
                else:
                    color = '#dc3545'  # Rojo
                    bg_color = '#fff5f5'
                    icon = '❌'
                    label = 'Error'

                # Verificar si está revertido (solo para validaciones exitosas)
                reverted_badge = ''
                if record.operation_type == 'validacion' and result == 'exito' and rec_id in reverted_ids_in_linked_logs:
                    reverted_badge = (
                        '<span style="display: inline-block; padding: 2px 6px; '
                        'font-size: 10px; font-weight: bold; color: #856404; '
                        'background-color: #fff3cd; border-radius: 4px; '
                        'margin-left: 6px; border: 1px solid #ffeeba;">'
                        '↩️ REVERTIDO</span>'
                    )

                # Build clickable link for the record name
                # Links are disabled if accessed via Purchase Order smart button (context: disable_log_links)
                disable_links = self.env.context.get('disable_log_links')
                
                if rec_id and item.get('record_model') and not disable_links:
                    link_url = f'/web#id={rec_id}&model={item.get("record_model")}&view_type=form'
                    name_html = (
                        f'<a href="{link_url}" target="_blank" '
                        f'style="color: #017e84; text-decoration: none; font-weight: bold;">'
                        f'{rec_name}</a>'
                    )
                else:
                    name_html = f'<span style="font-weight: bold;">{rec_name}</span>'

                rows.append(
                    f'<tr style="background-color: {bg_color}; border-bottom: 1px solid #dee2e6;">'
                    f'<td style="padding: 10px 12px; font-weight: 500;">'
                    f'{name_html}{reverted_badge}</td>'
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

            record.details_html = Markup(html)

    purchase_order_ids = fields.Many2many(
        comodel_name='purchase.order',
        relation='pedimento_log_purchase_rel',
        column1='log_id',
        column2='purchase_id',
        string='Órdenes de Compra',
        help='Órdenes de compra afectadas por esta operación.',
    )

    # ========== MÉTODOS AUXILIARES ==========

    @api.model
    def create_log(self, operation_type, is_bulk, details, execution_time=0.0, notes=''):
        """
        Método auxiliar para crear un registro de log de forma simplificada.
        Y enlaza reversiones con sus validaciones originales.
        """
        # Calcular conteos a partir de los detalles
        success_count = sum(1 for d in details if d.get('result') == 'exito')
        error_count = sum(1 for d in details if d.get('result') == 'error')
        skipped_count = sum(1 for d in details if d.get('result') == 'omitido')
        affected_count = len(details)

        # Determinar estado final
        if error_count == 0 and success_count > 0:
            final_state = 'exito'
        elif success_count > 0 and error_count > 0:
            final_state = 'parcial'
        else:
            final_state = 'fallo'

        # Extraer IDs de órdenes de compra para el campo M2M
        po_ids = [
            d.get('record_id') 
            for d in details 
            if d.get('record_model') == 'purchase.order' and d.get('record_id')
        ]

        new_log = self.create({
            'operation_type': operation_type,
            'is_bulk': is_bulk,
            'affected_count': affected_count,
            'success_count': success_count,
            'error_count': error_count,
            'skipped_count': skipped_count,
            'final_state': final_state,
            'execution_time': execution_time,
            'details_json': json.dumps(details, ensure_ascii=False, indent=2),
            'notes': notes,
            'purchase_order_ids': [(6, 0, po_ids)] if po_ids else [],
        })
        
        # Enlazar reversiones
        if operation_type == 'reversion' and success_count > 0:
            self._link_reversion_to_validations(new_log, details)
            
        return new_log

    def _link_reversion_to_validations(self, reversion_log, details):
        """
        Busca y enlaza los logs de validación previos para los registros revertidos.
        """
        reverted_record_ids = [
            d.get('record_id') for d in details 
            if d.get('result') == 'exito' and d.get('record_id')
        ]
        
        if not reverted_record_ids:
            return

        # Buscar logs de validación que contengan estos IDs.
        # Buscamos logs recientes de validación para optimizar.
        domain = [('operation_type', '=', 'validacion')]
        candidates = self.search(domain, order='create_date desc', limit=100)
        
        linked_validations = self.env['pedimento.operation.log']
        
        for val_log in candidates:
            if not val_log.details_json:
                continue
            try:
                val_details = json.loads(val_log.details_json)
                val_ids = [d.get('record_id') for d in val_details if d.get('result') == 'exito']
                
                # Si hay intersección de IDs entre lo revertido y lo validado
                if set(reverted_record_ids) & set(val_ids):
                    linked_validations |= val_log
            except (json.JSONDecodeError, TypeError):
                continue
                
        if linked_validations:
             linked_validations.write({'reversion_ids': [(4, reversion_log.id)]})

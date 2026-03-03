# coding: utf-8
# ============================================================================
# WIZARD: Validación Masiva de Pedimentos (Preview)
# ============================================================================
# Wizard transient para previsualizar y ejecutar operaciones masivas de
# validación de pedimentos. Soporta validación desde purchase.order y
# stock.landed.cost.
# ============================================================================

import json
import time
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PedimentoOperationWizard(models.TransientModel):
    """
    Wizard para previsualización y ejecución de operaciones masivas
    de validación de pedimentos.

    Flujo:
    1. Se invoca desde PO o LC con action_open_preview()
    2. Se ejecutan validaciones preliminares (_run_preview_validations)
    3. Se muestra tabla HTML con preview de cada registro
    4. El usuario puede confirmar (action_execute) o cancelar

    Tipos de operación soportados:
    - 'validacion': Validar pedimentos (masivo)
    """

    _name = 'pedimento.operation.wizard'
    _description = 'Wizard de Operación de Pedimentos'

    # ========== CAMPOS DE CONFIGURACIÓN ==========

    operation_type = fields.Selection(
        selection=[
            ('validacion', 'Validación'),
        ],
        string='Tipo de Operación',
        required=True,
        readonly=True,
    )

    source_model = fields.Char(
        string='Modelo de Origen',
        required=True,
        readonly=True,
        help='purchase.order o stock.landed.cost',
    )

    source_ids = fields.Text(
        string='IDs de Origen',
        readonly=True,
        help='IDs de los registros seleccionados, separados por coma.',
    )

    # ========== CAMPOS DE CONTEO ==========

    total_count = fields.Integer(
        string='Total',
        compute='_compute_counts',
        store=True,
    )

    valid_count = fields.Integer(
        string='Válidos',
        compute='_compute_counts',
        store=True,
    )

    invalid_count = fields.Integer(
        string='Inválidos',
        compute='_compute_counts',
        store=True,
    )

    can_proceed = fields.Boolean(
        string='Puede Proceder',
        compute='_compute_counts',
        store=True,
    )

    # ========== CAMPOS DE PREVIEW ==========

    preview_html = fields.Html(
        string='Vista Previa',
        sanitize=False,
        readonly=True,
    )

    valid_lines = fields.Text(
        string='Líneas Válidas (JSON)',
        readonly=True,
    )

    # ========== CAMPOS COMPUTADOS ==========

    @api.depends('valid_lines', 'preview_html')
    def _compute_counts(self):
        """Calcula conteos a partir de las líneas válidas e inválidas."""
        for wizard in self:
            valid = []
            total = 0
            if wizard.valid_lines:
                try:
                    valid = json.loads(wizard.valid_lines)
                except (json.JSONDecodeError, TypeError):
                    valid = []

            # El total viene del source_ids
            if wizard.source_ids:
                try:
                    source_list = [int(x.strip()) for x in wizard.source_ids.split(',') if x.strip()]
                    total = len(source_list)
                except (ValueError, AttributeError):
                    total = 0

            wizard.total_count = total
            wizard.valid_count = len(valid)
            wizard.invalid_count = total - len(valid)
            wizard.can_proceed = len(valid) > 0

    # ========== ACCIÓN DE APERTURA ==========

    @api.model
    def action_open_preview(self, operation_type, source_model, source_ids):
        """
        Crea el wizard y ejecuta las validaciones preliminares para
        mostrar una vista previa al usuario.

        Args:
            operation_type: 'validacion'
            source_model: 'purchase.order' o 'stock.landed.cost'
            source_ids: Lista de IDs de los registros seleccionados.

        Returns:
            dict: Acción de ventana para mostrar el wizard.
        """
        # Ejecutar validaciones preliminares
        preview_results = self._run_preview_validations(
            operation_type, source_model, source_ids
        )

        # Separar válidos e inválidos
        valid_items = [r for r in preview_results if r.get('is_valid')]
        invalid_items = [r for r in preview_results if not r.get('is_valid')]

        # Generar HTML de preview
        preview_html = self._generate_preview_html(valid_items, invalid_items)

        # Crear wizard
        wizard = self.create({
            'operation_type': operation_type,
            'source_model': source_model,
            'source_ids': ','.join(str(sid) for sid in source_ids),
            'preview_html': preview_html,
            'valid_lines': json.dumps([{
                'record_id': v['record_id'],
                'record_name': v['record_name'],
                'record_model': v.get('record_model', source_model),
            } for v in valid_items]),
        })

        return {
            'name': _('Vista Previa de Operación'),
            'type': 'ir.actions.act_window',
            'res_model': 'pedimento.operation.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ========== VALIDACIONES PRELIMINARES ==========

    @api.model
    def _run_preview_validations(self, operation_type, source_model, source_ids):
        """
        Ejecuta validaciones preliminares sin aplicar cambios.

        Returns:
            list[dict]: Lista de diccionarios con los resultados de validación.
        """
        if operation_type == 'validacion' and source_model == 'purchase.order':
            return self._preview_validation_purchase_order(source_ids)
        elif operation_type == 'validacion' and source_model == 'stock.landed.cost':
            return self._preview_validation_landed_cost(source_ids)
        return []

    def _preview_validation_purchase_order(self, source_ids):
        """
        Preview de validación para órdenes de compra.

        Valida cada orden según las reglas del pedimento:
        - Que tenga número de pedimento
        - Que esté confirmada (estado 'purchase' o 'done')
        - Que tenga un pedimento asociado
        - Que el pedimento esté en borrador

        Returns:
            list[dict]: Resultados de preview por cada registro.
        """
        records = self.env['purchase.order'].browse(source_ids).exists()
        results = []

        for po in records:
            errors = []
            current_state = dict(po._fields['state'].selection).get(po.state, po.state)
            customs_number = po.l10n_mx_edi_customs_number or ''

            # 1. Verificar número de pedimento
            if not po.l10n_mx_edi_customs_number:
                errors.append('No tiene número de pedimento asignado.')

            # 2. Verificar estado
            if po.state not in ('purchase', 'done'):
                errors.append(f'Estado actual: {current_state}. Debe estar confirmada.')

            # 3. Verificar asociación a pedimento
            if not po.pedimiento_id:
                errors.append('No tiene un pedimento (costo en destino) asociado.')
            elif po.pedimiento_id.state == 'done':
                errors.append('El pedimento asociado ya está validado.')

            # 4. Verificar si ya fue revertida
            if po.is_reverted:
                errors.append('Esta orden fue revertida anteriormente.')

            # ---- Validaciones de facturas y CFDI (si el pedimento existe) ----
            if po.pedimiento_id and po.pedimiento_id.state == 'draft':
                pass

            is_valid = len(errors) == 0
            lc_name = po.pedimiento_id.name if po.pedimiento_id else ''
            
            # Agregamos referencia de proveedor
            ref_prov = f" ({po.partner_ref})" if po.partner_ref else ""

            results.append({
                'record_name': f"{po.name}{ref_prov}",
                'record_model': 'purchase.order',
                'record_id': po.id,
                'current_state': current_state,
                'customs_number': customs_number,
                'landed_cost_name': lc_name,
                'is_valid': is_valid,
                'validation_messages': '<br/>'.join(errors) if errors else 'OK',
                'associated_orders': lc_name,
            })

        return results

    def _preview_validation_landed_cost(self, source_ids):
        """
        Preview de validación para costos en destino.

        Valida cada costo en destino verificando:
        - Que no esté cancelado
        - Que no esté ya validado (done)
        - Que tenga número de pedimento
        - Que tenga transferencias asociadas
        """
        records = self.env['stock.landed.cost'].browse(source_ids).exists()
        results = []

        for lc in records:
            errors = []
            state_labels = {'draft': 'Borrador', 'done': 'Validado', 'cancel': 'Cancelado'}
            current_state = state_labels.get(lc.state, lc.state)
            customs_number = lc.l10n_mx_edi_customs_number or ''

            # Buscar órdenes de compra asociadas
            purchase_orders = self.env['purchase.order'].search([
                ('pedimiento_id', '=', lc.id)
            ])
            associated_names = ', '.join(purchase_orders.mapped('name')) if purchase_orders else 'Ninguna'

            # ---- Validaciones ----
            if lc.state == 'cancel':
                errors.append('El pedimento está cancelado.')
            elif lc.state == 'done':
                errors.append('El pedimento ya está validado.')

            if not lc.l10n_mx_edi_customs_number:
                errors.append('No tiene número de pedimento asignado.')

            if not lc.picking_ids:
                errors.append('No tiene transferencias asociadas.')

            # ---- Validaciones por OC ----
            if purchase_orders and lc.state == 'draft':
                for po in purchase_orders:
                    pass
                    # invoice_messages = self._check_invoices_status(po)
                    # if invoice_messages:
                    #     errors.extend([f'{po.name}: {m}' for m in invoice_messages])

                    # stock_messages = self._check_stock_availability(po)
                    # if stock_messages:
                    #     errors.extend([f'{po.name}: {m}' for m in stock_messages])

            is_valid = len(errors) == 0

            results.append({
                'record_name': lc.name or f'LC #{lc.id}',
                'record_model': 'stock.landed.cost',
                'record_id': lc.id,
                'current_state': current_state,
                'customs_number': customs_number,
                'is_valid': is_valid,
                'validation_messages': '<br/>'.join(errors) if errors else 'OK',
                'associated_orders': associated_names,
            })

        return results

    # ========== MÉTODOS DE VALIDACIÓN AUXILIAR ==========

    def _check_invoices_status(self, purchase_order):
        """
        Verifica el estado de las facturas de una orden de compra.

        Returns:
            list[str]: Lista de mensajes de error (vacía si OK).
        """
        messages = []
        invoices = purchase_order.invoice_ids
        if not invoices:
            messages.append('No tiene facturas asociadas.')
        else:
            draft_invoices = invoices.filtered(lambda inv: inv.state == 'draft')
            if draft_invoices:
                names = ', '.join(draft_invoices.mapped('name'))
                messages.append(f'Facturas en borrador: {names}')
        return messages

    def _check_stock_availability(self, purchase_order):
        """
        Verifica la disponibilidad de stock para una orden de compra.

        Returns:
            list[str]: Lista de mensajes de error (vacía si OK).
        """
        messages = []
        pickings = purchase_order.picking_ids
        if not pickings:
            messages.append('No tiene transferencias de stock.')
        else:
            pending = pickings.filtered(lambda p: p.state not in ('done', 'cancel'))
            if pending:
                names = ', '.join(pending.mapped('name'))
                messages.append(f'Transferencias pendientes: {names}')
        return messages

    def _check_cfdi_status(self, purchase_order):
        """
        Verifica el estado CFDI de las facturas.

        Returns:
            list[str]: Lista de mensajes de error (vacía si OK).
        """
        messages = []
        for invoice in purchase_order.invoice_ids:
            if hasattr(invoice, 'l10n_mx_edi_cfdi_state'):
                if invoice.l10n_mx_edi_cfdi_state and invoice.l10n_mx_edi_cfdi_state != 'sent':
                    messages.append(
                        f'Factura {invoice.name}: CFDI no enviado (estado: {invoice.l10n_mx_edi_cfdi_state})'
                    )
        return messages

    # ========== GENERACIÓN DE HTML ==========

    def _generate_preview_html(self, valid_items, invalid_items):
        """Genera tabla HTML para preview con colores por validez."""
        if not valid_items and not invalid_items:
            return Markup('<p>No hay registros para mostrar.</p>')

        rows = []

        # Función helper para crear links
        def make_link(name, model, res_id):
            if not res_id or not model:
                return name
            # URL para abrir el registro en el backend
            url = f"/web#id={res_id}&model={model}&view_type=form"
            return f'<a href="{url}" target="_blank" style="font-weight: bold; color: #017e84; text-decoration: none;">{name}</a>'

        # Ordenar válidos por pedimento para agrupar visualmente
        valid_items.sort(key=lambda x: x.get("customs_number", ""))

        # Primero los válidos
        for item in valid_items:
            rec_name = item.get("record_name", "N/A")
            rec_model = item.get("record_model", self.source_model)
            rec_id = item.get("record_id")
            customs_num = item.get("customs_number", "")
            
            link_html = make_link(rec_name, rec_model, rec_id)
            
            # Mensaje más profesional
            msg = item.get("validation_messages", "")
            if msg == "OK":
                msg = "Listo para validar"

            rows.append(
                f'<tr style="background-color: #ffffff; border-bottom: 1px solid #dee2e6;">'
                f'<td style="padding: 10px 12px;">{link_html}</td>'
                f'<td style="padding: 10px 12px;">{item.get("current_state", "")}</td>'
                f'<td style="padding: 10px 12px; font-weight: bold;">{customs_num}</td>'
                f'<td style="padding: 10px 12px;">{item.get("associated_orders", "")}</td>'
                f'<td style="padding: 10px 12px; color: #28a745; font-weight: bold;">✅ Válido</td>'
                f'<td style="padding: 10px 12px; color: #000;">{msg}</td>'
                f'</tr>'
            )

        # Luego los inválidos
        for item in invalid_items:
            rec_name = item.get("record_name", "N/A")
            rec_model = item.get("record_model", self.source_model)
            rec_id = item.get("record_id")
            customs_num = item.get("customs_number", "")
            
            link_html = make_link(rec_name, rec_model, rec_id)

            rows.append(
                f'<tr style="background-color: #fff5f5; border-bottom: 1px solid #dee2e6;">'
                f'<td style="padding: 10px 12px;">{link_html}</td>'
                f'<td style="padding: 10px 12px;">{item.get("current_state", "")}</td>'
                f'<td style="padding: 10px 12px; font-weight: bold;">{customs_num}</td>'
                f'<td style="padding: 10px 12px;">{item.get("associated_orders", "")}</td>'
                f'<td style="padding: 10px 12px; color: #dc3545; font-weight: bold;">❌ Inválido</td>'
                f'<td style="padding: 10px 12px; color: #000;">{item.get("validation_messages", "")}</td>'
                f'</tr>'
            )

        html = (
            '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
            '<thead><tr style="background-color: #000000; border-bottom: 2px solid #dee2e6;">'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Registro</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Estado</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Pedimento</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Asociado(s)</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Resultado</th>'
            '<th style="padding: 10px 12px; text-align: left; color: #ffffff;">Detalle</th>'
            '</tr></thead>'
            '<tbody>'
            + ''.join(rows)
            + '</tbody></table>'
        )

        return Markup(html)

    # ========== EJECUCIÓN ==========

    def action_execute(self):
        """
        Ejecuta la operación real sobre los registros válidos.

        Returns:
            dict: Acción de ventana para mostrar el wizard de resultados.
        """
        self.ensure_one()
        if not self.valid_lines:
            raise UserError(_("No hay registros válidos para procesar."))

        try:
            valid = json.loads(self.valid_lines)
        except (json.JSONDecodeError, TypeError):
            raise UserError(_("Error al leer los registros válidos."))

        if not valid:
            raise UserError(_("No hay registros válidos para procesar."))

        operation_type = self.operation_type
        source_model = self.source_model
        source_ids = [v['record_id'] for v in valid]

        start_time = time.time()
        result_details = []

        # Ejecutar validación
        if operation_type == 'validacion':
            if source_model == 'stock.landed.cost':
                result_details = self._execute_validation_landed_cost(source_ids)
            elif source_model == 'purchase.order':
                result_details = self._execute_validation_purchase_order(source_ids)

        execution_time = time.time() - start_time

        # Crear log de auditoría
        log = self.env['pedimento.operation.log'].create_log(
            operation_type=operation_type,
            is_bulk=True,
            details=result_details,
            execution_time=execution_time,
            notes=_(
                "Operación masiva de %s sobre %d registros de %s.",
                operation_type, len(source_ids), source_model
            ),
        )

        # Crear y mostrar wizard de resultados
        return self.env['pedimento.operation.result'].action_show_results(
            operation_type=operation_type,
            result_details=result_details,
            log_id=log.id,
        )

    def _execute_validation_landed_cost(self, source_ids):
        """
        Ejecuta la validación de pedimentos sobre costos en destino.

        Llama button_validate() en cada LC válido y registra resultados.

        Returns:
            list[dict]: Detalles de resultado por cada registro.
        """
        records = self.env['stock.landed.cost'].browse(source_ids).exists()
        results = []

        for lc in records:
            try:
                lc.with_context(skip_audit_log=True).button_validate()
                results.append({
                    'record_name': lc.name,
                    'record_model': 'stock.landed.cost',
                    'record_id': lc.id,
                    'landed_cost_name': lc.name,
                    'customs_number': lc.l10n_mx_edi_customs_number or '',
                    'result': 'exito',
                    'message': 'Validación exitosa.',
                })
            except Exception as e:
                _logger.warning("Error validating LC %s: %s", lc.name, str(e))
                results.append({
                    'record_name': lc.name,
                    'record_model': 'stock.landed.cost',
                    'record_id': lc.id,
                    'landed_cost_name': lc.name,
                    'customs_number': lc.l10n_mx_edi_customs_number or '',
                    'result': 'error',
                    'message': str(e),
                })

        return results

    def _execute_validation_purchase_order(self, source_ids):
        """
        Ejecuta la validación de pedimentos desde órdenes de compra.

        Agrupa las OCs por pedimento y valida cada costo en destino.

        Returns:
            list[dict]: Detalles de resultado por cada registro.
        """
        records = self.env['purchase.order'].browse(source_ids).exists()
        results = []

        # Agrupar por pedimento para evitar validaciones duplicadas
        pedimentos_processed = set()

        for po in records:
            ref_prov = f" ({po.partner_ref})" if po.partner_ref else ""
            record_name = f"{po.name}{ref_prov}"

            if not po.pedimiento_id:
                results.append({
                    'record_name': record_name,
                    'record_model': 'purchase.order',
                    'record_id': po.id,
                    'landed_cost_name': '',
                    'customs_number': po.l10n_mx_edi_customs_number or '',
                    'result': 'omitido',
                    'message': 'Sin pedimento asociado.',
                })
                continue

            lc = po.pedimiento_id
            if lc.id in pedimentos_processed:
                results.append({
                    'record_name': po.name,
                    'record_model': 'purchase.order',
                    'record_id': po.id,
                    'landed_cost_name': lc.name,
                    'customs_number': po.l10n_mx_edi_customs_number or '',
                    'result': 'omitido',
                    'message': f'Pedimento {lc.name} ya fue procesado en esta operación.',
                })
                continue

            try:
                lc.with_context(skip_audit_log=True).button_validate()
                pedimentos_processed.add(lc.id)
                results.append({
                    'record_name': po.name,
                    'record_model': 'purchase.order',
                    'record_id': po.id,
                    'landed_cost_name': lc.name,
                    'customs_number': po.l10n_mx_edi_customs_number or '',
                    'result': 'exito',
                    'message': f'Pedimento {lc.name} validado exitosamente.',
                })
            except Exception as e:
                _logger.warning("Error validating LC %s from PO %s: %s", lc.name, po.name, str(e))
                pedimentos_processed.add(lc.id)
                results.append({
                    'record_name': po.name,
                    'record_model': 'purchase.order',
                    'record_id': po.id,
                    'landed_cost_name': lc.name,
                    'customs_number': po.l10n_mx_edi_customs_number or '',
                    'result': 'error',
                    'message': str(e),
                })

        return results

    # ========== CANCELAR ==========

    def action_cancel(self):
        """Cierra el wizard sin ejecutar ninguna operación."""
        return {'type': 'ir.actions.act_window_close'}

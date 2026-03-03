# coding: utf-8
# ============================================================================
# MODELO EXTENDIDO: stock.landed.cost
# ============================================================================
# Extensión del modelo de Costos en Destino para gestión de pedimentos
# aduanales mexicanos. Incluye:
#   - Eliminación del constraint de unicidad en número de pedimento
#   - Validación de formato del número de pedimento
#   - Cambio individual de número de pedimento (wizard)
#   - Limpieza de referencias en órdenes de compra al cancelar/eliminar
#   - Integración con wizard de preview para operaciones masivas
# ============================================================================

import re
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Patrón para números de pedimento aduanal mexicano (CFDI)
# Formato: YY  AA  PPPP  NNNNNNN
# Ejemplo: "15  48  3009  0001234"
CUSTOM_NUMBERS_PATTERN = re.compile(r'[0-9]{2}  [0-9]{2}  [0-9]{4}  [0-9]{7}')


class StockLandedCost(models.Model):
    """
    Extensión de stock.landed.cost para gestión de pedimentos aduanales.

    Hereda el modelo base de Costos en Destino y agrega funcionalidad
    para cambio de número de pedimento, eliminación de constraint de
    unicidad, y acciones masivas con wizard de preview.
    """

    _inherit = 'stock.landed.cost'

    # ========== CAMPOS ADICIONALES ==========

    pedimento_state_display = fields.Selection(
        selection=[
            ('sin_pedimento', 'Sin Pedimento'),
            ('borrador', 'Borrador'),
            ('validado', 'Validado'),
            ('cancelado', 'Cancelado'),
        ],
        string='Estado Pedimento',
        compute='_compute_pedimento_state_display',
        store=True,
        help='Estado visual del pedimento para la vista de lista.',
    )

    # ========== MÉTODOS DE INICIALIZACIÓN ==========

    def _auto_init(self):
        """
        Elimina el constraint de unicidad en l10n_mx_edi_customs_number
        durante la inicialización del modelo.

        Este método se ejecuta cada vez que el módulo se carga/actualiza
        y asegura que el constraint sea eliminado de la base de datos,
        permitiendo múltiples costos en destino con el mismo número de
        pedimento (siempre que sean del mismo proveedor).
        """
        self.env.cr.execute("""
            ALTER TABLE stock_landed_cost 
            DROP CONSTRAINT IF EXISTS stock_landed_cost_l10n_mx_edi_customs_number;
        """)
        return super()._auto_init()

    # ========== CAMPOS COMPUTADOS ==========

    @api.depends('l10n_mx_edi_customs_number', 'state')
    def _compute_pedimento_state_display(self):
        """
        Calcula el estado visual del pedimento para badges en la vista list.

        Mapeo:
        - Sin número de pedimento → 'sin_pedimento'
        - Con número + estado draft → 'borrador'
        - Con número + estado done → 'validado'
        - Con número + estado cancel → 'cancelado'
        """
        for record in self:
            if not record.l10n_mx_edi_customs_number:
                record.pedimento_state_display = 'sin_pedimento'
            elif record.state == 'done':
                record.pedimento_state_display = 'validado'
            elif record.state == 'cancel':
                record.pedimento_state_display = 'cancelado'
            else:
                record.pedimento_state_display = 'borrador'

    # ========== VALIDACIÓN DE CONSTRAINTS ==========

    @api.constrains('l10n_mx_edi_customs_number', 'state')
    def _check_l10n_mx_edi_customs_number(self):
        """
        Override para omitir la validación de unicidad de l10n_mx_edi_landing.

        Permite múltiples costos en destino con el mismo número de pedimento
        siempre que sean del mismo proveedor. La validación de proveedor se
        maneja en purchase_order.py al confirmar órdenes.

        Solo mantiene:
        1. Validación de formato del número de pedimento
        2. Unicidad entre pedimentos validados (estado 'done')

        Raises:
            ValidationError: Si el formato es incorrecto o ya existe otro
                pedimento validado con el mismo número.
        """
        help_message = self._fields['l10n_mx_edi_customs_number'].help
        if help_message:
            help_message = help_message.split('\n', 1)[1] if '\n' in help_message else help_message
        else:
            help_message = ""

        for landed_cost in self:
            if not landed_cost.l10n_mx_edi_customs_number:
                continue

            custom_number = landed_cost.l10n_mx_edi_customs_number.strip()

            # Validación de formato
            if not CUSTOM_NUMBERS_PATTERN.match(custom_number):
                raise ValidationError(self.env._(
                    "¡Error! El formato del número de pedimento es incorrecto. \n%s\n"
                    "Ejemplo: 15  48  3009  0001234", help_message))

            # Validación de unicidad entre pedimentos validados
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

    # ========== ACCIONES DE CANCELACIÓN ==========

    def action_landed_cost_cancel(self):
        """
        Override para limpiar referencia pedimiento_id en órdenes de compra
        al cancelar un costo en destino.
        """
        self._clear_purchase_order_reference()
        return super().action_landed_cost_cancel()

    def action_landed_cost_cancel_draft(self):
        """
        Override para limpiar referencia pedimiento_id en órdenes de compra
        al resetear a borrador un costo en destino.
        """
        self._clear_purchase_order_reference()
        return super().action_landed_cost_cancel_draft()

    def action_landed_cost_cancel_delete(self):
        """
        Override para limpiar referencia pedimiento_id en órdenes de compra
        antes de eliminar un costo en destino.
        """
        self._clear_purchase_order_reference()
        return super().action_landed_cost_cancel_delete()

    def _clear_purchase_order_reference(self):
        """
        Limpia el campo pedimiento_id en todas las órdenes de compra que
        referencian estos costos en destino.

        Se usa antes de cancelar o eliminar un costo en destino para evitar
        referencias huérfanas en las órdenes de compra.
        """
        PurchaseOrder = self.env['purchase.order']
        for landed_cost in self:
            purchase_orders = PurchaseOrder.search([
                ('pedimiento_id', '=', landed_cost.id)
            ])
            if purchase_orders:
                purchase_orders.write({'pedimiento_id': False})

    # ========== CAMBIO DE NÚMERO DE PEDIMENTO ==========

    def action_change_pedimento_number(self):
        """
        Abre el wizard para cambiar el número de pedimento.

        Solo disponible cuando el landed cost tiene un número de pedimento
        asignado. Permite cambiar el número y propagar el cambio a todas
        las órdenes de compra asociadas.

        Returns:
            dict: Acción de ventana para abrir el wizard de cambio de número.
        """
        self.ensure_one()
        return {
            'name': _('Cambiar Número de Pedimento'),
            'type': 'ir.actions.act_window',
            'res_model': 'pedimento.change.number',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_landed_cost_id': self.id,
                'default_current_number': self.l10n_mx_edi_customs_number or '',
            },
        }

    # ========== VALIDACIÓN ==========

    def button_validate(self):
        """
        Override para registrar la validación en el log de auditoría.
        """
        # Ejecutar validación estándar (super)
        res = super().button_validate()

        # Registrar en auditoría si es un pedimento mexicano
        if self.l10n_mx_edi_customs_number and not self.env.context.get('skip_audit_log'):
            # Buscar las OCs asociadas a través de los pickings
            related_pos = self.picking_ids.mapped('purchase_id')
            
            if related_pos:
                log_details = []
                for po in related_pos:
                    log_details.append({
                        'record_name': po.name,
                        'record_model': 'purchase.order',
                        'record_id': po.id,
                        'landed_cost_name': self.name,
                        'customs_number': self.l10n_mx_edi_customs_number,
                        'result': 'exito',
                        'message': _("Validación individual exitosa."),
                    })
                
                self.env['pedimento.operation.log'].create_log(
                    operation_type='validacion',
                    is_bulk=False,
                    details=log_details,
                )
            else:
                # Fallback: registrar el LC si no hay OCs
                log_details = [{
                    'record_name': self.name,
                    'record_model': 'stock.landed.cost',
                    'record_id': self.id,
                    'landed_cost_name': self.name,
                    'customs_number': self.l10n_mx_edi_customs_number, 
                    'result': 'exito',
                    'message': _("Validación exitosa (Sin OC asociada)."),
                }]
                self.env['pedimento.operation.log'].create_log(
                    operation_type='validacion',
                    is_bulk=False,
                    details=log_details,
                )

        return res

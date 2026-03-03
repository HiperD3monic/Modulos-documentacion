# coding: utf-8
# ============================================================================
# MODELO EXTENDIDO: purchase.order
# ============================================================================
# Extensión del modelo de Órdenes de Compra para gestión de pedimentos
# aduanales mexicanos. Incluye:
#   - Campo de número de pedimento con validación de formato
#   - Creación/reutilización automática de costos en destino al confirmar
#   - Validación masiva de pedimentos con wizard de preview
#   - Validaciones centralizadas (facturas, CFDI, stock, períodos)
# ============================================================================

import re
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Patrón para números de pedimento aduanal mexicano
# Formato: YY  AA  PPPP  NNNNNNN (2 dígitos año + 2 aduana + 4 serie + 7 progresivo)
# Ejemplo: "15  48  3009  0001234"
CUSTOM_NUMBERS_PATTERN = re.compile(r'[0-9]{2}  [0-9]{2}  [0-9]{4}  [0-9]{7}')


class PurchaseOrder(models.Model):
    """
    Extensión de purchase.order para gestión de pedimentos aduanales.

    Agrega el campo de número de pedimento con validación de formato mexicano,
    lógica de creación automática de costos en destino al confirmar, y
    funcionalidad de validación masiva.
    """

    _inherit = 'purchase.order'

    # ========== CAMPOS DEL PEDIMENTO ==========

    l10n_mx_edi_customs_number = fields.Char(
        help='Campo opcional para el número de pedimento aduanal en caso de '
        'ventas de primera mano de mercancías importadas o en caso de '
        'operaciones de comercio exterior con bienes o servicios.\n'
        'El formato debe ser:\n'
        ' - 2 dígitos del año de validación seguidos de dos espacios.\n'
        ' - 2 dígitos de la aduana de despacho seguidos de dos espacios.\n'
        ' - 4 dígitos del número de serie seguidos de dos espacios.\n'
        ' - 1 dígito correspondiente al último dígito del año actual, '
        'salvo en caso de un consolidado iniciado en el año previo.\n'
        ' - 6 dígitos de la numeración progresiva del pedimento.',
        string='Número de Pedimento', size=21, copy=False,
    )

    fiscal_country_codes = fields.Char(
        related="company_id.country_code",
        string='Código Fiscal del País',
    )
    
    pedimento_log_count = fields.Integer(
        string='Logs de Pedimento',
        compute='_compute_pedimento_log_count',
    )

    pedimiento_id = fields.Many2one(
        comodel_name='stock.landed.cost',
        string='Pedimento',
        help='Costo en destino (landed cost) asociado a esta orden de compra.',
    )
    
    is_reverted = fields.Boolean(
        string='Pedimento Revertido',
        copy=False,
        default=False,
        help='Indica si esta orden fue revertida desde un pedimento validado.',
    )

    # ========== VALIDACIÓN DE FORMATO ==========

    @api.constrains('l10n_mx_edi_customs_number')
    def _check_l10n_mx_edi_customs_number(self):
        """
        Valida que el número de pedimento tenga el formato correcto.

        El formato requerido es: YY  AA  PPPP  NNNNNNN
        Donde:
            YY = Año de validación (2 dígitos)
            AA = Aduana de despacho (2 dígitos)
            PPPP = Número de serie (4 dígitos)
            NNNNNNN = Numeración progresiva (7 dígitos)

        Separados por doble espacio.

        Raises:
            ValidationError: Si el formato no coincide con el patrón esperado.
        """
        help_text = self._fields['l10n_mx_edi_customs_number'].help or ''
        help_message = help_text.split('\n', 1)[1] if '\n' in help_text else ''

        for purchase_order in self:
            if not purchase_order.l10n_mx_edi_customs_number:
                continue
            custom_number = purchase_order.l10n_mx_edi_customs_number.strip()
            if not CUSTOM_NUMBERS_PATTERN.match(custom_number):
                raise ValidationError(self.env._(
                    "¡Error! El formato del número de pedimento es incorrecto. \n%s\n"
                    "Ejemplo: 15  48  3009  0001234", help_message))

    # ========== CONFIRMACIÓN DE ORDEN ==========

    def button_confirm(self):
        """
        Override para crear o reutilizar un costo en destino al confirmar
        la orden de compra.

        Lógica:
            1. Bloquear si el número de pedimento ya está usado en un costo
               en destino validado (estado 'done').
            2. Bloquear si existe un borrador con ese número pero de otro
               proveedor (diferente partner).
            3. Reutilizar costo en destino borrador existente con mismo número
               y mismo proveedor.
            4. Crear nuevo costo en destino si no existe ninguno.

        Returns:
            El resultado del super().button_confirm().

        Raises:
            ValidationError: Si el número de pedimento ya está validado o
                pertenece a otro proveedor.
        """
        StockLandedCost = self.env['stock.landed.cost']

        # ========== VALIDACIONES PRE-CONFIRMACIÓN ==========
        for order in self:
            if not order.l10n_mx_edi_customs_number or order.pedimiento_id:
                continue

            # Verificar si el pedimento ya está validado
            validated_pedimento = StockLandedCost.search([
                ('l10n_mx_edi_customs_number', '=', order.l10n_mx_edi_customs_number),
                ('state', '=', 'done'),
            ], limit=1)

            if validated_pedimento:
                raise ValidationError(_(
                    "El número de pedimento '%s' ya ha sido validado en el costo en "
                    "destino '%s'. No se puede reutilizar un número de pedimento que "
                    "ya fue procesado.",
                    order.l10n_mx_edi_customs_number,
                    validated_pedimento.name
                ))

            # Verificar si hay borrador con otro proveedor
            draft_pedimento = StockLandedCost.search([
                ('l10n_mx_edi_customs_number', '=', order.l10n_mx_edi_customs_number),
                ('state', '=', 'draft'),
            ], limit=1)

            if draft_pedimento:
                existing_partners = draft_pedimento.picking_ids.mapped('partner_id')
                if existing_partners and order.partner_id not in existing_partners:
                    partner_names = ', '.join(existing_partners.mapped('name'))
                    raise ValidationError(_(
                        "El número de pedimento '%s' ya está siendo utilizado en el "
                        "costo en destino '%s' con el proveedor '%s'. No se puede "
                        "usar el mismo número de pedimento con un proveedor diferente.",
                        order.l10n_mx_edi_customs_number,
                        draft_pedimento.name,
                        partner_names
                    ))

        # ========== CONFIRMACIÓN ==========
        # Si se confirma de nuevo, limpiar la marca de revertido
        self.write({'is_reverted': False})
        res = super().button_confirm()

        # ========== CREACIÓN/REUTILIZACIÓN DE PEDIMENTO ==========
        for order in self:
            if not order.l10n_mx_edi_customs_number:
                continue

            # Si ya tiene pedimento, solo agregar transferencias nuevas
            if order.pedimiento_id:
                order._add_pickings_to_pedimiento()
                continue

            # Buscar borrador existente con mismo número (proveedor ya validado arriba)
            existing_pedimento = StockLandedCost.search([
                ('l10n_mx_edi_customs_number', '=', order.l10n_mx_edi_customs_number),
                ('state', '=', 'draft'),
            ], limit=1)

            if existing_pedimento:
                # Reutilizar pedimento existente del mismo proveedor
                order.pedimiento_id = existing_pedimento.id
                order._add_pickings_to_pedimiento()
                continue

            # Crear nuevo costo en destino
            picking_ids = order.picking_ids.ids if order.picking_ids else []
            pedimento = StockLandedCost.with_company(order.company_id).create({
                'l10n_mx_edi_customs_number': order.l10n_mx_edi_customs_number,
                'target_model': 'picking',
                'picking_ids': [(6, 0, picking_ids)],
            })
            order.pedimiento_id = pedimento.id

        return res

    # ========== GESTIÓN DE TRANSFERENCIAS ==========

    def _add_pickings_to_pedimiento(self):
        """
        Agrega las transferencias de esta orden al pedimento asociado.

        Solo agrega transferencias que no estén ya asociadas al pedimento
        para evitar duplicados.
        """
        self.ensure_one()
        if not self.pedimiento_id or not self.picking_ids:
            return

        existing_picking_ids = set(self.pedimiento_id.picking_ids.ids)
        for picking in self.picking_ids:
            if picking.id not in existing_picking_ids:
                self.pedimiento_id.write({
                    'picking_ids': [(4, picking.id)]
                })

    # ========== NAVEGACIÓN ==========

    def action_open_pedimiento(self):
        """
        Abre la vista form del pedimento (costo en destino) asociado.

        Returns:
            dict: Acción de ventana para abrir el pedimento.
        """
        self.ensure_one()
        return {
            'name': _('Pedimento'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.landed.cost',
            'view_mode': 'form',
            'target': 'new',
            'res_id': self.pedimiento_id.id,
            'context': {'hide_change_button': True},
        }

    def _compute_pedimento_log_count(self):
        """Calcula el número de logs asociados a esta orden."""
        for order in self:
            order.pedimento_log_count = self.env['pedimento.operation.log'].search_count([
                ('purchase_order_ids', 'in', order.id)
            ])

    def action_view_pedimento_logs(self):
        """Abre la vista de lista de los logs asociados a esta orden."""
        self.ensure_one()
        return {
            'name': _('Historial de Operaciones'),
            'type': 'ir.actions.act_window',
            'res_model': 'pedimento.operation.log',
            'view_mode': 'list,form',
            'domain': [('purchase_order_ids', 'in', self.id)],
            'context': {
                'create': False,
                'disable_log_links': True,  # Disable links in details HTML
            },
        }

    # ========== VALIDACIÓN MASIVA DE PEDIMENTOS ==========

    def action_validate_pedimentos_bulk(self):
        """
        Valida pedimentos para múltiples órdenes de compra seleccionadas.

        En lugar de ejecutar directamente, abre el wizard de preview para
        que el usuario pueda revisar qué se va a procesar antes de confirmar.

        Returns:
            dict: Acción de ventana para abrir el wizard de preview.
        """
        if not self:
            return {'type': 'ir.actions.act_window_close'}

        return self.env['pedimento.operation.wizard'].action_open_preview(
            operation_type='validacion',
            source_model='purchase.order',
            source_ids=self.ids,
        )

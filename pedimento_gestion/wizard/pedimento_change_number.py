# coding: utf-8
# ============================================================================
# WIZARD: Cambio de Número de Pedimento
# ============================================================================
# Wizard transient para cambiar el número de pedimento en un costo en
# destino (stock.landed.cost) y propagarlo a todas las órdenes de compra
# y líneas de movimiento de stock asociadas.
# ============================================================================

import re
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Patrón para números de pedimento aduanal mexicano
CUSTOM_NUMBERS_PATTERN = re.compile(r'[0-9]{2}  [0-9]{2}  [0-9]{4}  [0-9]{7}')


class PedimentoChangeNumber(models.TransientModel):
    """
    Wizard para cambiar el número de pedimento en un costo en destino.

    Se abre desde el botón "Cambiar Pedimento" en la vista form de
    stock.landed.cost. Permite ingresar un nuevo número de pedimento y
    propagarlo a:
    - El propio costo en destino (stock.landed.cost)
    - Todas las órdenes de compra (purchase.order) vinculadas
    - Las líneas de movimiento de stock (stock.move.line) relacionadas

    Registra la operación en el log de auditoría.
    """

    _name = 'pedimento.change.number'
    _description = 'Cambiar Número de Pedimento'

    # ========== CAMPOS ==========

    landed_cost_id = fields.Many2one(
        comodel_name='stock.landed.cost',
        string='Costo en Destino',
        required=True,
        readonly=True,
    )

    current_number = fields.Char(
        string='Número Actual',
        readonly=True,
    )

    new_number = fields.Char(
        string='Nuevo Número de Pedimento',
        required=True,
        size=21,
        help='Ingrese el nuevo número de pedimento con el formato:\n'
             'YY  AA  PPPP  NNNNNNN\n'
             'Ejemplo: 15  48  3009  0001234',
    )

    # Campos informativos
    purchase_order_count = fields.Integer(
        string='Órdenes de Compra Afectadas',
        compute='_compute_affected_records',
    )

    purchase_order_names = fields.Char(
        string='Órdenes de Compra',
        compute='_compute_affected_records',
    )

    # ========== COMPUTADOS ==========

    @api.depends('landed_cost_id')
    def _compute_affected_records(self):
        """Calcula las órdenes de compra que serán afectadas por el cambio."""
        for wizard in self:
            if wizard.landed_cost_id:
                pos = self.env['purchase.order'].search([
                    ('pedimiento_id', '=', wizard.landed_cost_id.id)
                ])
                wizard.purchase_order_count = len(pos)
                wizard.purchase_order_names = ', '.join(pos.mapped('name')) if pos else _('Ninguna')
            else:
                wizard.purchase_order_count = 0
                wizard.purchase_order_names = _('Ninguna')

    # ========== VALIDACIÓN ==========

    @api.constrains('new_number')
    def _check_new_number_format(self):
        """Valida formato del nuevo número de pedimento."""
        for wizard in self:
            if wizard.new_number:
                new_num = wizard.new_number.strip()
                if not CUSTOM_NUMBERS_PATTERN.match(new_num):
                    raise ValidationError(_(
                        "El formato del nuevo número de pedimento es incorrecto.\n"
                        "Formato requerido: YY  AA  PPPP  NNNNNNN\n"
                        "Ejemplo: 15  48  3009  0001234"
                    ))

    # ========== ACCIÓN PRINCIPAL ==========

    def action_change_number(self):
        """
        Ejecuta el cambio de número de pedimento.

        1. Valida el nuevo número
        2. Verifica que no esté en uso en otro pedimento validado
        3. Actualiza el número en el costo en destino
        4. Propaga a las órdenes de compra vinculadas
        5. Propaga a las líneas de movimiento de stock
        6. Registra en el log de auditoría

        Returns:
            dict: Acción para cerrar el wizard.
        """
        self.ensure_one()
        lc = self.landed_cost_id
        new_num = self.new_number.strip()
        # Usar el valor real del LC y limpiar espacios para comparar
        old_num = (lc.l10n_mx_edi_customs_number or '').strip()

        # Validar que sea diferente
        if new_num == old_num:
            raise UserError(_("El nuevo número es igual al número actual."))

        # Validar que no exista otro pedimento validado con ese número
        existing = self.env['stock.landed.cost'].search([
            ('l10n_mx_edi_customs_number', '=', new_num),
            ('state', '=', 'done'),
            ('id', '!=', lc.id),
        ], limit=1)

        if existing:
            raise UserError(_(
                "El número de pedimento '%s' ya está validado en el costo en "
                "destino '%s'. No se puede reutilizar.",
                new_num, existing.name
            ))

        # ========== EJECUTAR CAMBIO ==========

        # 1. Actualizar el costo en destino
        lc.write({'l10n_mx_edi_customs_number': new_num})

        # 2. Actualizar órdenes de compra vinculadas
        purchase_orders = self.env['purchase.order'].search([
            ('pedimiento_id', '=', lc.id)
        ])
        if purchase_orders:
            purchase_orders.write({'l10n_mx_edi_customs_number': new_num})

        # 3. Actualizar líneas de movimiento de stock (stock.move.line)
        # en los pickings del landed cost (solo si el campo existe)
        if 'l10n_mx_edi_customs_number' in self.env['stock.move.line']._fields:
            for picking in lc.picking_ids:
                move_lines = picking.move_line_ids.filtered(
                    lambda ml: ml.l10n_mx_edi_customs_number == old_num
                )
                if move_lines:
                    move_lines.write({'l10n_mx_edi_customs_number': new_num})

        # 4. Actualizar líneas de factura (account.move.line)
        # Buscar TODAS las líneas de factura que tengan el número antiguo
        invoice_lines = self.env['account.move.line'].sudo().search([
            ('l10n_mx_edi_customs_number', 'like', old_num),
        ])
        updated_invoice_count = 0
        if invoice_lines:
            for inv_line in invoice_lines:
                # Reemplazar el número antiguo por el nuevo en el campo
                # (puede contener múltiples números separados por coma)
                current_val = inv_line.l10n_mx_edi_customs_number or ''
                new_val = current_val.replace(old_num, new_num)
                # Usar SQL directo para evitar constrains en facturas publicadas
                self.env.cr.execute(
                    "UPDATE account_move_line SET l10n_mx_edi_customs_number = %s WHERE id = %s",
                    (new_val, inv_line.id)
                )
                updated_invoice_count += 1

            self.env.cr.flush()
            invoice_lines.invalidate_recordset(['l10n_mx_edi_customs_number'])
            _logger.info(
                "Pedimento actualizado en %d líneas de factura: '%s' → '%s'",
                updated_invoice_count, old_num, new_num,
            )

        # ========== REGISTRO EN AUDITORÍA ==========
        log_details = [{
            'record_name': lc.name,
            'record_model': 'stock.landed.cost',
            'record_id': lc.id,
            'landed_cost_name': lc.name,
            'customs_number': new_num,
            'result': 'exito',
            'message': _(
                "Número cambiado de '%s' a '%s'. "
                "%d órdenes de compra y %d líneas de factura actualizadas.",
                old_num, new_num, len(purchase_orders), updated_invoice_count
            ),
        }]

        # Agregar detalle de cada PO afectada
        for po in purchase_orders:
            log_details.append({
                'record_name': po.name,
                'record_model': 'purchase.order',
                'record_id': po.id,
                'landed_cost_name': lc.name,
                'customs_number': new_num,
                'result': 'exito',
                'message': _(
                    "Número de pedimento actualizado de '%s' a '%s'.",
                    old_num, new_num
                ),
            })

        self.env['pedimento.operation.log'].create_log(
            operation_type='cambio_numero',
            is_bulk=False,
            details=log_details,
            notes=_(
                "Cambio de número de pedimento en %s: '%s' → '%s'",
                lc.name, old_num, new_num
            ),
        )

        return {'type': 'ir.actions.act_window_close'}

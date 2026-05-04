# -*- coding: utf-8 -*-
from odoo import api, fields, models

import logging

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    is_exchange_payment = fields.Boolean(
        string='Es pago de intercambio',
        default=False,
        help='Indica que esta orden solo cobra la diferencia de un intercambio. '
             'Los pickings de inventario ya fueron creados por create_exchange() '
             'y NO deben duplicarse al procesar esta orden.'
    )

    custom_return_done = fields.Boolean(
        string='Devolución procesada',
        default=False,
        help='Indica que esta orden ya fue devuelta a través del módulo pos_return. '
             'Se usa para mostrar la etiqueta en el POS y bloquear reembolsos nativos.'
    )

    custom_exchange_done = fields.Boolean(
        string='Intercambio procesado',
        default=False,
        help='Indica que esta orden ya fue intercambiada a través del módulo pos_return. '
             'Se usa para mostrar la etiqueta en el POS y bloquear reembolsos nativos.'
    )

    custom_exchange_replaced = fields.Boolean(
        string='Reemplazado por intercambio',
        default=False,
        help='Indica que esta orden fue reemplazada por un nuevo ticket de pago de intercambio. '
             'Se usa para ocultar el ticket original del popup de devoluciones.'
    )

    # =====================================================================
    # Computed fields for the Return/Exchange audit tab
    # =====================================================================
    return_picking_ids = fields.One2many(
        'stock.picking', compute='_compute_return_exchange_pickings',
        string='Recepciones (Devoluciones)',
        help='Pickings de entrada creados por devoluciones de esta orden.'
    )
    exchange_picking_in_ids = fields.One2many(
        'stock.picking', compute='_compute_return_exchange_pickings',
        string='Recepciones (Intercambio)',
        help='Pickings de entrada creados por intercambios de esta orden.'
    )
    exchange_picking_out_ids = fields.One2many(
        'stock.picking', compute='_compute_return_exchange_pickings',
        string='Entregas (Intercambio)',
        help='Pickings de salida creados por intercambios de esta orden.'
    )
    return_picking_count = fields.Integer(
        compute='_compute_return_exchange_pickings',
        string='Nº Recepciones Dev.',
    )
    exchange_picking_count = fields.Integer(
        compute='_compute_return_exchange_pickings',
        string='Nº Pickings Intercambio',
    )
    return_exchange_status = fields.Selection([
        ('none', 'Sin operaciones'),
        ('returned', 'Devuelto'),
        ('exchanged', 'Intercambiado'),
        ('exchange_replaced', 'Reemplazado por intercambio'),
        ('exchange_payment', 'Pago de intercambio'),
    ], compute='_compute_return_exchange_status', string='Estado Dev./Int.',
        store=False)

    @api.depends('pos_reference', 'name')
    def _compute_return_exchange_pickings(self):
        for order in self:
            refs = [r for r in [order.pos_reference, order.name] if r]
            if not refs:
                order.return_picking_ids = self.env['stock.picking']
                order.exchange_picking_in_ids = self.env['stock.picking']
                order.exchange_picking_out_ids = self.env['stock.picking']
                order.return_picking_count = 0
                order.exchange_picking_count = 0
                continue

            # All incoming pickings with this order's reference as origin.
            # This includes BOTH return receipts AND exchange receipts,
            # because _create_return_receipt() uses origin_ref directly
            # (no INT: prefix) for both returns and exchanges.
            all_incoming = self.env['stock.picking'].search([
                ('origin', 'in', refs),
                ('picking_type_id.code', '=', 'incoming'),
            ])

            # Exchange delivery pickings use INT: prefix in origin
            int_refs = ['INT:' + r for r in refs]
            exchange_out = self.env['stock.picking'].search([
                ('origin', 'in', int_refs),
                ('picking_type_id.code', '=', 'outgoing'),
            ])

            # Legacy: check outgoing pickings with direct ref (before INT: convention)
            if not exchange_out:
                pos_picking_type = order.session_id.config_id.picking_type_id if order.session_id else False
                legacy_domain = [
                    ('origin', 'in', refs),
                    ('picking_type_id.code', '=', 'outgoing'),
                ]
                if pos_picking_type:
                    legacy_domain.append(('picking_type_id', '!=', pos_picking_type.id))
                exchange_out = self.env['stock.picking'].search(legacy_domain)

            # If exchange deliveries exist, try to separate exchange receipts
            # from pure return receipts using the note field
            exchange_in = self.env['stock.picking']
            return_only = all_incoming
            if exchange_out:
                # Exchange receipts are the ones created alongside deliveries.
                # We use the note content as heuristic - exchange notes contain
                # "Intercambio" or "EXCHANGE" metadata
                exchange_in_list = []
                return_only_list = []
                for p in all_incoming:
                    note = (p.note or '').lower()
                    if 'intercambio' in note or 'exchange' in note:
                        exchange_in_list.append(p.id)
                    else:
                        return_only_list.append(p.id)
                exchange_in = self.env['stock.picking'].browse(exchange_in_list)
                return_only = self.env['stock.picking'].browse(return_only_list)

            order.return_picking_ids = return_only
            order.exchange_picking_in_ids = exchange_in
            order.exchange_picking_out_ids = exchange_out
            order.return_picking_count = len(return_only)
            order.exchange_picking_count = len(exchange_in) + len(exchange_out)


    @api.depends('custom_return_done', 'custom_exchange_done',
                 'custom_exchange_replaced', 'is_exchange_payment')
    def _compute_return_exchange_status(self):
        for order in self:
            if order.is_exchange_payment:
                order.return_exchange_status = 'exchange_payment'
            elif order.custom_exchange_replaced:
                order.return_exchange_status = 'exchange_replaced'
            elif order.custom_exchange_done:
                order.return_exchange_status = 'exchanged'
            elif order.custom_return_done:
                order.return_exchange_status = 'returned'
            else:
                order.return_exchange_status = 'none'

    def init(self):
        """Force-create database columns if they don't exist.
        
        This ensures the columns are created even if the module wasn't
        properly upgraded via -u flag. Runs on every server start.
        """
        self.env.cr.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'pos_order' AND column_name = 'custom_return_done'
                ) THEN
                    ALTER TABLE pos_order ADD COLUMN custom_return_done boolean DEFAULT false;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'pos_order' AND column_name = 'custom_exchange_done'
                ) THEN
                    ALTER TABLE pos_order ADD COLUMN custom_exchange_done boolean DEFAULT false;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'pos_order' AND column_name = 'custom_exchange_replaced'
                ) THEN
                    ALTER TABLE pos_order ADD COLUMN custom_exchange_replaced boolean DEFAULT false;
                END IF;
            END $$;
        """)
        _logger.info("pos_return: init() verified custom columns on pos_order")

    def _create_order_picking(self):
        """Override to skip picking creation for exchange payment orders.

        When an exchange requires the customer to pay a difference (new_total > return_total),
        a POS order is created with the actual product lines to show a proper receipt and
        process the payment. However, the inventory movements (incoming picking for returned
        products, outgoing picking for new products) have ALREADY been created and validated
        by pos.session.create_exchange() BEFORE this order exists.

        Without this override, the native _create_order_picking() would create DUPLICATE
        stock pickings for the same products, corrupting the inventory:
        - Returned products would enter inventory TWICE
        - New products would leave inventory TWICE

        See: pos.session.create_exchange() and pos.session._create_picking_at_end_of_session()
        """
        if self.is_exchange_payment:
            _logger.info(
                "POS Exchange: Skipping picking creation for exchange payment order %s "
                "(pickings already created by create_exchange)",
                self.name
            )
            return
        return super()._create_order_picking()

    # =====================================================================
    # Action methods for stat buttons
    # =====================================================================
    def action_view_return_pickings(self):
        """Open the return pickings (incoming) for this order."""
        self.ensure_one()
        pickings = self.return_picking_ids
        action = {
            'type': 'ir.actions.act_window',
            'name': 'Recepciones de Devolución',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', pickings.ids)],
            'context': {'create': False},
        }
        if len(pickings) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = pickings.id
        return action

    def action_view_exchange_pickings(self):
        """Open the exchange pickings (in + out) for this order."""
        self.ensure_one()
        pickings = self.exchange_picking_in_ids | self.exchange_picking_out_ids
        action = {
            'type': 'ir.actions.act_window',
            'name': 'Pickings de Intercambio',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', pickings.ids)],
            'context': {'create': False},
        }
        if len(pickings) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = pickings.id
        return action


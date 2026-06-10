# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

import json
import logging
import re

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    # Legacy audit fields — kept for backward compatibility and historical data
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
        ('partial', 'Devuelto Parcial'),
        ('returned', 'Devuelto'),
        ('exchanged', 'Intercambiado'),
        ('exchange_replaced', 'Reemplazado por intercambio'),
        ('refund', 'Reembolso'),
        ('exchange_order', 'Orden de Intercambio'),
    ], compute='_compute_return_exchange_status', string='Estado Dev./Int.',
        store=False)

    # Computed fields to display the return type and reason in the backend
    custom_return_type_display = fields.Char(
        compute='_compute_return_type_info',
        string='Tipo de Operación',
    )
    custom_return_reason = fields.Char(
        compute='_compute_return_type_info',
        string='Razón / Referencia',
    )

    # Link to refund orders (for navigation from original → refunds)
    refund_order_ids = fields.One2many(
        'pos.order', compute='_compute_refund_order_ids',
        string='Órdenes de Reembolso',
        help='Órdenes de reembolso creadas a partir de esta orden.'
    )
    refund_order_count = fields.Integer(
        compute='_compute_refund_order_ids',
        string='Nº Reembolsos',
    )

    # Remaining products summary for original tickets with partial returns
    original_remaining_summary = fields.Html(
        compute='_compute_original_remaining_summary',
        string='Productos Restantes',
    )

    # Link to the original order for exchange orders (V2 native)
    exchange_origin_order_id = fields.Many2one(
        'pos.order', compute='_compute_exchange_origin',
        string='Ticket Original del Intercambio',
    )
    exchange_origin_ref = fields.Char(
        compute='_compute_exchange_origin',
        string='Ref. Ticket Original',
    )

    # =====================================================================
    # Override write to prevent is_refund from being reset to False
    # =====================================================================
    def write(self, vals):
        if 'is_refund' in vals and not vals['is_refund']:
            # Prevent resetting is_refund to False for refund orders
            orders_to_protect = self.filtered(
                lambda o: o.is_refund or o.amount_total < 0
            )
            if orders_to_protect:
                _logger.debug(
                    "pos_return: Blocked is_refund=False for orders: %s",
                    orders_to_protect.mapped('pos_reference')
                )
                vals = dict(vals)
                del vals['is_refund']
                if not vals:
                    return True
        return super().write(vals)

    # =====================================================================
    # Override _compute_order_name to prefix "REEMBOLSO DE" for refund
    # orders without refunded_order_id (Arus / Sin Ticket returns)
    # =====================================================================
    def _compute_order_name(self, session=None):
        """Prefix order name for custom returns and exchanges.
        
        Native Odoo refunds get "REEMBOLSO DE {original}" from
        refunded_order_id. Our custom Arus/Sin Ticket returns have
        is_refund=True but no refunded_order_id, so we add the prefix
        here to keep the backend list view consistent with the POS.
        
        Exchange orders (mixed positive/negative lines) get a CLEAN
        "INTERCAMBIO DE <session> - <sequence>" name instead of
        chaining "REEMBOLSO DE" prefixes from Odoo's native naming.
        This ensures each exchange in a chain has its own readable name.
        """
        # Detect exchange orders: has both positive AND negative lines
        has_positive = any(l.qty > 0 for l in self.lines)
        has_negative = any(l.qty < 0 for l in self.lines)
        is_exchange = has_positive and has_negative

        # Helper to generate a clean base name from session config + sequence
        def _clean_base_name():
            sess = session or self.session_id
            last_ref = self.get_reference_last_part()
            pfx = sess.config_id.order_seq_id.prefix or sess.config_id.name
            sfx = f" - {sess.config_id.order_seq_id.suffix}" if sess.config_id.order_seq_id.suffix else ''
            return f"{pfx} - {last_ref}{sfx}"

        if is_exchange:
            # Generate a clean name: "INTERCAMBIO DE <config> - <sequence>"
            # instead of using super() which would chain "REEMBOLSO DE..."
            return _("INTERCAMBIO DE %(name)s", name=_clean_base_name())

        # For pure refunds with refunded_order_id: generate clean name
        # to prevent chaining like "REEMBOLSO DE INTERCAMBIO DE INTERCAMBIO DE..."
        if self.refunded_order_id.exists():
            return _("REEMBOLSO DE %(name)s", name=_clean_base_name())

        name = super()._compute_order_name(session)

        if (self.amount_total < 0
                and 'REFUND' not in name.upper()
                and 'REEMBOLSO' not in name.upper()):
            # Pure refund without original order (Arus/Sin Ticket)
            name = _("REEMBOLSO DE %(name)s", name=name)

        return name

    # =====================================================================
    # Override _process_saved_order to ensure is_refund is set for refunds
    # and mark original orders as returned when refund is paid
    # =====================================================================
    def _process_saved_order(self, draft):
        """Set is_refund for orders with negative totals before processing.
        Also marks the original order as returned when a refund is paid.
        
        Succession pattern (applies to BOTH exchanges AND partial returns):
        - Original is ALWAYS marked as done (custom_return_done or custom_exchange_done)
        - The new ticket inherits remaining products via _build_inherited_lines_from_original
        - Partial refund tickets keep is_refund=False so they remain searchable
        - Full refund tickets get is_refund=True (nothing left to return)
        """
        # ── Double-refund guard ──
        # Before processing, verify that the quantities being refunded
        # don't exceed the actual remaining quantities on the original order.
        # This prevents race conditions when two return orders are created
        # simultaneously for the same ticket.
        if not draft:
            self._validate_refund_quantities()

        result = super()._process_saved_order(draft)

        if not draft:
            # Detect if this is an exchange (has both positive AND negative lines)
            has_positive = any(l.qty > 0 for l in self.lines)
            has_negative = any(l.qty < 0 for l in self.lines)
            is_exchange = has_positive and has_negative

            # ── Find original orders first to determine remaining products ──
            original_order_ids = set()
            for line in self.lines:
                if line.refunded_orderline_id:
                    original_order_ids.add(line.refunded_orderline_id.order_id.id)

            # Also include the source ticket from internal_note Ref: field.
            # This is critical for returns from exchange tickets: the inherited
            # lines' refunded_orderline_id may point to a grandparent order,
            # skipping the intermediate exchange ticket whose own new products
            # would otherwise be lost from the remaining products calculation.
            try:
                note_tags = json.loads(self.internal_note) if self.internal_note else []
                for tag in note_tags:
                    text = tag.get('text', '') if isinstance(tag, dict) else ''
                    ref_match = re.search(r'Ref:\s*(\S+)', text)
                    if ref_match:
                        source_ticket = self.env['pos.order'].search([
                            ('pos_reference', '=', ref_match.group(1)),
                        ], limit=1)
                        if source_ticket:
                            original_order_ids.add(source_ticket.id)
                        break
            except (json.JSONDecodeError, TypeError):
                pass

            # Check if original still has remaining products after this operation.
            # Follow the entire ancestor chain (not just direct parents) to catch
            # inherited products from grandparent orders in multi-step scenarios.
            has_remaining_on_original = False
            remaining_products = []
            if original_order_ids:
                visited = {self.id}
                orders_to_scan = set(original_order_ids)
                while orders_to_scan:
                    current_id = orders_to_scan.pop()
                    if current_id in visited:
                        continue
                    visited.add(current_id)
                    ancestor = self.env['pos.order'].browse(current_id)
                    if not ancestor.exists():
                        continue
                    for orig_line in ancestor.lines:
                        if orig_line.qty <= 0:
                            continue
                        total_refunded = sum(
                            abs(rl.qty) for rl in orig_line.refund_orderline_ids
                            if rl.order_id.state not in ('cancel', 'draft')
                        )
                        remaining = orig_line.qty - total_refunded
                        if remaining > 0:
                            has_remaining_on_original = True
                            product_name = re.sub(
                                r'^\[.*?\]\s*', '',
                                orig_line.product_id.display_name
                            )
                            remaining_products.append(
                                "%s     %d" % (product_name, int(remaining))
                            )
                    # If ancestor is itself an exchange, follow its chain further
                    is_ancestor_exchange = (
                        any(l.qty > 0 for l in ancestor.lines)
                        and any(l.qty < 0 for l in ancestor.lines)
                    )
                    if is_ancestor_exchange:
                        for anc_line in ancestor.lines:
                            if anc_line.refunded_orderline_id:
                                gp_id = anc_line.refunded_orderline_id.order_id.id
                                if gp_id not in visited:
                                    orders_to_scan.add(gp_id)

            # ── Add "Productos en posesión" note for BOTH returns AND exchanges ──
            # When a partial return or partial exchange leaves products with the
            # customer, the new ticket should show what the customer still has.
            if has_remaining_on_original and remaining_products:
                possession_text = (
                    "Productos en posesión del cliente:\n"
                    + "\n".join(remaining_products)
                )
                # internal_note is JSON (array of tag objects) — add as a tag
                try:
                    tags = json.loads(self.internal_note) if self.internal_note else []
                except (json.JSONDecodeError, TypeError):
                    tags = []
                tags.append({
                    "text": possession_text,
                    "colorIndex": 2,
                })
                self.internal_note = json.dumps(tags)

            # ── is_refund persistence (returns only — exchanges are NEVER is_refund) ──
            if self.amount_total < 0 and not is_exchange:
                if has_remaining_on_original:
                    # Partial return: keep searchable (is_refund = False)
                    # Override any is_refund that frontend may have set
                    self.env.cr.execute(
                        "UPDATE pos_order SET is_refund = false WHERE id = %s",
                        [self.id]
                    )
                    self.invalidate_recordset(['is_refund'], flush=False)

                    _logger.info(
                        "pos_return: Partial return %s — kept searchable (is_refund=False)",
                        self.pos_reference
                    )
                else:
                    # Full return: mark as refund (not searchable)
                    self.env.cr.execute(
                        "UPDATE pos_order SET is_refund = true WHERE id = %s",
                        [self.id]
                    )
                    self.invalidate_recordset(['is_refund'], flush=False)


            # ── Mark original order as returned/exchanged ──
            # The new ticket (refund or exchange) becomes the successor
            # Collect IDs of all marked orders so sync_from_ui can include
            # them in the response, ensuring the frontend gets fresh data.
            marked_order_ids = []

            if original_order_ids:
                original_orders = self.env['pos.order'].browse(list(original_order_ids))
                for orig in original_orders:
                    if is_exchange:
                        if not orig.custom_exchange_done:
                            orig.custom_exchange_done = True
                            marked_order_ids.append(orig.id)
                            _logger.info(
                                "pos_return: Marked EXCHANGED: order %s (id=%s) by exchange %s",
                                orig.pos_reference, orig.id, self.pos_reference
                            )
                    else:
                        # ALWAYS mark original as returned — the refund ticket
                        # inherits remaining products via succession pattern
                        if not orig.custom_return_done:
                            orig.custom_return_done = True
                            marked_order_ids.append(orig.id)
                            _logger.info(
                                "pos_return: Marked RETURNED: order %s (id=%s) by %s%s",
                                orig.pos_reference, orig.id, self.pos_reference,
                                " (partial — successor inherits remaining)" if has_remaining_on_original else " (full)"
                            )

            # ── Mark intermediate successor tickets as returned ──
            # When returning from a successor ticket (e.g., 2nd partial return),
            # refunded_orderline_ids point to the ORIGINAL ticket, NOT the
            # intermediate successor. We need to find and mark the successor
            # that was the actual source of this return.
            # We do this by extracting the ticket reference from internal_note.
            source_ref = None
            try:
                tags = json.loads(self.internal_note) if self.internal_note else []
                for tag in tags:
                    text = tag.get('text', '') if isinstance(tag, dict) else ''
                    # Match "Devolución - ... | Ref: XXXX" pattern
                    ref_match = re.search(r'Ref:\s*(\S+)', text)
                    if ref_match:
                        source_ref = ref_match.group(1)
                        break
            except (json.JSONDecodeError, TypeError):
                pass

            if source_ref:
                # Find the source ticket and mark it if it's a successor
                # (not the same as the originals we already marked)
                done_field = 'custom_exchange_done' if is_exchange else 'custom_return_done'
                source_order = self.env['pos.order'].search([
                    ('pos_reference', '=', source_ref),
                    ('id', 'not in', list(original_order_ids) if original_order_ids else []),
                    (done_field, '=', False),
                ], limit=1)
                if source_order:
                    if is_exchange:
                        source_order.custom_exchange_done = True
                    else:
                        source_order.custom_return_done = True
                    marked_order_ids.append(source_order.id)
                    _logger.info(
                        "pos_return: Marked %s (successor): order %s (id=%s) by %s",
                        'EXCHANGED' if is_exchange else 'RETURNED',
                        source_order.pos_reference, source_order.id, self.pos_reference
                    )


        return result

    # =====================================================================
    # Override sync_from_ui to include marked predecessors in the response
    # =====================================================================
    @api.model
    def sync_from_ui(self, orders):
        """Extend sync_from_ui to include predecessor/successor orders that
        were marked as returned or exchanged during _process_saved_order.

        The native sync_from_ui returns data for the newly created orders
        and their direct refund targets (via refunded_orderline_id).
        However, intermediate successor tickets (e.g., a 1st partial return
        that was then itself returned) are NOT in the response.

        This override scans the synced refund orders, finds any predecessor
        tickets that were just marked, and includes them in the response.
        """
        result = super().sync_from_ui(orders)

        # Collect IDs already in the response
        existing_ids = set()
        synced_orders_data = result.get('pos.order', [])
        for order_data in synced_orders_data:
            if isinstance(order_data, dict) and 'id' in order_data:
                existing_ids.add(order_data['id'])

        if not existing_ids:
            return result

        # For each synced order, check if it references a predecessor ticket
        # via internal_note "Ref: XXXX" that should now show as 'Devuelto'.
        extra_ids = set()
        synced_order_records = self.env['pos.order'].browse(list(existing_ids))

        for order in synced_order_records:
            # Skip non-refund orders
            if order.amount_total >= 0 and not any(l.qty < 0 for l in order.lines):
                continue

            # Parse internal_note for "Ref: XXXX" to find predecessor
            try:
                tags = json.loads(order.internal_note) if order.internal_note else []
                for tag in tags:
                    text = tag.get('text', '') if isinstance(tag, dict) else ''
                    ref_match = re.search(r'Ref:\s*(\S+)', text)
                    if ref_match:
                        source_ref = ref_match.group(1)
                        source_order = self.env['pos.order'].search([
                            ('pos_reference', '=', source_ref),
                            ('id', 'not in', list(existing_ids)),
                            '|',
                            ('custom_return_done', '=', True),
                            ('custom_exchange_done', '=', True),
                        ], limit=1)
                        if source_order:
                            extra_ids.add(source_order.id)
                        break
            except (json.JSONDecodeError, TypeError):
                pass

            # Also check original orders via refunded_orderline_id that are
            # already in the response (native handles this) — but check for
            # any originals that got marked and are NOT yet in response
            for line in order.lines:
                if line.refunded_orderline_id:
                    orig = line.refunded_orderline_id.order_id
                    if orig.id not in existing_ids and (
                        orig.custom_return_done or orig.custom_exchange_done
                    ):
                        extra_ids.add(orig.id)

        if not extra_ids:
            return result

        extra_orders = self.env['pos.order'].browse(list(extra_ids)).exists()
        if extra_orders:
            config = extra_orders[0].config_id
            if config:
                extra_data = extra_orders.read_pos_data([], config)
                for key, values in extra_data.items():
                    if key in result:
                        result[key].extend(values)
                    else:
                        result[key] = values
                _logger.info(
                    "pos_return: sync_from_ui included %d marked predecessor orders: %s",
                    len(extra_ids), list(extra_ids)
                )

        return result

    # =====================================================================
    # Override _get_refunded_orders: allow multi-origin refund lines
    # =====================================================================
    @api.model
    def _get_refunded_orders(self, order):
        """Allow refund lines that reference multiple original orders.

        Odoo's native implementation raises ValidationError when
        refunded_orderline_ids point to lines from more than one order.
        In our succession pattern this is expected: a successor ticket may
        have inherited lines (pointing to the original) AND its own lines
        (pointing to the intermediate exchange).  Instead of blocking we
        return only the first order so the native ``len() > 1`` check passes.
        """
        refunded_orderline_ids = [
            line[2]['refunded_orderline_id']
            for line in order.get('lines', [])
            if line[0] in [0, 1] and line[2].get('refunded_orderline_id')
        ]
        if not refunded_orderline_ids:
            return self.env['pos.order']
        orders = self.env['pos.order.line'].browse(refunded_orderline_ids).mapped('order_id')
        if len(orders) > 1:
            # Multiple origins detected (successor pattern) — pick the first
            # to satisfy the native ``len()==1`` constraint.
            _logger.info(
                "pos_return: multi-origin refund detected (%d orders). "
                "Allowing succession pattern.",
                len(orders)
            )
            return orders[:1]
        return orders

    def _validate_refund_quantities(self):
        """Validate that refund quantities don't exceed actual remaining stock.
        
        Prevents race conditions when two return orders target the same ticket.
        For each refund line, checks the original orderline's total refunded qty
        (from OTHER completed orders, excluding the current one being processed)
        and verifies we're not over-refunding.
        
        Raises UserError with details of which products exceed available qty.
        """
        over_refunded = []

        for line in self.lines:
            if not line.refunded_orderline_id or line.qty >= 0:
                continue

            orig_line = line.refunded_orderline_id
            requested_refund = abs(line.qty)

            # Calculate total already refunded by OTHER completed orders
            # (exclude self because we haven't been fully processed yet)
            already_refunded = sum(
                abs(rl.qty)
                for rl in orig_line.refund_orderline_ids
                if rl.order_id.id != self.id
                and rl.order_id.state not in ('cancel', 'draft')
            )

            available = max(0, orig_line.qty - already_refunded)

            if requested_refund > available:
                product_name = re.sub(
                    r'^\[.*?\]\s*', '',
                    orig_line.product_id.display_name
                )
                over_refunded.append(
                    _("• %(product)s: solicitado %(requested)d, disponible %(available)d",
                      product=product_name,
                      requested=int(requested_refund),
                      available=int(available))
                )

        if over_refunded:
            details = "\n".join(over_refunded)
            raise UserError(_(
                "No se puede procesar esta devolución porque algunos productos "
                "ya fueron devueltos en otra orden:\n\n%(details)s\n\n"
                "Por favor, cancele esta orden y cree una nueva devolución "
                "con las cantidades correctas.",
                details=details
            ))

    @api.depends('lines.refunded_orderline_id')
    def _compute_refund_order_ids(self):
        """Find all refund orders linked to this order via refunded_orderline_id."""
        for order in self:
            refund_orders = self.env['pos.order'].search([
                ('lines.refunded_orderline_id.order_id', '=', order.id),
            ])
            order.refund_order_ids = refund_orders
            order.refund_order_count = len(refund_orders)

    @api.depends('lines.refund_orderline_ids', 'lines.qty',
                 'custom_return_done', 'custom_exchange_done')
    def _compute_original_remaining_summary(self):
        """Compute a summary of remaining returnable products.
        
        For original tickets: shows which products have been returned and
        which still remain. For refund tickets: shows the status of the
        source ticket they refunded.
        """
        for order in self:
            # Determine the target order to analyze
            # If this order is a refund, analyze its source (refunded_order_id)
            target = order
            is_refund_view = False
            if order.refunded_order_id:
                target = order.refunded_order_id
                is_refund_view = True

            # Only analyze orders that have positive lines with refunds
            positive_lines = target.lines.filtered(lambda l: l.qty > 0)
            if not positive_lines or not any(l.refund_orderline_ids for l in positive_lines):
                order.original_remaining_summary = False
                continue

            remaining_items = []
            total_original = 0
            total_remaining = 0

            for line in positive_lines:
                total_refunded = sum(
                    abs(rl.qty) for rl in line.refund_orderline_ids
                    if rl.order_id.state not in ('cancel', 'draft')
                )
                remaining = max(0, line.qty - total_refunded)
                total_original += line.qty
                total_remaining += remaining

                if remaining > 0:
                    product_name = re.sub(
                        r'^\[.*?\]\s*', '',
                        line.product_id.display_name
                    )
                    remaining_items.append((product_name, int(remaining)))

            if total_remaining == 0:
                # All products returned - green indicator
                html = (
                    '<div style="padding:6px 10px;background:#d4edda;'
                    'border:1px solid #c3e6cb;border-radius:4px;'
                    'color:#155724;font-size:13px;">'
                    '<i class="fa fa-check-circle" style="margin-right:5px;"></i>'
                    '<strong>Todos los productos devueltos</strong>'
                    '</div>'
                )
            else:
                # Products remain - amber indicator with details
                ref_label = ''
                if is_refund_view:
                    ref_label = (
                        f'<div style="margin-bottom:4px;color:#856404;font-size:12px;">'
                        f'Ticket origen: {target.pos_reference or target.name}'
                        f'</div>'
                    )
                items_html = ''.join(
                    f'<div style="padding:2px 0;font-size:12px;">'
                    f'• {name} — <strong>{qty}</strong></div>'
                    for name, qty in remaining_items
                )
                html = (
                    f'<div style="padding:6px 10px;background:#fff3cd;'
                    f'border:1px solid #ffc107;border-radius:4px;color:#856404;">'
                    f'{ref_label}'
                    f'<div style="font-size:13px;margin-bottom:4px;">'
                    f'<i class="fa fa-exclamation-triangle" style="margin-right:5px;"></i>'
                    f'<strong>Quedan {int(total_remaining)} de {int(total_original)} '
                    f'productos por devolver</strong></div>'
                    f'{items_html}'
                    f'</div>'
                )

            order.original_remaining_summary = html

    @api.depends('lines.refunded_orderline_id', 'lines.qty', 'internal_note')
    def _compute_exchange_origin(self):
        """Identify the original order for native exchange orders.
        
        A native exchange order has both positive (new) and negative (returned) lines.
        The negative lines have refunded_orderline_id pointing to the original order.
        
        Falls back to the internal_note Ref: field when refunded_orderline_id
        points to a grandparent order (common in multi-step exchange chains
        where inherited lines trace back to ancestors).
        """
        for order in self:
            has_positive = any(l.qty > 0 for l in order.lines)
            has_negative = any(l.qty < 0 for l in order.lines)
            
            if has_positive and has_negative:
                # Primary: try internal_note Ref: field (most accurate source)
                origin = False
                try:
                    note_tags = json.loads(order.internal_note) if order.internal_note else []
                    for tag in note_tags:
                        text = tag.get('text', '') if isinstance(tag, dict) else ''
                        ref_match = re.search(r'Ref:\s*(\S+)', text)
                        if ref_match:
                            origin = self.env['pos.order'].search([
                                ('pos_reference', '=', ref_match.group(1)),
                            ], limit=1)
                            break
                except (json.JSONDecodeError, TypeError):
                    pass

                # Fallback: find from refund links on negative lines
                if not origin:
                    origin_orders = self.env['pos.order']
                    for line in order.lines:
                        if line.qty < 0 and line.refunded_orderline_id:
                            origin_orders |= line.refunded_orderline_id.order_id
                    if origin_orders:
                        origin = origin_orders[0]

                if origin:
                    order.exchange_origin_order_id = origin
                    order.exchange_origin_ref = origin.pos_reference or origin.name
                else:
                    order.exchange_origin_order_id = False
                    order.exchange_origin_ref = False
            else:
                order.exchange_origin_order_id = False
                order.exchange_origin_ref = False

    @api.depends('pos_reference', 'name', 'picking_ids')
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

            # The order's OWN native pickings (sale delivery or refund receipt).
            # These must be EXCLUDED from exchange sections.
            own_pickings = order.picking_ids

            # ── Return receipts ──
            # For the ORIGINAL order: find pickings from its refund orders.
            # POS refunds only create receipt pickings (goods returning),
            # but the picking type code may not be 'incoming' (POS uses custom types).
            refund_orders = self.env['pos.order'].search([
                ('lines.refunded_orderline_id.order_id', '=', order.id),
            ])
            refund_pickings = self.env['stock.picking']
            for refund in refund_orders:
                refund_pickings |= refund.picking_ids

            # Own pickings when THIS order is a refund (has negative lines)
            is_refund = order.amount_total < 0
            own_return_pickings = own_pickings if is_refund else self.env['stock.picking']

            # Legacy: incoming pickings found by origin reference
            legacy_incoming = self.env['stock.picking'].search([
                ('origin', 'in', refs),
                ('picking_type_id.code', '=', 'incoming'),
                ('id', 'not in', own_pickings.ids),  # exclude own
            ])

            all_return_pickings = refund_pickings | own_return_pickings | legacy_incoming

            # ── Exchange pickings ──
            # Legacy exchange deliveries use INT: prefix in origin
            int_refs = ['INT:' + r for r in refs]
            exchange_out = self.env['stock.picking'].search([
                ('origin', 'in', int_refs),
                ('picking_type_id.code', '=', 'outgoing'),
            ])

            # Legacy exchange receipts: incoming with 'intercambio' in note
            exchange_in = self.env['stock.picking']
            return_only = all_return_pickings

            if exchange_out:
                exchange_in_list = []
                return_only_list = []
                for p in all_return_pickings:
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
                 'custom_exchange_replaced', 'amount_total',
                 'lines.refund_orderline_ids')
    def _compute_return_exchange_status(self):
        for order in self:
            if order.custom_exchange_replaced:
                order.return_exchange_status = 'exchange_replaced'
            elif order.custom_exchange_done:
                order.return_exchange_status = 'exchanged'
            elif order.custom_return_done:
                order.return_exchange_status = 'returned'
            elif order.amount_total < 0:
                # This order IS a refund (negative total)
                order.return_exchange_status = 'refund'
            else:
                # Check if this is a native exchange order (has both + and - lines)
                has_positive = any(l.qty > 0 for l in order.lines)
                has_negative = any(l.qty < 0 for l in order.lines)
                if has_positive and has_negative:
                    order.return_exchange_status = 'exchange_order'
                else:
                    # Check for partial returns via refund_orderline_ids
                    has_any_refund = False
                    for line in order.lines:
                        if line.refund_orderline_ids:
                            refunded_qty = sum(
                                abs(rl.qty) for rl in line.refund_orderline_ids
                                if rl.order_id.state not in ('cancel', 'draft')
                            )
                            if refunded_qty > 0:
                                has_any_refund = True
                                break
                    if has_any_refund:
                        order.return_exchange_status = 'partial'
                    else:
                        order.return_exchange_status = 'none'

    @api.depends('internal_note', 'custom_return_done', 'custom_exchange_done')
    def _compute_return_type_info(self):
        """Extract return/exchange type and reason from the internal_note JSON tag.
        Also shows 'Devuelto'/'Intercambiado' for original orders.
        
        The internal_note is set by the frontend when creating the order:
        - Sin Ticket: "Devolución - Devolución Sin Ticket | Razón: <reason>"
        - Arus: "Devolución - Devolución Arus | Ref: <ticket>"
        - Odoo: "Devolución - Devolución Odoo | Ref: <ticket>"
        - Exchange: "Intercambio - <type> | Ref: <ref> | Diff: <amount>"
        """
        for order in self:
            order.custom_return_type_display = ''
            order.custom_return_reason = ''

            # First: check if this is an original order that was returned/exchanged
            if order.custom_exchange_done:
                order.custom_return_type_display = 'Intercambiado'
                continue
            elif order.custom_return_done:
                order.custom_return_type_display = 'Devuelto'
                continue

            # Check for partial return (has refunds but not fully returned)
            has_partial = False
            if not order.custom_return_done and not order.custom_exchange_done:
                for line in order.lines:
                    if line.refund_orderline_ids:
                        refunded_qty = sum(
                            abs(rl.qty) for rl in line.refund_orderline_ids
                            if rl.order_id.state not in ('cancel', 'draft')
                        )
                        if refunded_qty > 0:
                            has_partial = True
                            break
            if has_partial:
                order.custom_return_type_display = 'Devuelto Parcial'
                continue

            # Second: parse internal_note for refund/exchange type
            note_text = ''
            if order.internal_note:
                try:
                    tags = json.loads(order.internal_note)
                    if tags and isinstance(tags, list):
                        note_text = tags[0].get('text', '')
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            if not note_text:
                continue

            # Detect operation type
            if 'Sin Ticket' in note_text:
                order.custom_return_type_display = 'Dev. Sin Ticket'
            elif 'Arus' in note_text:
                order.custom_return_type_display = 'Dev. Arus'
            elif 'Devolución' in note_text and 'Odoo' in note_text:
                order.custom_return_type_display = 'Dev. Odoo'
            elif 'Intercambio' in note_text:
                order.custom_return_type_display = 'Intercambio'

            # Extract reason or reference
            if 'Razón:' in note_text:
                order.custom_return_reason = note_text.split('Razón:')[-1].strip()
            elif 'Ref:' in note_text:
                ref_part = note_text.split('Ref:')[-1].strip()
                # Remove anything after | (e.g. Diff info in exchanges)
                if '|' in ref_part:
                    ref_part = ref_part.split('|')[0].strip()
                order.custom_return_reason = ref_part

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

    # =====================================================================
    # CFDI constraint override for l10n_mx_edi_pos compatibility
    # =====================================================================
    @api.constrains('amount_total')
    def _l10n_mx_edi_constrains_amount_total(self):
        """Override the l10n_mx_edi_pos constraint to allow pos_return patterns.

        The original constraint rejects:
        1. Refund orders (refunded_order_id) with positive-subtotal lines
           → This blocks exchanges (mixed positive/negative lines)
        2. Non-refund orders with negative totals
           → This blocks no-ticket returns (no refunded_orderline_id)

        Our override:
        - Allows refund orders (is_refund=True) with negative totals even
          without refunded_order_id (no-ticket returns).
        - Allows orders with mixed lines when they are exchange-related.
        - Falls through to the original constraint for all other cases.
        """
        # Only proceed if l10n_mx_edi_pos module is installed
        if not hasattr(self, 'l10n_mx_edi_is_cfdi_needed'):
            return

        for order in self:
            # Skip if CFDI is not needed for this order
            if not order.l10n_mx_edi_is_cfdi_needed:
                continue

            order_lines = order.lines
            if hasattr(order_lines, '_l10n_mx_edi_cfdi_lines'):
                order_lines = order_lines._l10n_mx_edi_cfdi_lines()

            if not order_lines:
                continue

            # CASE 1: Refund order with positive lines (exchange pattern)
            # If the order has refunded_order_id and positive lines, allow
            # it if this is an exchange (has both positive and negative lines)
            if order.refunded_order_id:
                has_positive = any(
                    line.price_subtotal > 0.0
                    for line in order_lines.filtered(
                        lambda l: not l.refunded_orderline_id and (
                            'coupon_id' not in l._fields or not l.coupon_id
                        )
                    )
                )
                has_negative = any(
                    line.price_subtotal < 0.0 for line in order_lines
                )
                if has_positive and has_negative:
                    # Exchange pattern: mixed lines → allow
                    continue
                elif has_positive:
                    # Pure positive lines on a refund → original error
                    raise ValidationError(
                        "The amount of the order must be positive for a sale "
                        "and negative for a refund."
                    )

            # CASE 2: No refunded_order_id and negative total
            # Allow if this is a POS refund (is_refund flag, typical of
            # no-ticket returns from our module)
            if (
                not order.refunded_order_id
                and order.currency_id.round(order.amount_total) < 0.0
            ):
                if order.is_refund:
                    # No-ticket return from pos_return → allow
                    continue
                else:
                    # Non-refund order with negative total → original error
                    raise ValidationError(
                        "The amount of the order must be positive for a sale "
                        "and negative for a refund."
                    )

    # =====================================================================
    # Action methods for stat buttons
    # =====================================================================
    def action_view_refund_orders(self):
        """Open the refund orders linked to this order."""
        self.ensure_one()
        orders = self.refund_order_ids
        action = {
            'type': 'ir.actions.act_window',
            'name': 'Órdenes de Reembolso',
            'res_model': 'pos.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)],
            'context': {'create': False},
        }
        if len(orders) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = orders.id
        return action


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

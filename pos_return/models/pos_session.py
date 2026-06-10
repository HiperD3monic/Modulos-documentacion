# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
import json
import logging
import re

_logger = logging.getLogger(__name__)


class PosSession(models.Model):
    """
    Extensión de pos.session para manejar devoluciones e intercambios
    de productos en el Punto de Venta (POS).

    ARQUITECTURA v2 (Nativa):
    Las devoluciones e intercambios se procesan como órdenes POS nativas.
    - Devolución: orden con líneas negativas → picking de entrada automático
    - Intercambio: orden con líneas mixtas → pickings automáticos
    - Pagos: procesados por PaymentScreen nativo
    - Contabilidad: manejada automáticamente al cerrar sesión

    Los métodos create_return() y create_exchange() ahora solo VALIDAN
    los datos y devuelven payloads para que el frontend cree las órdenes.
    """
    _inherit = 'pos.session'

    def find_product_by_barcode(self, barcode, config_id):
        """
        Busca un producto por código de barras o referencia interna.

        Este método es llamado desde el frontend cuando el producto
        no se encuentra en el cache local del POS.

        :param barcode: Código de barras o referencia a buscar
        :param config_id: ID de la configuración del POS
        :return: dict con los productos encontrados en formato compatible con POS
        """
        Product = self.env['product.product']

        # Buscar primero por barcode exacto
        product = Product.search([
            ('barcode', '=', barcode),
            ('available_in_pos', '=', True)
        ], limit=1)

        # Si no encuentra, buscar por default_code (referencia interna)
        if not product:
            product = Product.search([
                ('default_code', '=', barcode),
                ('available_in_pos', '=', True)
            ], limit=1)

        if product:
            # Retornar en formato compatible con el sistema de datos del POS
            return {
                'product.product': [{
                    'id': product.id,
                    'display_name': product.display_name,
                    'lst_price': product.lst_price,
                    'barcode': product.barcode,
                    'default_code': product.default_code,
                }]
            }

        return {'product.product': []}

    # =====================================================================
    # DEVOLUCIÓN (Return) — Validación y Preparación
    # =====================================================================

    def create_return(self, ticket, products_data, return_type='odoo', partner_id=False):
        """
        Valida una devolución y devuelve los datos necesarios para que el
        frontend cree una orden POS nativa con líneas negativas.

        El frontend creará la orden, navegará al PaymentScreen, y al pagar:
        - Odoo crea automáticamente el picking de entrada (líneas negativas)
        - Odoo registra el pago correctamente
        - Odoo maneja la contabilidad al cerrar sesión

        :param ticket: Número de ticket o razón (si es sin ticket)
        :param products_data: Lista de productos [{product_id, quantity, price_unit}]
        :param return_type: 'odoo', 'arus', 'no_ticket'
        :param partner_id: ID del cliente (opcional)
        :return: dict con datos para crear la orden en el frontend
        """
        self.ensure_one()
        _logger.info("POS Return: Validating return type %s for %s", return_type, ticket)

        try:
            # Validaciones de entrada
            if not ticket:
                if return_type == 'no_ticket':
                    raise UserError(str(_("Debe ingresar una razón para la devolución.")))
                raise UserError(str(_("El número de ticket es obligatorio.")))

            if not products_data:
                raise UserError(str(_("Debe seleccionar al menos un producto para la devolución.")))

            # Calcular total de la devolución
            total_amount = sum(
                product['quantity'] * product['price_unit']
                for product in products_data
            )

            if total_amount <= 0:
                raise UserError(str(_("El monto total de la devolución debe ser mayor a cero.")))

            # Determinar Referencia de Origen
            if return_type == 'no_ticket':
                origin_ref = "SIN_TICKET"
                reason = ticket  # The 'ticket' field contains the reason text
                base_type = str(_("Devolución Sin Ticket"))
            elif return_type == 'arus':
                origin_ref = ticket
                reason = ''
                base_type = str(_("Devolución Arus"))
            else:  # odoo
                origin_ref = ticket
                reason = ''
                base_type = str(_("Devolución Odoo"))

            # === Buscar orden original (para Odoo tickets) ===
            original_order_id = False
            original_order_lines = []
            if return_type == 'odoo' and ticket:
                original_order = self.env['pos.order'].search([
                    ('pos_reference', '=', ticket),
                ], limit=1)
                if original_order:
                    original_order_id = original_order.id
                    # Preparar mapeo de líneas originales para refunded_orderline_id
                    for line in original_order.lines:
                        original_order_lines.append({
                            'id': line.id,
                            'product_id': line.product_id.id,
                            'qty': line.qty,
                            'price_unit': line.price_unit,
                            'discount': line.discount,
                            'tax_ids': line.tax_ids.ids,
                        })

            # NOTE: We no longer mark the original order as returned here.
            # The marking happens in _process_saved_order() ONLY when the
            # refund order is actually paid and synced. This prevents
            # marking the original as "returned" if the user cancels
            # the refund before paying.

            _logger.info("POS Return: Validation OK. Total: %s", total_amount)

            return {
                'success': True,
                'total_amount': total_amount,
                'origin_ref': origin_ref,
                'base_type': base_type,
                'reason': reason,
                'original_order_id': original_order_id,
                'original_order_lines': original_order_lines,
                'message': str(_("Devolución validada. Procese el pago.")),
            }
        except Exception as e:
            _logger.exception("POS Return: Error validating return")
            return {
                'success': False,
                'error': str(e)
            }

    # =====================================================================
    # INTERCAMBIO (Exchange) — Validación y Preparación
    # =====================================================================

    def create_exchange(self, ticket, returned_products, new_products, exchange_type='odoo', partner_id=False):
        """
        Valida un intercambio y devuelve los datos necesarios para que el
        frontend cree una orden POS nativa con líneas mixtas.

        El frontend creará la orden con:
        - Líneas positivas para productos nuevos (salida de inventario)
        - Líneas negativas para productos devueltos (entrada de inventario)

        Al pagar en PaymentScreen:
        - Odoo crea pickings automáticamente (IN para negativas, OUT para positivas)
        - El pago se registra correctamente
        - La contabilidad se maneja al cerrar sesión

        :param ticket: Número de ticket o razón
        :param returned_products: [{product_id, quantity, price_unit}]
        :param new_products: [{product_id, quantity, price_unit}]
        :param exchange_type: 'odoo', 'arus', 'no_ticket'
        :param partner_id: ID del cliente (opcional)
        :return: dict con datos para crear la orden en el frontend
        """
        self.ensure_one()
        _logger.info("POS Exchange: Validating exchange type %s for %s", exchange_type, ticket)

        try:
            # === Validaciones de entrada ===
            if not ticket:
                if exchange_type == 'no_ticket':
                    raise UserError(str(_("Debe ingresar una razón para el intercambio.")))
                raise UserError(str(_("El número de ticket es obligatorio.")))

            if not returned_products:
                raise UserError(str(_("Debe seleccionar al menos un producto a devolver.")))

            if not new_products:
                raise UserError(str(_("Debe seleccionar al menos un producto nuevo para el intercambio.")))

            # === Calcular totales ===
            return_total = sum(
                p['quantity'] * p['price_unit'] for p in returned_products
            )
            new_total = sum(
                p['quantity'] * p['price_unit'] for p in new_products
            )

            if return_total <= 0:
                raise UserError(str(_("El valor de los productos a devolver debe ser mayor a cero.")))
            if new_total <= 0:
                raise UserError(str(_("El valor de los productos nuevos debe ser mayor a cero.")))

            # === Determinar referencia y etiquetas ===
            if exchange_type == 'no_ticket':
                origin_ref = "INTERCAMBIO_SIN_TICKET"
                base_type = str(_("Intercambio"))
            elif exchange_type == 'arus':
                origin_ref = ticket
                base_type = str(_("Intercambio Arus"))
            else:  # odoo
                origin_ref = ticket
                base_type = str(_("Intercambio Odoo"))

            difference = new_total - return_total

            # === Buscar orden original y preparar datos de líneas ===
            original_order_id = False
            original_order_lines = []
            if exchange_type == 'odoo' and ticket:
                original_order = self.env['pos.order'].search([
                    ('pos_reference', '=', ticket),
                ], limit=1)
                if original_order:
                    original_order_id = original_order.id
                    for line in original_order.lines:
                        original_order_lines.append({
                            'id': line.id,
                            'product_id': line.product_id.id,
                            'qty': line.qty,
                            'price_unit': line.price_unit,
                            'discount': line.discount,
                            'tax_ids': line.tax_ids.ids,
                        })

            # NOTE: We no longer mark the original order as exchanged here.
            # The marking happens in _process_saved_order() ONLY when the
            # exchange order is actually paid and synced.

            # Determine cash message for frontend display
            if difference > 0:
                cash_message = str(_("Cliente paga diferencia"))
            elif difference < 0:
                cash_message = str(_("Devolución al cliente"))
            else:
                cash_message = str(_("Sin movimiento de caja"))

            _logger.info(
                "POS Exchange: Validation OK. Return: %s, New: %s, Diff: %s",
                return_total, new_total, difference
            )

            return {
                'success': True,
                'return_total': return_total,
                'new_total': new_total,
                'difference': difference,
                'origin_ref': origin_ref,
                'base_type': base_type,
                'cash_message': cash_message,
                'original_order_id': original_order_id,
                'original_order_lines': original_order_lines,
                'message': str(_("Intercambio validado. Procese el pago.")),
            }
        except Exception as e:
            _logger.exception("POS Exchange: Error validating exchange")
            return {
                'success': False,
                'error': str(e),
            }

    # =====================================================================
    # TICKET Search Methods (preserved from v1)
    # =====================================================================

    def get_partner_tickets(self, partner_id):
        """
        Busca los últimos pedidos (tickets) de un cliente específico.
        Limitado a los 20 más recientes para evitar sobrecarga.

        Excluye:
        - Reembolsos nativos (is_refund = True)
        - Órdenes ya devueltas por pos_return (custom_return_done = True)
        - Órdenes ya intercambiadas por pos_return (custom_exchange_replaced = True)

        Retorna por cada ticket:
        - lines: solo líneas con remaining_qty > 0 (para selección de devolución)
        - all_lines: TODAS las líneas incluyendo devueltas (para visualización completa)
        - return_status: 'none', 'partial', 'full'
        - total_original_qty: suma de cantidades originales
        - total_remaining_qty: suma de cantidades disponibles para devolver
        - exchanges: lista de intercambios asociados al ticket
        """
        domain = [
            ('partner_id', '=', partner_id),
            ('state', 'in', ['paid', 'done', 'invoiced']),
            ('is_refund', '=', False),
            ('custom_return_done', '=', False),
            ('custom_exchange_done', '=', False),
            ('custom_exchange_replaced', '=', False),
        ]
        orders = self.env['pos.order'].search(domain, order='date_order desc', limit=20)

        # Exclude orders with no remaining products to return/exchange
        orders = orders.filtered(self._order_has_remaining_products)

        return self._process_tickets(orders)

    def search_recent_tickets(self):
        """
        Devuelve las últimas órdenes elegibles para devolución.
        Se usa cuando el usuario hace click en la barra de búsqueda de tickets
        sin haber escrito nada, para mostrar un listado navegable.

        Ordenado por fecha desc (más recientes primero), limitado a 20 resultados.

        :return: Lista de tickets en el mismo formato que get_partner_tickets
        """
        domain = [
            ('state', 'in', ['paid', 'done', 'invoiced']),
            ('is_refund', '=', False),
            ('custom_return_done', '=', False),
            ('custom_exchange_done', '=', False),
            ('custom_exchange_replaced', '=', False),
        ]
        orders = self.env['pos.order'].search(domain, order='date_order desc', limit=20)

        # Exclude orders with no remaining products to return/exchange
        orders = orders.filtered(self._order_has_remaining_products)

        if not orders:
            return []

        return self._process_tickets(orders, include_partner=True)

    def search_ticket_by_ref(self, query):
        """
        Busca tickets por número de referencia (pos_reference o name).
        NO requiere un cliente seleccionado — busca en TODOS los tickets.

        Soporta búsqueda parcial (ilike) para autocompletado.
        Limitado a 10 resultados para rendimiento.

        :param query: Texto de búsqueda (ej: "0006", "POS/001")
        :return: Lista de tickets en el mismo formato que get_partner_tickets
        """
        if not query or len(query) < 2:
            return []

        domain = [
            ('state', 'in', ['paid', 'done', 'invoiced']),
            ('is_refund', '=', False),
            ('custom_return_done', '=', False),
            ('custom_exchange_done', '=', False),
            ('custom_exchange_replaced', '=', False),
            '|',
            ('pos_reference', 'ilike', query),
            ('name', 'ilike', query),
        ]
        orders = self.env['pos.order'].search(domain, order='date_order desc', limit=10)

        # Exclude orders with no remaining products to return/exchange
        orders = orders.filtered(self._order_has_remaining_products)

        if not orders:
            return []

        return self._process_tickets(orders, include_partner=True)

    def _order_has_remaining_products(self, order):
        """Check if an order has any remaining products available for return/exchange.
        
        Handles:
        1. Regular orders: check all lines with remaining qty
        2. Exchange orders (+ and - lines): only check positive lines
        3. Partial refund successors (only - lines, is_refund=False): check inherited
        4. Original orders with child exchange orders: check exchange positive lines
        """
        # Detect order type
        is_exchange_order = (
            any(l.qty > 0 for l in order.lines)
            and any(l.qty < 0 for l in order.lines)
        )
        is_refund_successor = (
            not is_exchange_order
            and any(l.qty < 0 for l in order.lines)
            and any(l.refunded_orderline_id for l in order.lines)
            and not order.is_refund
        )
        is_successor_order = is_exchange_order or is_refund_successor

        # Check order's own lines (skip negatives for successor orders)
        for line in order.lines:
            if is_successor_order and line.qty < 0:
                continue

            refunded_qty = sum(
                abs(rl.qty) for rl in line.refund_orderline_ids
                if rl.order_id.state not in ('cancel', 'draft')
            )
            if line.qty > refunded_qty:
                return True

        # For successor orders: check inherited lines from parent orders
        if is_successor_order:
            inherited = self._build_inherited_lines_from_original(order)
            if inherited:
                return True

        # For non-successor orders: also check child native exchange orders
        if not is_successor_order:
            exchange_orders = self.env['pos.order'].search([
                ('lines.refunded_orderline_id.order_id', '=', order.id),
                ('state', 'in', ['paid', 'done', 'invoiced']),
            ])
            for ex_order in exchange_orders:
                has_positive = any(l.qty > 0 for l in ex_order.lines)
                has_negative = any(l.qty < 0 for l in ex_order.lines)
                if has_positive and has_negative:
                    for line in ex_order.lines:
                        if line.qty > 0:
                            refunded_qty = sum(
                                abs(rl.qty) for rl in line.refund_orderline_ids
                                if rl.order_id.state not in ('cancel', 'draft')
                            )
                            if line.qty > refunded_qty:
                                return True

        return False

    def _process_tickets(self, orders, include_partner=False):
        """
        Procesa una lista de órdenes POS y devuelve datos estructurados
        para el frontend de devoluciones/intercambios.

        :param orders: recordset de pos.order
        :param include_partner: si True, incluye datos del partner en cada resultado
        :return: lista de dicts con datos de tickets
        """
        results = []
        for order in orders:
            origin_refs = [order.pos_reference, order.name]

            # Calcular cantidad ya devuelta por producto
            # Usar refunds nativos de Odoo (refund_orderline_ids)
            returned_qty_by_product = {}
            for line in order.lines:
                pid = line.product_id.id
                # Sumar cantidades de refund nativas
                refunded_qty = sum(
                    abs(rl.qty) for rl in line.refund_orderline_ids
                    if rl.order_id.state not in ('cancel', 'draft')
                )
                if refunded_qty > 0:
                    returned_qty_by_product[pid] = returned_qty_by_product.get(pid, 0) + refunded_qty

            # También buscar pickings legacy (de la v1 del módulo)
            return_pickings = self.env['stock.picking'].search([
                ('origin', 'in', origin_refs),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'incoming')
            ])
            for picking in return_pickings:
                for move in picking.move_ids:
                    pid = move.product_id.id
                    returned_qty_by_product[pid] = returned_qty_by_product.get(pid, 0) + move.quantity

            # Buscar entregas de intercambio (legacy pickings)
            exchange_out_origins = ['INT:' + ref for ref in origin_refs]
            exchange_pickings = self.env['stock.picking'].search([
                ('origin', 'in', exchange_out_origins),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'outgoing')
            ])

            # Backward compatibility: buscar sin prefijo
            if not exchange_pickings:
                pos_picking_type = self.config_id.picking_type_id
                legacy_pickings = self.env['stock.picking'].search([
                    ('origin', 'in', origin_refs),
                    ('state', '=', 'done'),
                    ('picking_type_id.code', '=', 'outgoing'),
                    ('picking_type_id', '!=', pos_picking_type.id),
                ])
                exchange_pickings = legacy_pickings

            # Construir información de intercambios
            exchanges_data = self._build_exchanges_data(exchange_pickings)

            lines_data = []       # Solo líneas con remaining > 0 (para selección)
            all_lines_data = []   # TODAS las líneas (para visualización)
            total_original_qty = 0
            total_remaining_qty = 0

            # Detect order type for line processing
            is_exchange_order = (
                any(l.qty > 0 for l in order.lines)
                and any(l.qty < 0 for l in order.lines)
            )

            # Detect partial-refund successor orders (only negative lines, not is_refund)
            # These are partial return tickets that inherit remaining products from original
            is_refund_successor = (
                not is_exchange_order
                and any(l.qty < 0 for l in order.lines)
                and any(l.refunded_orderline_id for l in order.lines)
                and not order.is_refund  # is_refund=False means it's a partial (we set this)
            )

            # Unified: orders that inherit remaining products from their parent
            is_successor_order = is_exchange_order or is_refund_successor

            for line in order.lines:
                pid = line.product_id.id
                original_qty = line.qty

                # For successor orders: skip negative lines from returnable calculation
                if is_successor_order and original_qty < 0:
                    # Include in all_lines for visual reference only
                    prefix = '↩ ' if is_exchange_order else '↩ '
                    all_lines_data.append({
                        'id': line.id,
                        'product_id': line.product_id.id,
                        'name': prefix + line.product_id.display_name,
                        'qty': original_qty,
                        'remaining_qty': 0,
                        'price_unit': line.price_unit,
                        'price_subtotal_incl': line.price_subtotal_incl,
                        'discount': line.discount,
                        'tax_ids': line.tax_ids.ids,
                        'is_exchange_product': False,
                        'is_returned_line': True,
                    })
                    continue

                returned_so_far = returned_qty_by_product.get(pid, 0)

                remaining_qty = max(0, original_qty - returned_so_far)

                deducted = original_qty - remaining_qty
                returned_qty_by_product[pid] = max(0, returned_so_far - deducted)

                total_original_qty += original_qty
                total_remaining_qty += remaining_qty

                line_dict = {
                    'id': line.id,
                    'product_id': line.product_id.id,
                    'name': ('🔄 ' if is_exchange_order else '') + line.product_id.display_name,
                    'qty': original_qty,
                    'remaining_qty': remaining_qty,
                    'price_unit': line.price_unit,
                    'price_subtotal_incl': line.price_subtotal_incl,
                    'discount': line.discount,
                    'tax_ids': line.tax_ids.ids,
                    'is_exchange_product': is_exchange_order,
                }

                # all_lines siempre incluye la línea
                all_lines_data.append(line_dict)

                # lines solo incluye si hay remanente
                if remaining_qty > 0:
                    lines_data.append(line_dict)

            # === Agregar productos de intercambio LEGACY como líneas retornables ===
            exchange_lines = self._build_exchange_returnable_lines(
                exchange_pickings, returned_qty_by_product,
                total_original_qty, total_remaining_qty
            )
            for ex_line in exchange_lines['lines']:
                lines_data.append(ex_line)
            for ex_line in exchange_lines['all_lines']:
                all_lines_data.append(ex_line)
            total_original_qty = exchange_lines['total_original_qty']
            total_remaining_qty = exchange_lines['total_remaining_qty']

            # === Agregar productos de intercambio NATIVO (V2) como líneas retornables ===
            native_exchange_result = self._build_native_exchange_returnable_lines(
                order, returned_qty_by_product,
                total_original_qty, total_remaining_qty
            )
            for ex_line in native_exchange_result['lines']:
                lines_data.append(ex_line)
            for ex_line in native_exchange_result['all_lines']:
                all_lines_data.append(ex_line)
            total_original_qty = native_exchange_result['total_original_qty']
            total_remaining_qty = native_exchange_result['total_remaining_qty']

            # Merge native exchange history into exchanges_data
            native_exchanges = self._build_native_exchanges_data(order)
            exchanges_data.extend(native_exchanges)

            # === For successor orders: inherit remaining products from original ticket ===
            # Both exchange tickets and partial refund tickets inherit remaining
            # products from the original order that weren't part of the operation.
            if is_successor_order:
                inherited_lines = self._build_inherited_lines_from_original(order)
                for inh_line in inherited_lines:
                    lines_data.append(inh_line)
                    all_lines_data.append(inh_line)
                    total_original_qty += inh_line['remaining_qty']
                    total_remaining_qty += inh_line['remaining_qty']

            # Determinar estado de devolución del ticket
            if total_original_qty == 0:
                return_status = 'none'
            elif total_remaining_qty == 0:
                return_status = 'full'
            elif total_remaining_qty < total_original_qty:
                return_status = 'partial'
            else:
                return_status = 'none'

            result = {
                'id': order.id,
                'name': order.name,
                'pos_reference': order.pos_reference,
                'date_order': order.date_order,
                'amount_total': order.amount_total,
                # Total value of products still available for return
                'available_total': sum(
                    l['price_subtotal_incl']
                    for l in lines_data
                    if l.get('remaining_qty', 0) > 0
                ),
                'lines': lines_data,
                'all_lines': all_lines_data,
                'return_status': return_status,
                'total_original_qty': total_original_qty,
                'total_remaining_qty': total_remaining_qty,
                'has_exchange': len(exchanges_data) > 0,
                'exchanges': exchanges_data,
            }

            # Always include partner info (needed for client-side mismatch checks)
            result['partner_id'] = order.partner_id.id if order.partner_id else False
            result['partner_name'] = order.partner_id.name if order.partner_id else _('Sin cliente')

            results.append(result)

        # Exclude tickets with no remaining returnable products
        results = [r for r in results if r['total_remaining_qty'] > 0 or r['return_status'] == 'none']

        return results

    def _build_exchanges_data(self, exchange_pickings):
        """Build exchange history data from legacy exchange pickings."""
        exchanges_data = []
        for ep in exchange_pickings:
            new_products = []
            for move in ep.move_ids:
                new_products.append({
                    'product_name': move.product_id.display_name,
                    'quantity': move.quantity,
                })

            # Find the linked return picking (reception)
            returned_products_list = []
            picking_in_name = ''
            return_name = self._extract_note_metadata(ep.note, 'EXCHANGE_RETURN')
            if return_name:
                try:
                    return_picking = self.env['stock.picking'].search([('name', '=', return_name)], limit=1)
                    if return_picking:
                        picking_in_name = return_picking.name
                        for move in return_picking.move_ids:
                            returned_products_list.append({
                                'product_name': move.product_id.display_name,
                                'quantity': move.quantity,
                            })
                except (ValueError, IndexError):
                    pass

            exchanges_data.append({
                'picking_out_name': ep.name,
                'picking_in_name': picking_in_name,
                'date': str(ep.scheduled_date or ep.create_date),
                'new_products': new_products,
                'returned_products': returned_products_list,
            })
        return exchanges_data

    def _build_exchange_returnable_lines(self, exchange_pickings, returned_qty_by_product,
                                          total_original_qty, total_remaining_qty):
        """Build returnable lines from legacy exchange delivery pickings."""
        lines = []
        all_lines = []

        for ep in exchange_pickings:
            # Buscar si los productos de este intercambio ya fueron devueltos
            ep_return_pickings = self.env['stock.picking'].search([
                ('origin', 'in', [ep.name, 'INT:' + ep.name]),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'incoming')
            ])
            ep_returned_by_product = {}
            for rp in ep_return_pickings:
                for move in rp.move_ids:
                    pid = move.product_id.id
                    ep_returned_by_product[pid] = ep_returned_by_product.get(pid, 0) + move.quantity

            # Parse stored exchange prices from picking note
            exchange_prices = {}
            prices_str = self._extract_note_metadata(ep.note, 'EXCHANGE_PRICES')
            if prices_str:
                try:
                    exchange_prices = json.loads(prices_str)
                except (json.JSONDecodeError, ValueError):
                    pass

            for move in ep.move_ids:
                pid = move.product_id.id
                ex_qty = move.quantity

                ex_returned = ep_returned_by_product.get(pid, 0)
                general_returned = returned_qty_by_product.get(pid, 0)
                total_returned = ex_returned + general_returned
                ex_remaining = max(0, ex_qty - total_returned)

                consumed = ex_qty - ex_remaining
                ep_consumed = min(consumed, ex_returned)
                ep_returned_by_product[pid] = max(0, ex_returned - ep_consumed)
                general_consumed = consumed - ep_consumed
                returned_qty_by_product[pid] = max(0, general_returned - general_consumed)

                total_original_qty += ex_qty
                total_remaining_qty += ex_remaining

                # Use stored exchange price > pricelist price > lst_price
                # New format: {pid: {price, original_price, discount}}
                # Old format: {pid: effective_price}  (backward compat)
                stored_data = exchange_prices.get(str(pid))
                ex_discount = 0
                if stored_data is not None:
                    if isinstance(stored_data, dict):
                        # New format: preserve original price + discount
                        ex_price = stored_data.get('original_price', stored_data.get('price', 0))
                        ex_discount = stored_data.get('discount', 0)
                    else:
                        # Old format: just a number (effective price)
                        ex_price = stored_data
                else:
                    pricelist = self.config_id.pricelist_id
                    if pricelist:
                        ex_price = pricelist._get_product_price(move.product_id, 1.0)
                    else:
                        ex_price = move.product_id.lst_price

                ex_line_dict = {
                    'id': 'ex_%s_%s' % (ep.id, move.id),
                    'product_id': move.product_id.id,
                    'name': '🔄 ' + move.product_id.display_name,
                    'qty': ex_qty,
                    'remaining_qty': ex_remaining,
                    'price_unit': ex_price,
                    'discount': ex_discount,
                    'price_subtotal_incl': ex_price * (1 - ex_discount / 100) * ex_qty,
                    'is_exchange_product': True,
                    'exchange_picking': ep.name,
                }

                all_lines.append(ex_line_dict)
                if ex_remaining > 0:
                    lines.append(ex_line_dict)

        return {
            'lines': lines,
            'all_lines': all_lines,
            'total_original_qty': total_original_qty,
            'total_remaining_qty': total_remaining_qty,
        }

    # =====================================================================
    # Native Exchange (V2) Product Tracking
    # =====================================================================

    def _build_native_exchange_returnable_lines(self, order, returned_qty_by_product,
                                                 total_original_qty, total_remaining_qty):
        """Build returnable lines from native exchange orders (V2).
        
        Native exchange orders are identified by having refunded_orderline_id
        pointing to the original order AND having both positive (new) and
        negative (returned) lines.
        
        The positive lines represent products the customer RECEIVED during
        the exchange and can potentially re-exchange later.
        """
        lines = []
        all_lines = []

        # Find all orders that have refund links to this order
        exchange_orders = self.env['pos.order'].search([
            ('lines.refunded_orderline_id.order_id', '=', order.id),
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ])

        # Filter: only orders with BOTH positive and negative lines (exchanges)
        exchange_orders = exchange_orders.filtered(
            lambda o: any(l.qty > 0 for l in o.lines)
                  and any(l.qty < 0 for l in o.lines)
        )

        for ex_order in exchange_orders:
            for line in ex_order.lines:
                if line.qty <= 0:
                    # Negative lines are returned products — already tracked
                    continue

                pid = line.product_id.id
                ex_qty = line.qty

                # Calculate how much of this product was already refunded/re-exchanged
                refunded_qty = sum(
                    abs(rl.qty) for rl in line.refund_orderline_ids
                    if rl.order_id.state not in ('cancel', 'draft')
                )

                remaining = max(0, ex_qty - refunded_qty)

                total_original_qty += ex_qty
                total_remaining_qty += remaining

                ex_line_dict = {
                    'id': line.id,
                    'product_id': pid,
                    'name': '🔄 ' + line.product_id.display_name,
                    'qty': ex_qty,
                    'remaining_qty': remaining,
                    'price_unit': line.price_unit,
                    'price_subtotal_incl': line.price_subtotal_incl,
                    'discount': line.discount,
                    'tax_ids': line.tax_ids.ids,
                    'is_exchange_product': True,
                    'exchange_order_ref': ex_order.pos_reference or ex_order.name,
                }

                all_lines.append(ex_line_dict)
                if remaining > 0:
                    lines.append(ex_line_dict)

        return {
            'lines': lines,
            'all_lines': all_lines,
            'total_original_qty': total_original_qty,
            'total_remaining_qty': total_remaining_qty,
        }

    def _build_native_exchanges_data(self, order):
        """Build exchange history data from native exchange orders (V2).
        
        Used for the exchange audit tab display. Creates structured data
        about each exchange operation.
        """
        exchanges_data = []

        # Find all orders that have refund links to this order
        exchange_orders = self.env['pos.order'].search([
            ('lines.refunded_orderline_id.order_id', '=', order.id),
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ])

        # Filter: only orders with BOTH positive and negative lines (exchanges)
        exchange_orders = exchange_orders.filtered(
            lambda o: any(l.qty > 0 for l in o.lines)
                  and any(l.qty < 0 for l in o.lines)
        )

        for ex_order in exchange_orders:
            new_products = []
            returned_products = []

            for line in ex_order.lines:
                product_data = {
                    'product_name': line.product_id.display_name,
                    'quantity': abs(line.qty),
                }
                if line.qty > 0:
                    new_products.append(product_data)
                else:
                    returned_products.append(product_data)

            exchanges_data.append({
                'picking_out_name': ex_order.pos_reference or ex_order.name,
                'picking_in_name': '',  # Native: no separate picking
                'date': str(ex_order.date_order),
                'new_products': new_products,
                'returned_products': returned_products,
                'is_native': True,
            })

        return exchanges_data

    def _build_inherited_lines_from_original(self, exchange_order):
        """Build inherited lines from the original ticket(s) for an exchange order.
        
        When an exchange ticket is created, the original is marked as "Intercambiado"
        and becomes unsearchable. However, the original may have had products that
        were NOT part of the exchange (never returned/exchanged). These products
        must be "inherited" by the exchange ticket so they remain accessible for
        future operations.
        
        This method follows the chain of exchanges recursively to collect ALL
        remaining products from ancestor orders.
        
        Example:
            Original 90011: SABRITAS(2), FRITURAS(3), MAZAPAN(4), PALETA(3), CHURROS(6)
            Return 90018: SABRITAS(2), FRITURAS(1), PALETA(1), CHURROS(2) returned
            Exchange 90019: FRITURAS(2)+CHURROS(4) → PAPA(2)+PISTACHES(3)
            
            90019 should inherit: MAZAPAN(4), PALETA(2) from original 90011
        """
        inherited_lines = []
        visited_order_ids = {exchange_order.id}  # Avoid infinite loops

        # Collect original order IDs from the exchange's refund links
        orders_to_check = set()
        for line in exchange_order.lines:
            if line.refunded_orderline_id:
                orig_id = line.refunded_orderline_id.order_id.id
                if orig_id not in visited_order_ids:
                    orders_to_check.add(orig_id)

        # Also include the source ticket from internal_note Ref: field.
        # This is critical for returns from exchange tickets: inherited lines'
        # refunded_orderline_id may point to a grandparent order, skipping
        # the intermediate exchange ticket whose own new products would be lost.
        try:
            note_tags = json.loads(exchange_order.internal_note) if exchange_order.internal_note else []
            for tag in note_tags:
                text = tag.get('text', '') if isinstance(tag, dict) else ''
                ref_match = re.search(r'Ref:\s*(\S+)', text)
                if ref_match:
                    source_ticket = self.env['pos.order'].search([
                        ('pos_reference', '=', ref_match.group(1)),
                    ], limit=1)
                    if source_ticket and source_ticket.id not in visited_order_ids:
                        orders_to_check.add(source_ticket.id)
                    break
        except (json.JSONDecodeError, TypeError):
            pass

        while orders_to_check:
            current_id = orders_to_check.pop()
            if current_id in visited_order_ids:
                continue
            visited_order_ids.add(current_id)

            ancestor_order = self.env['pos.order'].browse(current_id)
            if not ancestor_order.exists():
                continue

            # Determine if ancestor is itself an exchange
            is_ancestor_exchange = (
                any(l.qty > 0 for l in ancestor_order.lines)
                and any(l.qty < 0 for l in ancestor_order.lines)
            )

            for orig_line in ancestor_order.lines:
                # Skip negative lines (returns within exchange orders)
                if orig_line.qty <= 0:
                    continue

                # Calculate total refunded/exchanged across ALL operations on this line
                total_refunded = sum(
                    abs(rl.qty) for rl in orig_line.refund_orderline_ids
                    if rl.order_id.state not in ('cancel', 'draft')
                )
                remaining = max(0, orig_line.qty - total_refunded)

                if remaining > 0:
                    inherited_lines.append({
                        'id': orig_line.id,
                        'product_id': orig_line.product_id.id,
                        'name': orig_line.product_id.display_name,
                        'qty': remaining,
                        'remaining_qty': remaining,
                        'price_unit': orig_line.price_unit,
                        'price_subtotal_incl': (
                            orig_line.price_subtotal_incl * (remaining / orig_line.qty)
                            if orig_line.qty else 0
                        ),
                        'discount': orig_line.discount,
                        'tax_ids': orig_line.tax_ids.ids,
                        'is_exchange_product': False,
                        'is_inherited_line': True,
                    })

            # If ancestor is also an exchange, follow its chain further
            if is_ancestor_exchange:
                for anc_line in ancestor_order.lines:
                    if anc_line.refunded_orderline_id:
                        grandparent_id = anc_line.refunded_orderline_id.order_id.id
                        if grandparent_id not in visited_order_ids:
                            orders_to_check.add(grandparent_id)

        return inherited_lines

    # =====================================================================
    # Utility Methods
    # =====================================================================

    def _extract_note_metadata(self, note, key):
        """
        Extrae metadatos de una nota de picking.
        Soporta dos formatos:
        - Comentarios HTML: <!-- KEY:value -->
        - Texto plano (legacy): KEY:value
        Retorna el valor como string, o '' si no se encuentra.
        """
        if not note:
            return ''

        # Formato 1: Comentario HTML <!-- KEY:value -->
        comment_match = re.search(r'<!--\s*' + re.escape(key) + r':(.+?)\s*-->', note)
        if comment_match:
            return comment_match.group(1).strip()

        # Formato 2: Texto plano (legacy / fallback)
        raw = note.replace('<br/>', '\n').replace('<br>', '\n')
        raw = re.sub(r'<[^>]+>', '', raw)
        if key + ':' in raw:
            try:
                value = raw[raw.index(key + ':') + len(key) + 1:].split('\n')[0].strip()
                return value
            except (ValueError, IndexError):
                pass

        return ''
# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
import json
import logging
import re

_logger = logging.getLogger(__name__)


class PosSession(models.Model):
    """
    Extensión de pos.session para manejar devoluciones de mercancía
    sin registro de venta previo en el sistema.
    
    Flujo:
    1. Usuario abre popup de devolución desde el POS
    2. Ingresa número de ticket y selecciona productos
    3. Sistema crea automáticamente:
       - stock.picking de recepción (entrada de inventario)
       - account.bank.statement.line negativo (salida de efectivo)
    """
    _inherit = 'pos.session'

    def _create_picking_at_end_of_session(self):
        """Override to exclude exchange payment orders from session-end picking creation.

        When update_stock_at_closing=True, this method creates pickings for all orders
        at session close. Exchange payment orders (is_exchange_payment=True) must be
        excluded because their inventory was already handled by create_exchange().

        This mirrors the native implementation in
        addons/point_of_sale/models/pos_session.py _create_picking_at_end_of_session()
        with the single addition of the is_exchange_payment filter.
        """
        self.ensure_one()
        lines_grouped_by_dest_location = {}
        picking_type = self.config_id.picking_type_id

        if not picking_type or not picking_type.default_location_dest_id:
            session_destination_id = self.env['stock.warehouse']._get_partner_locations()[0].id
        else:
            session_destination_id = picking_type.default_location_dest_id.id

        for order in self._get_closed_orders():
            # === ADDED: Skip exchange payment orders (pickings already created) ===
            if order.is_exchange_payment:
                continue
            if order.company_id.anglo_saxon_accounting and order.is_invoiced or order.shipping_date:
                continue
            destination_id = order.partner_id.property_stock_customer.id or session_destination_id
            if destination_id in lines_grouped_by_dest_location:
                lines_grouped_by_dest_location[destination_id] |= order.lines
            else:
                lines_grouped_by_dest_location[destination_id] = order.lines

        for location_dest_id, lines in lines_grouped_by_dest_location.items():
            pickings = self.env['stock.picking']._create_picking_from_pos_order_lines(location_dest_id, lines, picking_type)
            pickings.write({'pos_session_id': self.id, 'origin': self.name})

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

    def create_return(self, ticket, products_data, return_type='odoo', partner_id=False):
        """
        Crea una devolución completa con recepción de inventario y salida de efectivo.
        
        :param ticket: Número de ticket o razón (si es sin ticket)
        :param products_data: Lista de productos
        :param return_type: 'odoo', 'arus', 'no_ticket'
        :param partner_id: ID del cliente (opcional)
        """
        self.ensure_one()
        _logger.info("POS Return: Creating return type %s for %s", return_type, ticket)
        
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
                
            # Determinar Referencia de Origen y Razón del Pago
            if return_type == 'no_ticket':
                # El argumento ticket contiene la razón
                user_reason = ticket
                origin_ref = "SIN_TICKET"
                base_type = str(_("Devolución"))
                payment_reason = user_reason
            elif return_type == 'arus':
                origin_ref = ticket
                payment_reason = ticket
                base_type = str(_("Devolución Arus"))
            else: # odoo
                origin_ref = ticket
                payment_reason = ticket
                base_type = str(_("Devolución Odoo"))
            
            # === Duplicate detection ===
            # For Odoo tickets, check if a return picking already exists for this ref
            # to prevent double-click or retried RPC calls from creating duplicates.
            if return_type == 'odoo' and origin_ref:
                existing_picking = self.env['stock.picking'].search([
                    ('origin', '=', origin_ref),
                    ('picking_type_id.code', '=', 'incoming'),
                    ('state', '!=', 'cancel'),
                ], limit=1)
                if existing_picking:
                    _logger.warning(
                        "POS Return: Duplicate return detected for %s. "
                        "Existing picking: %s. Skipping creation.",
                        origin_ref, existing_picking.name
                    )
                    return {
                        'success': True,
                        'picking_id': existing_picking.id,
                        'picking_name': existing_picking.name,
                        'total_amount': total_amount,
                        'message': str(_("Devolución ya procesada anteriormente.")),
                        'original_order_id': False,
                        'duplicate': True,
                    }
            
            # Crear la recepción de inventario (stock.picking)
            # Para 'no_ticket', usamos la razón como nota (que viene en el argumento ticket).
            note = ticket if return_type == 'no_ticket' else False
            picking = self._create_return_receipt(origin_ref, products_data, partner_id, note=note)
            
            # Crear la salida de efectivo (account.bank.statement.line)
            self._create_return_cash_out(payment_reason, total_amount, partner_id, type_label=base_type)
            
            _logger.info("POS Return: Successfully created picking %s", picking.name)
            
            # === Mark original order as returned (for Odoo tickets only) ===
            original_order = False
            if return_type == 'odoo' and ticket:
                original_order = self.env['pos.order'].search([
                    ('pos_reference', '=', ticket),
                ], limit=1)
                if original_order:
                    try:
                        original_order.custom_return_done = True
                    except (AttributeError, Exception):
                        self.env.cr.execute(
                            "UPDATE pos_order SET custom_return_done = true WHERE id = %s",
                            [original_order.id]
                        )
                    _logger.info("POS Return: Marked order %s as returned", original_order.name)
            
            return {
                'success': True,
                'picking_id': picking.id,
                'picking_name': picking.name,
                'total_amount': total_amount,
                'message': str(_("Devolución creada exitosamente.")),
                'original_order_id': original_order.id if original_order else False,
            }
        except Exception as e:
            _logger.exception("POS Return: Error creating return")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _create_return_receipt(self, origin_ref, products_data, partner_id=False, note=False):
        """
        Crea una recepción de inventario (stock.picking) para la devolución.
        
        :param origin_ref: Referencia de origen (Ticket o "SIN_TICKET")
        :param products_data: Lista de productos a recibir
        :param partner_id: ID del cliente asociado (opcional)
        :param note: Nota interna para el picking (opcional)
        :return: stock.picking creado y validado
        """
        self.ensure_one()
        
        # Obtener el almacén desde la configuración del POS
        warehouse = self.config_id.picking_type_id.warehouse_id
        if not warehouse:
            # Fallback: buscar almacén por defecto de la compañía
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.company_id.id)
            ], limit=1)
        
        if not warehouse:
            raise UserError(str(_("No se encontró un almacén configurado.")))
        
        # Obtener tipo de operación de recepción
        picking_type = warehouse.in_type_id
        if not picking_type:
            raise UserError(str(_("No se encontró un tipo de operación de recepción en el almacén.")))
        
        # Ubicaciones: origen (proveedores/virtual) y destino (stock principal)
        location_src = self.env.ref('stock.stock_location_suppliers')
        location_dest = warehouse.lot_stock_id
        
        # Crear el picking (recepción de devolución)
        picking_vals = {
            'picking_type_id': picking_type.id,
            'partner_id': partner_id, 
            'origin': origin_ref,     
            'location_id': location_src.id,
            'location_dest_id': location_dest.id,
            'scheduled_date': fields.Datetime.now(),
            'move_type': 'direct',  # Recepción directa
            'note': note,
        }
        picking = self.env['stock.picking'].create(picking_vals)
        
        # Crear movimientos de stock para cada producto
        for product_data in products_data:
            product = self.env['product.product'].browse(product_data['product_id'])
            if not product.exists():
                raise UserError(str(_("Producto no encontrado: ID %s") % product_data['product_id']))
            
            move_vals = {
                'product_id': product.id,
                'product_uom_qty': product_data['quantity'],
                'product_uom': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': location_src.id,
                'location_dest_id': location_dest.id,
                'picking_type_id': picking_type.id,
            }
            self.env['stock.move'].create(move_vals)
        
        # Flujo de validación del picking
        # 1. Confirmar el picking (draft -> confirmed/waiting)
        picking.action_confirm()
        
        # 2. Asignar/reservar (para recepciones esto suele ser automático)
        picking.action_assign()
        
        # 3. Marcar cantidades como realizadas
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        
        # 4. Validar usando _action_done para evitar wizards/validaciones UI
        # Wrapped in savepoint + try/except following native POS pattern
        # (see addons/point_of_sale/models/stock_picking.py _create_picking_from_pos_order_lines)
        # This ensures the picking is created even if validation fails
        # (e.g., missing lots, strict reservation rules)
        self.env.flush_all()
        try:
            with self.env.cr.savepoint():
                picking._action_done()
        except (UserError, ValidationError):
            _logger.warning(
                "POS Return: Picking %s could not be fully validated. "
                "It will need manual processing.", picking.name
            )
        
        return picking
    
    def _create_return_cash_out(self, reason, amount, partner_id=False, type_label=False):
        """
        Crea una salida de efectivo para la devolución.
        
        :param reason: Razón del pago
        :param amount: Monto a devolver
        :param partner_id: ID del cliente
        :param type_label: Etiqueta del tipo de movimiento (ej. Devolución Odoo)
        """
        self.ensure_one()
        
        partner = self.env['res.partner'].browse(partner_id) if partner_id else False
        
        # Use provided label or default
        t_type = type_label if type_label else str(_("Devolución"))
        extras = {'translatedType': t_type}
        
        partner_val = partner.id if partner else False
        self.try_cash_in_out('out', amount, reason, partner_val, extras)

    def get_partner_tickets(self, partner_id):
        """
        Busca los últimos pedidos (tickets) de un cliente específico.
        Limitado a los 20 más recientes para evitar sobrecarga.
        
        Excluye:
        - Reembolsos nativos (is_refund = True)
        - Órdenes ya devueltas por pos_return (custom_return_done = True)
        - Órdenes ya intercambiadas por pos_return (custom_exchange_done = True)
        
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
            ('custom_exchange_replaced', '=', False),
        ]
        orders = self.env['pos.order'].search(domain, order='date_order desc', limit=20)
        
        # Also exclude orders fully refunded via native Odoo refund
        orders = orders.filtered(
            lambda o: not all(line.refund_orderline_ids for line in o.lines)
        )
        
        results = []
        for order in orders:
            origin_refs = [order.pos_reference, order.name]
            
            # Buscar devoluciones previas (Pickings de entrada con origen = referencia del ticket)
            return_pickings = self.env['stock.picking'].search([
                ('origin', 'in', origin_refs),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'incoming')
            ])
            
            # Buscar entregas de intercambio (Pickings de salida)
            # Nuevos intercambios usan prefijo INT: en el origin
            # Para backward compatibility, también buscamos sin prefijo
            # pero excluimos pickings del POS (que usan el picking_type del config)
            exchange_out_origins = ['INT:' + ref for ref in origin_refs]
            
            # Buscar con prefijo INT: (nuevos intercambios)
            exchange_pickings = self.env['stock.picking'].search([
                ('origin', 'in', exchange_out_origins),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'outgoing')
            ])
            
            # Backward compatibility: buscar sin prefijo, excluyendo pickings del POS
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
            exchanges_data = []
            exchange_delivery_origins = set()
            for ep in exchange_pickings:
                exchange_delivery_origins.add(ep.origin)
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
            
            # Calcular cantidad ya devuelta por producto
            returned_qty_by_product = {}
            for picking in return_pickings:
                for move in picking.move_ids:
                    pid = move.product_id.id
                    returned_qty_by_product[pid] = returned_qty_by_product.get(pid, 0) + move.quantity

            lines_data = []       # Solo líneas con remaining > 0 (para selección)
            all_lines_data = []   # TODAS las líneas (para visualización)
            total_original_qty = 0
            total_remaining_qty = 0

            for line in order.lines:
                pid = line.product_id.id
                original_qty = line.qty
                returned_so_far = returned_qty_by_product.get(pid, 0)
                
                remaining_qty = max(0, original_qty - returned_so_far)
                
                deducted = original_qty - remaining_qty
                returned_qty_by_product[pid] = max(0, returned_so_far - deducted)

                total_original_qty += original_qty
                total_remaining_qty += remaining_qty

                line_dict = {
                    'id': line.id,
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'qty': original_qty,
                    'remaining_qty': remaining_qty,
                    'price_unit': line.price_unit,
                    'price_subtotal_incl': line.price_subtotal_incl,
                    'is_exchange_product': False,
                }

                # all_lines siempre incluye la línea
                all_lines_data.append(line_dict)

                # lines solo incluye si hay remanente
                if remaining_qty > 0:
                    lines_data.append(line_dict)

            # === Agregar productos de intercambio como líneas retornables ===
            # Estos son productos que el cliente recibió en un intercambio previo
            # y que puede devolver o volver a intercambiar
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

                    # Check returns specific to this exchange delivery picking
                    ex_returned = ep_returned_by_product.get(pid, 0)

                    # Also check the general return pool (returned_qty_by_product)
                    # This catches returns where origin = ticket ref (e.g., when an
                    # exchange product is returned via a subsequent exchange)
                    general_returned = returned_qty_by_product.get(pid, 0)
                    total_returned = ex_returned + general_returned

                    ex_remaining = max(0, ex_qty - total_returned)

                    # Deduct consumed quantities from both pools
                    consumed = ex_qty - ex_remaining
                    ep_consumed = min(consumed, ex_returned)
                    ep_returned_by_product[pid] = max(0, ex_returned - ep_consumed)
                    general_consumed = consumed - ep_consumed
                    returned_qty_by_product[pid] = max(0, general_returned - general_consumed)

                    total_original_qty += ex_qty
                    total_remaining_qty += ex_remaining

                    # Use stored exchange price > pricelist price > lst_price
                    stored_price = exchange_prices.get(str(pid))
                    if stored_price is not None:
                        ex_price = stored_price
                    else:
                        # Fallback for exchanges created before price storage
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
                        'price_subtotal_incl': ex_price * ex_qty,
                        'is_exchange_product': True,
                        'exchange_picking': ep.name,
                    }

                    all_lines_data.append(ex_line_dict)
                    if ex_remaining > 0:
                        lines_data.append(ex_line_dict)

            # Determinar estado de devolución del ticket
            if total_original_qty == 0:
                return_status = 'none'
            elif total_remaining_qty == 0:
                return_status = 'full'
            elif total_remaining_qty < total_original_qty:
                return_status = 'partial'
            else:
                return_status = 'none'

            results.append({
                'id': order.id,
                'name': order.name,
                'pos_reference': order.pos_reference,
                'date_order': order.date_order,
                'amount_total': order.amount_total,
                'lines': lines_data,
                'all_lines': all_lines_data,
                'return_status': return_status,
                'total_original_qty': total_original_qty,
                'total_remaining_qty': total_remaining_qty,
                'has_exchange': len(exchanges_data) > 0,
                'exchanges': exchanges_data,
            })

        # Exclude tickets with no remaining returnable products
        results = [r for r in results if r['total_remaining_qty'] > 0 or r['return_status'] == 'none']
            
        return results

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
            ('custom_exchange_replaced', '=', False),
            '|',
            ('pos_reference', 'ilike', query),
            ('name', 'ilike', query),
        ]
        orders = self.env['pos.order'].search(domain, order='date_order desc', limit=10)
        
        # Also exclude orders fully refunded via native Odoo refund
        orders = orders.filtered(
            lambda o: not all(line.refund_orderline_ids for line in o.lines)
        )
        
        if not orders:
            return []
        
        results = []
        for order in orders:
            origin_refs = [order.pos_reference, order.name]
            
            # Buscar devoluciones previas
            return_pickings = self.env['stock.picking'].search([
                ('origin', 'in', origin_refs),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'incoming')
            ])
            
            # Buscar entregas de intercambio
            exchange_out_origins = ['INT:' + ref for ref in origin_refs]
            exchange_pickings = self.env['stock.picking'].search([
                ('origin', 'in', exchange_out_origins),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'outgoing')
            ])
            
            if not exchange_pickings:
                pos_picking_type = self.config_id.picking_type_id
                legacy_pickings = self.env['stock.picking'].search([
                    ('origin', 'in', origin_refs),
                    ('state', '=', 'done'),
                    ('picking_type_id.code', '=', 'outgoing'),
                    ('picking_type_id', '!=', pos_picking_type.id),
                ])
                exchange_pickings = legacy_pickings
            
            # Calcular cantidad devuelta por producto
            returned_qty_by_product = {}
            for picking in return_pickings:
                for move in picking.move_ids:
                    pid = move.product_id.id
                    returned_qty_by_product[pid] = returned_qty_by_product.get(pid, 0) + move.quantity

            lines_data = []
            all_lines_data = []
            total_original_qty = 0
            total_remaining_qty = 0

            for line in order.lines:
                pid = line.product_id.id
                original_qty = line.qty
                returned_so_far = returned_qty_by_product.get(pid, 0)
                remaining_qty = max(0, original_qty - returned_so_far)
                deducted = original_qty - remaining_qty
                returned_qty_by_product[pid] = max(0, returned_so_far - deducted)

                total_original_qty += original_qty
                total_remaining_qty += remaining_qty

                line_dict = {
                    'id': line.id,
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'qty': original_qty,
                    'remaining_qty': remaining_qty,
                    'price_unit': line.price_unit,
                    'price_subtotal_incl': line.price_subtotal_incl,
                    'is_exchange_product': False,
                }
                all_lines_data.append(line_dict)
                if remaining_qty > 0:
                    lines_data.append(line_dict)

            # Determinar estado
            if total_original_qty == 0:
                return_status = 'none'
            elif total_remaining_qty == 0:
                return_status = 'full'
            elif total_remaining_qty < total_original_qty:
                return_status = 'partial'
            else:
                return_status = 'none'

            results.append({
                'id': order.id,
                'name': order.name,
                'pos_reference': order.pos_reference,
                'date_order': order.date_order,
                'amount_total': order.amount_total,
                'partner_id': order.partner_id.id if order.partner_id else False,
                'partner_name': order.partner_id.name if order.partner_id else _('Sin cliente'),
                'lines': lines_data,
                'all_lines': all_lines_data,
                'return_status': return_status,
                'total_original_qty': total_original_qty,
                'total_remaining_qty': total_remaining_qty,
                'has_exchange': len(exchange_pickings) > 0,
                'exchanges': [],
            })

        # Exclude tickets with no remaining returnable products
        results = [r for r in results if r['total_remaining_qty'] > 0 or r['return_status'] == 'none']
            
        return results

    # =====================================================================
    # INTERCAMBIO (Exchange) Methods
    # =====================================================================

    def create_exchange(self, ticket, returned_products, new_products, exchange_type='odoo', partner_id=False):
        """
        Crea un intercambio de productos con manejo inteligente de dinero.
        
        Flujo:
        1. Valida datos de entrada
        2. Crea picking de recepción (productos devueltos entran al inventario)
        3. Crea picking de entrega (productos nuevos salen del inventario)
        4. Calcula diferencia y crea movimiento de caja solo si es necesario
        
        :param ticket: Número de ticket o razón (si es sin ticket)
        :param returned_products: Lista de productos devueltos [{product_id, quantity, price_unit}]
        :param new_products: Lista de productos nuevos [{product_id, quantity, price_unit}]
        :param exchange_type: 'odoo', 'arus', 'no_ticket'
        :param partner_id: ID del cliente (opcional)
        """
        self.ensure_one()
        _logger.info("POS Exchange: Creating exchange type %s for %s", exchange_type, ticket)
        
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
                user_reason = ticket
                origin_ref = "INTERCAMBIO_SIN_TICKET"
                base_type = str(_("Intercambio"))
                payment_reason = user_reason
            elif exchange_type == 'arus':
                origin_ref = ticket
                payment_reason = ticket
                base_type = str(_("Intercambio Arus"))
            else:  # odoo
                origin_ref = ticket
                payment_reason = ticket
                base_type = str(_("Intercambio Odoo"))
            
            # === Duplicate detection for exchanges ===
            # For Odoo tickets, check if exchange pickings already exist
            if exchange_type == 'odoo' and origin_ref:
                int_origin = 'INT:' + origin_ref
                existing_exchange = self.env['stock.picking'].search([
                    ('origin', '=', int_origin),
                    ('state', '!=', 'cancel'),
                ], limit=1)
                if existing_exchange:
                    _logger.warning(
                        "POS Exchange: Duplicate exchange detected for %s. "
                        "Existing picking: %s. Skipping creation.",
                        origin_ref, existing_exchange.name
                    )
                    return {
                        'success': True,
                        'duplicate': True,
                        'message': str(_("Intercambio ya procesado anteriormente.")),
                        'picking_in_name': existing_exchange.name,
                        'picking_out_name': '',
                        'return_total': return_total,
                        'new_total': new_total,
                        'difference': new_total - return_total,
                        'needs_payment': False,
                        'cash_message': str(_("Intercambio duplicado - sin acción")),
                    }
            
            # === Crear picking de recepción (productos devueltos) ===
            picking_in = self._create_return_receipt(origin_ref, returned_products, partner_id)
            
            # === Crear picking de entrega (productos nuevos) ===
            picking_out = self._create_exchange_delivery(origin_ref, new_products, partner_id)
            
            # === Manejo inteligente de dinero ===
            difference = new_total - return_total
            cash_message = str(_("Sin movimiento de caja"))
            needs_payment = False
            
            if difference > 0:
                needs_payment = True
                cash_message = str(_("Pendiente de cobro"))
            elif difference < 0:
                self._create_exchange_cash_movement(
                    payment_reason, abs(difference), partner_id, 'out',
                    type_label=str(_("%s - Devolución") % base_type)
                )
                cash_message = str(_("Devolución al cliente"))
            
            # === Construir nota legible para los pickings ===
            readable_note = self._build_exchange_note(
                exchange_type, ticket,
                returned_products, new_products,
                picking_in, picking_out,
                difference, cash_message
            )
            picking_out.note = readable_note
            picking_in.note = readable_note
            
            _logger.info(
                "POS Exchange: Created. In: %s, Out: %s, Diff: %s",
                picking_in.name, picking_out.name, difference
            )
            
            # === Mark original order as exchanged ===
            # Always set custom_exchange_done for badge display and refund blocking.
            # When needs_payment=True (case 3: customer pays difference), also set
            # custom_exchange_replaced to hide the original ticket from the popup since
            # a new replacement POS order will be created.
            original_order = False
            if exchange_type == 'odoo' and ticket:
                original_order = self.env['pos.order'].search([
                    ('pos_reference', '=', ticket),
                ], limit=1)
                if original_order:
                    try:
                        original_order.custom_exchange_done = True
                        if needs_payment:
                            original_order.custom_exchange_replaced = True
                    except (AttributeError, Exception):
                        self.env.cr.execute(
                            "UPDATE pos_order SET custom_exchange_done = true WHERE id = %s",
                            [original_order.id]
                        )
                        if needs_payment:
                            self.env.cr.execute(
                                "UPDATE pos_order SET custom_exchange_replaced = true WHERE id = %s",
                                [original_order.id]
                            )
                    _logger.info("POS Exchange: Marked order %s as exchanged%s", original_order.name, ' (replaced by new ticket)' if needs_payment else '')
            
            return {
                'success': True,
                'picking_in_id': picking_in.id,
                'picking_in_name': picking_in.name,
                'picking_out_id': picking_out.id,
                'picking_out_name': picking_out.name,
                'return_total': return_total,
                'new_total': new_total,
                'difference': difference,
                'needs_payment': needs_payment,
                'cash_message': cash_message,
                'message': str(_("Intercambio creado exitosamente.")),
                'returned_products_data': returned_products if needs_payment else [],
                'new_products_data': new_products if needs_payment else [],
                'exchange_ref': origin_ref,
                'original_order_id': original_order.id if original_order else False,
            }
        except Exception as e:
            _logger.exception("POS Exchange: Error creating exchange")
            return {
                'success': False,
                'error': str(e),
            }

    def _create_exchange_delivery(self, origin_ref, products_data, partner_id=False):
        """
        Crea un picking de entrega (salida de inventario) para los productos nuevos del intercambio.
        
        :param origin_ref: Referencia de origen
        :param products_data: Lista de productos nuevos a entregar
        :param partner_id: ID del cliente (opcional)
        :return: stock.picking creado y validado
        """
        self.ensure_one()
        
        # Obtener el almacén desde la configuración del POS
        warehouse = self.config_id.picking_type_id.warehouse_id
        if not warehouse:
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.company_id.id)
            ], limit=1)
        
        if not warehouse:
            raise UserError(str(_("No se encontró un almacén configurado.")))
        
        # Tipo de operación de entrega (salida)
        picking_type = warehouse.out_type_id
        if not picking_type:
            raise UserError(str(_("No se encontró un tipo de operación de entrega en el almacén.")))
        
        # Ubicaciones: origen (stock principal) y destino (clientes)
        location_src = warehouse.lot_stock_id
        location_dest = self.env.ref('stock.stock_location_customers')
        
        # Crear el picking de entrega (nota se asigna después en create_exchange)
        picking_vals = {
            'picking_type_id': picking_type.id,
            'partner_id': partner_id,
            'origin': 'INT:' + origin_ref,
            'location_id': location_src.id,
            'location_dest_id': location_dest.id,
            'scheduled_date': fields.Datetime.now(),
            'move_type': 'direct',
        }
        picking = self.env['stock.picking'].create(picking_vals)
        
        # Crear movimientos de stock para cada producto
        for product_data in products_data:
            product = self.env['product.product'].browse(product_data['product_id'])
            if not product.exists():
                raise UserError(str(_("Producto no encontrado: ID %s") % product_data['product_id']))
            
            move_vals = {
                'product_id': product.id,
                'product_uom_qty': product_data['quantity'],
                'product_uom': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': location_src.id,
                'location_dest_id': location_dest.id,
                'picking_type_id': picking_type.id,
            }
            self.env['stock.move'].create(move_vals)
        
        # Flujo de validación
        picking.action_confirm()
        picking.action_assign()
        
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        
        # Wrapped in savepoint + try/except following native POS pattern
        self.env.flush_all()
        try:
            with self.env.cr.savepoint():
                picking._action_done()
        except (UserError, ValidationError):
            _logger.warning(
                "POS Exchange: Delivery picking %s could not be fully validated. "
                "It will need manual processing.", picking.name
            )
        
        return picking

    def _create_exchange_cash_movement(self, reason, amount, partner_id=False, direction='out', type_label=False):
        """
        Crea un movimiento de caja para el intercambio, solo si el monto es mayor a 0.
        
        :param reason: Razón del movimiento
        :param amount: Monto absoluto del movimiento
        :param partner_id: ID del cliente
        :param direction: 'in' para cobro al cliente, 'out' para devolución al cliente
        :param type_label: Etiqueta personalizada del tipo de movimiento
        """
        self.ensure_one()
        
        if amount <= 0:
            return  # No crear movimiento si no hay diferencia
        
        partner = self.env['res.partner'].browse(partner_id) if partner_id else False
        t_type = type_label if type_label else str(_("Intercambio"))
        extras = {'translatedType': t_type}
        partner_val = partner.id if partner else False
        
        self.try_cash_in_out(direction, amount, reason, partner_val, extras)

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

    def _build_exchange_note(self, exchange_type, ticket,
                             returned_products, new_products,
                             picking_in, picking_out,
                             difference, cash_message):
        """
        Construye una nota HTML legible y detallada para los pickings del intercambio.
        Incluye EXCHANGE_PRICES y EXCHANGE_RETURN al final para recuperación interna.
        """
        # Encabezado
        type_labels = {
            'no_ticket': str(_('INTERCAMBIO SIN TICKET')),
            'arus': str(_('INTERCAMBIO ARUS')),
            'odoo': str(_('INTERCAMBIO ODOO')),
        }
        header = type_labels.get(exchange_type, str(_('INTERCAMBIO')))
        
        html = '<div style="font-family: sans-serif; font-size: 13px;">'
        html += '<p><strong>═══ %s ═══</strong></p>' % header
        
        if exchange_type == 'no_ticket':
            html += '<p>%s: %s</p>' % (str(_('Razón')), ticket)
        else:
            html += '<p>%s: %s</p>' % (str(_('Ticket origen')), ticket)
        
        # Productos devueltos
        html += '<p><strong style="color:#dc2626;">%s</strong> (%s: %s):</p>' % (
            str(_('Productos devueltos')), str(_('Recepción')), picking_in.name
        )
        html += '<ul>'
        for p in returned_products:
            product = self.env['product.product'].browse(p['product_id'])
            html += '<li>%s x%s — $%.2f</li>' % (
                product.display_name, int(p['quantity']), p['price_unit']
            )
        html += '</ul>'
        
        # Productos entregados
        html += '<p><strong style="color:#16a34a;">%s</strong> (%s: %s):</p>' % (
            str(_('Productos entregados')), str(_('Entrega')), picking_out.name
        )
        html += '<ul>'
        for p in new_products:
            product = self.env['product.product'].browse(p['product_id'])
            html += '<li>%s x%s — $%.2f</li>' % (
                product.display_name, int(p['quantity']), p['price_unit']
            )
        html += '</ul>'
        
        # Resumen financiero
        html += '<p><strong>%s:</strong> $%.2f (%s)</p>' % (
            str(_('Diferencia')), abs(difference), cash_message
        )
        html += '</div>'
        
        # Metadata interna en comentarios HTML (sobrevive la sanitización de Odoo)
        price_map = {str(pd['product_id']): pd['price_unit'] for pd in new_products}
        html += '<!-- EXCHANGE_PRICES:' + json.dumps(price_map) + ' -->'
        html += '<!-- EXCHANGE_RETURN:' + picking_in.name + ' -->'
        
        return html
# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

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
            
            # Crear la recepción de inventario (stock.picking)
            # Para 'no_ticket', usamos la razón como nota (que viene en el argumento ticket).
            note = ticket if return_type == 'no_ticket' else False
            picking = self._create_return_receipt(origin_ref, products_data, partner_id, note=note)
            
            # Crear la salida de efectivo (account.bank.statement.line)
            self._create_return_cash_out(payment_reason, total_amount, partner_id, type_label=base_type)
            
            _logger.info("POS Return: Successfully created picking %s", picking.name)
            
            return {
                'success': True,
                'picking_id': picking.id,
                'picking_name': picking.name,
                'total_amount': total_amount,
                'message': str(_("Devolución creada exitosamente."))
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
        picking._action_done()
        
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
        """
        domain = [('partner_id', '=', partner_id), ('state', 'in', ['paid', 'done', 'invoiced'])]
        orders = self.env['pos.order'].search(domain, order='date_order desc', limit=20)
        
        results = []
        for order in orders:
            # Buscar devoluciones previas (Pickings de entrada con origen = referencia del ticket)
            # Notas: 
            # - Buscamos tanto por pos_reference como por name para asegurar cobertura
            # - Solo consideramos pickings en estado 'done' (efectivamente devueltos)
            # - Solo tipo 'incoming' (recepción)
            return_pickings = self.env['stock.picking'].search([
                ('origin', 'in', [order.pos_reference, order.name]),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'incoming')
            ])
            
            # Calcular cantidad ya devuelta por producto
            returned_qty_by_product = {}
            for picking in return_pickings:
                for move in picking.move_ids:
                    pid = move.product_id.id
                    returned_qty_by_product[pid] = returned_qty_by_product.get(pid, 0) + move.quantity

            lines_data = []
            for line in order.lines:
                pid = line.product_id.id
                original_qty = line.qty
                returned_so_far = returned_qty_by_product.get(pid, 0)
                
                # Calcular remanente para esta línea
                # Si hay multiples lineas del mismo producto, vamos descontando del acumulado
                remaining_qty = max(0, original_qty - returned_so_far)
                
                # Actualizar el acumulado de devueltos (consumir lo que se pudo de esta linea)
                # Si teniamos 3 devueltos y esta linea es de 5:
                # remaining = 2.
                # Nuevo returned_so_far debe ser 0 para la proxima linea (ya "gastamos" los 3 devueltos aqui)
                # Si teniamos 10 devueltos y linea de 5:
                # remaining = 0.
                # Nuevo returned_so_far = 5 (sobran 5 por descontar a otras lineas)
                deducted = original_qty - remaining_qty
                returned_qty_by_product[pid] = max(0, returned_so_far - deducted)
                
                if remaining_qty > 0:
                    lines_data.append({
                        'id': line.id,
                        'product_id': line.product_id.id,
                        'name': line.product_id.name,
                        'qty': original_qty, # Cantidad original de la compra
                        'remaining_qty': remaining_qty, # Cantidad disponible para devolver
                        'price_unit': line.price_unit,
                        'price_subtotal_incl': line.price_subtotal_incl,
                    })

            results.append({
                'id': order.id,
                'name': order.name,
                'pos_reference': order.pos_reference,
                'date_order': order.date_order,
                'amount_total': order.amount_total,
                'lines': lines_data,
            })
            
        return results
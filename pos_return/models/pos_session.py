# -*- coding: utf-8 -*-
# =============================================================================
# MÓDULO: pos_return
# ARCHIVO: models/pos_session.py
# DESCRIPCIÓN: Extensión de pos.session para gestionar devoluciones
# MIGRADO: Odoo 18 -> Odoo 19
# FECHA: 2026-02-01
# =============================================================================
# 
# NOTAS DE MIGRACIÓN:
# - Los imports de 'odoo' (api, fields, models, _) siguen siendo válidos en v19
# - UserError de odoo.exceptions sigue siendo el estándar
# - No se usan APIs deprecadas (odoo.osv, record._cr, record._context, etc.)
# - El ORM (search, create, browse, exists) mantiene la misma sintaxis
# - stock.move.quantity y stock.move.picked siguen siendo válidos en v19
# - account.bank.statement.line sigue existiendo y acepta los mismos campos
#
# =============================================================================

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

    def create_return(self, ticket, products_data):
        """
        Crea una devolución completa con recepción de inventario y salida de efectivo.
        
        Este es el método principal llamado desde el frontend para procesar
        una devolución de mercancía.
        
        :param ticket: Número de ticket externo (obligatorio para trazabilidad)
        :param products_data: Lista de diccionarios con productos:
            [{'product_id': int, 'quantity': float, 'price_unit': float}, ...]
        :return: dict con resultado de la operación
        :raises UserError: Si faltan datos obligatorios o hay errores de validación
        """
        self.ensure_one()
        _logger.info("POS Return: Creating return for ticket %s", ticket)
        
        try:
            # Validaciones de entrada
            if not ticket:
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
            
            # Crear la recepción de inventario (stock.picking)
            picking = self._create_return_receipt(ticket, products_data)
            
            # Crear la salida de efectivo (account.bank.statement.line)
            self._create_return_cash_out(ticket, total_amount)
            
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
    
    def _create_return_receipt(self, ticket, products_data):
        """
        Crea una recepción de inventario (stock.picking) para la devolución.
        
        El picking se crea como entrada desde ubicación de proveedores
        hacia el stock principal del almacén del POS.
        
        :param ticket: Número de ticket (usado como origin/referencia)
        :param products_data: Lista de productos a recibir
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
            'partner_id': False,  # Sin partner - devolución de cliente anónimo
            'origin': ticket,     # Número de ticket como referencia
            'location_id': location_src.id,
            'location_dest_id': location_dest.id,
            'scheduled_date': fields.Datetime.now(),
            'move_type': 'direct',  # Recepción directa
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
        # NOTA MIGRACIÓN v19: Los campos 'quantity' y 'picked' en stock.move
        # siguen siendo válidos. En v19 se quitaron los valuation layers
        # pero la mecánica de move.quantity se mantiene.
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        
        # 4. Validar usando _action_done para evitar wizards/validaciones UI
        # TODO: revisar en Odoo 19 - Si hay cambios en el flujo de validación,
        # considerar usar button_validate() con context para bypass
        picking._action_done()
        
        return picking
    
    def _create_return_cash_out(self, ticket, amount):
        """
        Crea una salida de efectivo para la devolución.
        
        Registra un movimiento negativo en el statement line asociado
        a la sesión POS actual usando el método estándar try_cash_in_out.
        
        :param ticket: Número de ticket (para referencia del pago)
        :param amount: Monto a devolver (se registrará como salida)
        """
        self.ensure_one()
        
        reason = str(_("Devolucion ticket: %s") % ticket)
        
        # Usar el método estándar de Odoo para movimientos de efectivo
        # Esto asegura compatibilidad con la lógica contable de v18/v19
        # try_cash_in_out(type, amount, reason, partner, extras)
        self.try_cash_in_out('out', amount, reason, False, {'translatedType': str(_("Devolución"))})

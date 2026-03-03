# -*- coding: utf-8 -*-
# Servicio de Pronóstico de Producción
# Modelo abstracto que expone métodos RPC para el frontend OWL.js

import logging
import math
import json
import io
import base64
from collections import defaultdict

from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductionForecastService(models.AbstractModel):
    """
    Servicio RPC para el módulo de Pronóstico de Producción.
    No crea tabla en BD (AbstractModel). Provee métodos que el
    frontend OWL.js consume vía orm.call().
    """
    _name = 'production.forecast.service'
    _description = 'Servicio de Pronóstico de Producción'

    # -------------------------------------------------------------------------
    # MÉTODOS PÚBLICOS (RPC)
    # -------------------------------------------------------------------------

    @api.model
    def get_forecast_data(self, product_id, mode='on_hand'):
        """
        Método principal: recibe un producto y un modo de stock,
        explota la BoM recursivamente, calcula cantidades producibles
        y retorna todos los datos para el frontend.

        :param product_id: ID del product.product
        :param mode: 'on_hand' (qty_available) o 'forecasted' (virtual_available)
        :return: dict con toda la información del pronóstico
        """
        if not product_id:
            return {'error': _('Debe seleccionar un producto.')}

        product = self.env['product.product'].browse(product_id)
        if not product.exists():
            return {'error': _('Producto no encontrado.')}

        # Buscar la BoM activa para este producto
        bom = self.env['mrp.bom']._bom_find(product)
        bom = bom.get(product, self.env['mrp.bom'])

        if not bom:
            return {
                'error': _('No se encontró una Lista de Materiales (BoM) activa para "%s".', product.display_name),
            }

        try:
            # Ejecutar la explosión multinivel recursiva
            components, intermediates, log_lines = self._explode_bom_recursive(
                bom, product, 1.0, mode
            )

            # Calcular cantidades producibles por componente
            component_data = self._calculate_producible_quantities(
                components, mode
            )

            # Calcular la cantidad máxima fabricable (mínimo de todos)
            max_producible = self._calculate_max_producible(component_data)

            # Determinar indicadores semáforo
            component_data = self._assign_status_indicators(
                component_data, max_producible
            )

            # Preparar datos de productos intermedios
            intermediate_data = self._prepare_intermediate_data(
                intermediates, mode
            )

            # Generar log de cálculo
            computation_log = self._generate_computation_log(
                product, bom, mode, max_producible, component_data, log_lines
            )

            # Generar entradas estructuradas para renderizado en UI
            log_entries = self._build_structured_log_entries(
                product, bom, mode, max_producible,
                component_data, log_lines
            )

            return {
                'product': {
                    'id': product.id,
                    'name': product.name,
                    'default_code': product.default_code or '',
                    'uom': product.uom_id.name,
                    'image_url': f'/web/image/product.product/{product.id}/image_128',
                    'qty_available': round(product.qty_available, 2),
                    'virtual_available': round(product.virtual_available, 2),
                },
                'bom': {
                    'id': bom.id,
                    'name': bom.display_name,
                    'qty': bom.product_qty,
                },
                'mode': mode,
                'max_producible_qty': max_producible,
                'components': component_data,
                'intermediates': intermediate_data,
                'limiting_component': self._get_limiting_component(component_data),
                'computation_log': computation_log,
                'log_entries': log_entries,
            }

        except Exception as e:
            _logger.error(
                'Error en pronóstico de producción para producto %s: %s',
                product.display_name, str(e), exc_info=True
            )
            return {'error': _('Error al calcular el pronóstico: %s', str(e))}

    @api.model
    def get_products_with_bom(self, search_term='', limit=20):
        """
        Busca productos que tengan al menos una BoM activa.
        Usado por el autocompletado del frontend.

        :param search_term: texto de búsqueda
        :param limit: máximo de resultados
        :return: lista de dicts con id y name
        """
        domain = [('bom_ids', '!=', False)]
        if search_term:
            domain += ['|',
                ('name', 'ilike', search_term),
                ('default_code', 'ilike', search_term),
            ]

        # Buscar en product.template y obtener las variantes
        templates = self.env['product.template'].search(domain, limit=limit)
        products = templates.mapped('product_variant_ids')

        return [{
            'id': p.id,
            'name': p.display_name,
            'default_code': p.default_code or '',
            'uom': p.uom_id.name,
        } for p in products[:limit]]

    @api.model
    def export_xlsx(self, product_id, mode='on_hand'):
        """
        Genera un archivo Excel con los datos del pronóstico actual.

        :param product_id: ID del product.product
        :param mode: 'on_hand' o 'forecasted'
        :return: dict con archivo en base64 y nombre
        """
        try:
            import xlsxwriter
        except ImportError:
            raise UserError(_(
                'La librería xlsxwriter no está instalada. '
                'Contacte al administrador del sistema.'
            ))

        # Obtener datos del pronóstico
        data = self.get_forecast_data(product_id, mode)
        if 'error' in data:
            raise UserError(data['error'])

        # Crear archivo Excel en memoria
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet('Pronóstico de Producción')

        # Estilos
        header_format = workbook.add_format({
            'bold': True, 'bg_color': '#343a40', 'font_color': '#ffffff',
            'border': 1, 'align': 'center', 'valign': 'vcenter',
            'font_size': 11,
        })
        title_format = workbook.add_format({
            'bold': True, 'font_size': 14, 'align': 'left',
        })
        subtitle_format = workbook.add_format({
            'bold': True, 'font_size': 11, 'bg_color': '#e9ecef',
            'border': 1,
        })
        number_format = workbook.add_format({
            'num_format': '#,##0.00', 'border': 1, 'align': 'center',
        })
        text_format = workbook.add_format({
            'border': 1, 'align': 'left',
        })
        green_format = workbook.add_format({
            'bg_color': '#d4edda', 'border': 1, 'align': 'center',
            'font_color': '#155724',
        })
        yellow_format = workbook.add_format({
            'bg_color': '#fff3cd', 'border': 1, 'align': 'center',
            'font_color': '#856404',
        })
        red_format = workbook.add_format({
            'bg_color': '#f8d7da', 'border': 1, 'align': 'center',
            'font_color': '#721c24',
        })
        result_format = workbook.add_format({
            'bold': True, 'font_size': 16, 'align': 'center',
            'bg_color': '#007bff', 'font_color': '#ffffff',
            'border': 2,
        })

        # Anchos de columna
        sheet.set_column(0, 0, 35)  # Componente
        sheet.set_column(1, 1, 20)  # Cantidad requerida
        sheet.set_column(2, 2, 20)  # Stock disponible
        sheet.set_column(3, 3, 20)  # Uds. producibles
        sheet.set_column(4, 4, 15)  # Estado
        sheet.set_column(5, 5, 15)  # UdM

        # Encabezado del reporte
        mode_label = 'Stock En Mano' if mode == 'on_hand' else 'Stock Pronosticado'
        row = 0
        sheet.write(row, 0, 'Pronóstico de Producción', title_format)
        row += 1
        sheet.write(row, 0, f'Producto: {data["product"]["name"]}')
        row += 1
        sheet.write(row, 0, f'BoM: {data["bom"]["name"]}')
        row += 1
        sheet.write(row, 0, f'Modo: {mode_label}')
        row += 1
        sheet.write(row, 0, f'Cantidad Máxima Fabricable:')
        sheet.write(row, 1, data['max_producible_qty'], result_format)
        row += 2

        # Tabla de componentes base
        sheet.write(row, 0, 'COMPONENTES BASE', subtitle_format)
        sheet.write(row, 1, '', subtitle_format)
        sheet.write(row, 2, '', subtitle_format)
        sheet.write(row, 3, '', subtitle_format)
        sheet.write(row, 4, '', subtitle_format)
        sheet.write(row, 5, '', subtitle_format)
        row += 1

        headers = ['Componente', 'Cant. Requerida/Ud', 'Stock Disponible',
                    'Uds. Producibles', 'Estado', 'UdM']
        for col, h in enumerate(headers):
            sheet.write(row, col, h, header_format)
        row += 1

        status_formats = {
            'green': green_format,
            'yellow': yellow_format,
            'red': red_format,
        }
        status_labels = {
            'green': 'Suficiente',
            'yellow': 'Justo',
            'red': 'Limitante',
        }

        for comp in data.get('components', []):
            comp_name = f"[{comp['default_code']}] {comp['name']}" if comp.get('default_code') else comp['name']
            sheet.write(row, 0, comp_name, text_format)
            sheet.write(row, 1, comp['qty_required'], number_format)
            sheet.write(row, 2, comp['available_qty'], number_format)
            sheet.write(row, 3, comp['producible_qty'], number_format)
            fmt = status_formats.get(comp.get('status', 'green'), text_format)
            sheet.write(row, 4, status_labels.get(comp.get('status', ''), ''), fmt)
            sheet.write(row, 5, comp.get('uom', ''), text_format)
            row += 1

        # Tabla de productos intermedios (si hay)
        if data.get('intermediates'):
            row += 1
            sheet.write(row, 0, 'PRODUCTOS INTERMEDIOS EN STOCK', subtitle_format)
            sheet.write(row, 1, '', subtitle_format)
            sheet.write(row, 2, '', subtitle_format)
            sheet.write(row, 3, '', subtitle_format)
            row += 1
            int_headers = ['Producto Intermedio', 'Cant. Requerida/Ud',
                           'Stock Disponible', 'UdM']
            for col, h in enumerate(int_headers):
                sheet.write(row, col, h, header_format)
            row += 1
            for inter in data['intermediates']:
                inter_name = f"[{inter['default_code']}] {inter['name']}" if inter.get('default_code') else inter['name']
                sheet.write(row, 0, inter_name, text_format)
                sheet.write(row, 1, inter['qty_required'], number_format)
                sheet.write(row, 2, inter['available_qty'], number_format)
                sheet.write(row, 3, inter.get('uom', ''), text_format)
                row += 1

        workbook.close()
        output.seek(0)

        # Retornar archivo como base64
        file_data = base64.b64encode(output.read()).decode('utf-8')
        filename = f'pronostico_{data["product"]["name"].replace(" ", "_")}.xlsx'

        return {
            'file_data': file_data,
            'filename': filename,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }

    @api.model
    def create_manufacturing_order(self, product_id, bom_id, qty):
        """
        Crea una Orden de Manufactura y retorna la acción para navegarla.

        :param product_id: ID del product.product
        :param bom_id: ID de la mrp.bom
        :param qty: cantidad a fabricar
        :return: dict de acción para el frontend
        """
        if qty <= 0:
            raise UserError(_('La cantidad a fabricar debe ser mayor a 0.'))

        product = self.env['product.product'].browse(product_id)
        bom = self.env['mrp.bom'].browse(bom_id)

        if not product.exists() or not bom.exists():
            raise UserError(_('Producto o BoM no encontrados.'))

        # Crear la orden de manufactura
        mo = self.env['mrp.production'].create({
            'product_id': product.id,
            'bom_id': bom.id,
            'product_qty': qty,
            'product_uom_id': product.uom_id.id,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.production',
            'res_id': mo.id,
            'views': [[False, 'form']],
            'target': 'current',
            'name': _('Orden de Manufactura'),
        }

    # -------------------------------------------------------------------------
    # MÉTODOS PRIVADOS — EXPLOSIÓN RECURSIVA
    # -------------------------------------------------------------------------

    def _explode_bom_recursive(self, bom, product, qty_per_unit, mode,
                                visited=None, depth=0, log_lines=None):
        """
        Explota una BoM recursivamente hasta llegar a los componentes base
        (productos sin BoM propia). Busca TODAS las BoMs activas tipo 'normal',
        no solo las tipo 'phantom' como hace el explode() estándar.

        :param bom: mrp.bom recordset
        :param product: product.product del producto padre
        :param qty_per_unit: cantidad requerida por unidad del producto raíz
        :param mode: 'on_hand' o 'forecasted'
        :param visited: set de product_ids visitados (anti-ciclos)
        :param depth: profundidad actual de recursión
        :param log_lines: lista para acumular líneas de log
        :return: (components_dict, intermediates_list, log_lines)
                 components_dict = {product_id: {'product': record, 'qty': float, ...}}
                 intermediates_list = [{'product': record, 'bom': record, 'qty': float}]
        """
        if visited is None:
            visited = set()
        if log_lines is None:
            log_lines = []

        # Protección anti-ciclos
        MAX_DEPTH = 20
        if depth > MAX_DEPTH:
            _logger.warning(
                'Profundidad máxima de recursión (%d) alcanzada para producto %s',
                MAX_DEPTH, product.display_name
            )
            log_lines.append({
                'action': 'max_depth',
                'product_id': product.id,
                'product_name': product.name,
                'depth': depth,
            })
            return {}, [], log_lines

        if product.id in visited:
            _logger.warning(
                'Ciclo detectado en BoM: producto %s ya fue visitado',
                product.display_name
            )
            log_lines.append({
                'action': 'cycle',
                'product_id': product.id,
                'product_name': product.name,
                'depth': depth,
            })
            return {}, [], log_lines

        visited.add(product.id)
        indent = '  ' * depth
        log_lines.append({
            'action': 'processing',
            'product_id': product.id,
            'product_name': product.name,
            'bom_name': bom.display_name,
            'qty': round(qty_per_unit, 4),
            'uom': product.uom_id.name,
            'depth': depth,
        })

        components = {}  # {product_id: {'product': record, 'qty_required': float, 'uom': record}}
        intermediates = []

        for line in bom.bom_line_ids:
            component = line.product_id
            # Cantidad de este componente por unidad del producto padre
            line_qty = (line.product_qty / bom.product_qty) * qty_per_unit

            # Buscar si este componente tiene su propia BoM activa
            sub_bom_dict = self.env['mrp.bom']._bom_find(component)
            sub_bom = sub_bom_dict.get(component, self.env['mrp.bom'])

            if sub_bom and component.id not in visited:
                # Este componente es un producto intermedio — registrarlo
                intermediates.append({
                    'product': component,
                    'bom': sub_bom,
                    'qty_required': line_qty,
                })

                log_lines.append({
                    'action': 'intermediate',
                    'product_id': component.id,
                    'product_name': component.name,
                    'qty': round(line_qty, 4),
                    'uom': component.uom_id.name,
                    'depth': depth + 1,
                })

                # Explotar recursivamente
                sub_components, sub_intermediates, log_lines = \
                    self._explode_bom_recursive(
                        sub_bom, component, line_qty, mode,
                        visited=visited, depth=depth + 1,
                        log_lines=log_lines
                    )

                # Consolidar componentes base del sub-nivel
                for pid, comp_data in sub_components.items():
                    if pid in components:
                        components[pid]['qty_required'] += comp_data['qty_required']
                    else:
                        components[pid] = comp_data.copy()

                # Acumular intermedios
                intermediates.extend(sub_intermediates)

            else:
                # Componente base (sin BoM propia) — agregar al resultado
                log_lines.append({
                    'action': 'base',
                    'product_id': component.id,
                    'product_name': component.name,
                    'qty': round(line_qty, 4),
                    'uom': component.uom_id.name,
                    'depth': depth + 1,
                })

                if component.id in components:
                    components[component.id]['qty_required'] += line_qty
                else:
                    components[component.id] = {
                        'product': component,
                        'qty_required': line_qty,
                        'uom': component.uom_id,
                    }

        # Remover el producto actual del visited para permitir que el mismo
        # componente aparezca en ramas diferentes del árbol
        visited.discard(product.id)

        return components, intermediates, log_lines

    # -------------------------------------------------------------------------
    # MÉTODOS PRIVADOS — CÁLCULOS
    # -------------------------------------------------------------------------

    def _calculate_producible_quantities(self, components, mode):
        """
        Para cada componente base, calcula cuántas unidades del producto final
        se pueden fabricar con el stock disponible.

        :param components: dict de componentes del _explode_bom_recursive
        :param mode: 'on_hand' o 'forecasted'
        :return: lista de dicts con datos de cada componente
        """
        result = []
        stock_field = 'qty_available' if mode == 'on_hand' else 'virtual_available'

        # Prefetch de los productos para optimizar queries
        product_ids = [comp_data['product'].id for comp_data in components.values()]
        products = self.env['product.product'].browse(product_ids)
        # Forzar lectura del campo de stock para todos de una vez
        products.mapped(stock_field)

        for pid, comp_data in components.items():
            product = comp_data['product']
            qty_required = comp_data['qty_required']
            available = getattr(product, stock_field, 0.0)

            # Calcular unidades producibles con este componente
            if qty_required > 0:
                producible = math.floor(max(available, 0) / qty_required)
            else:
                producible = float('inf')

            result.append({
                'id': product.id,
                'name': product.name,
                'default_code': product.default_code or '',
                'qty_required': round(qty_required, 4),
                'available_qty': round(available, 4),
                'producible_qty': producible if producible != float('inf') else 999999,
                'uom': comp_data['uom'].name,
                'status': 'green',  # Se asignará después
            })

        # Ordenar por producible_qty (limitantes primero)
        result.sort(key=lambda x: x['producible_qty'])
        return result

    def _calculate_max_producible(self, component_data):
        """
        Calcula la cantidad máxima fabricable = mínimo de todas las producible_qty.
        """
        if not component_data:
            return 0

        quantities = [c['producible_qty'] for c in component_data]
        return min(quantities) if quantities else 0

    def _assign_status_indicators(self, component_data, max_producible):
        """
        Asigna indicadores semáforo a cada componente:
        - 🔴 red: componente limitante (producible_qty == max_producible)
        - 🟡 yellow: producible_qty <= 2 * max_producible
        - 🟢 green: suficiente material

        :param component_data: lista de dicts de componentes
        :param max_producible: cantidad máxima fabricable
        :return: component_data con status actualizado
        """
        for comp in component_data:
            producible = comp['producible_qty']
            if max_producible == 0:
                # Sin producción posible, todo es rojo si no hay stock
                comp['status'] = 'red' if producible == 0 else 'yellow'
            elif producible == max_producible:
                comp['status'] = 'red'
            elif producible <= max_producible * 2:
                comp['status'] = 'yellow'
            else:
                comp['status'] = 'green'

        return component_data

    def _prepare_intermediate_data(self, intermediates, mode):
        """
        Prepara datos de productos intermedios para mostrar en la UI.
        """
        stock_field = 'qty_available' if mode == 'on_hand' else 'virtual_available'
        result = []
        seen = set()

        for inter in intermediates:
            product = inter['product']
            if product.id in seen:
                continue
            seen.add(product.id)

            available = getattr(product, stock_field, 0.0)
            result.append({
                'id': product.id,
                'name': product.name,
                'default_code': product.default_code or '',
                'bom_name': inter['bom'].display_name,
                'qty_required': round(inter['qty_required'], 4),
                'available_qty': round(available, 4),
                'uom': product.uom_id.name,
            })

        return result

    def _get_limiting_component(self, component_data):
        """Retorna el componente con menor producible_qty (el limitante)."""
        if not component_data:
            return None
        limiting = min(component_data, key=lambda c: c['producible_qty'])
        return {
            'id': limiting['id'],
            'name': limiting['name'],
            'producible_qty': limiting['producible_qty'],
        }

    def _generate_computation_log(self, product, bom, mode, max_producible,
                                   component_data, log_lines):
        """
        Genera un texto de log detallado del cálculo realizado.
        """
        mode_label = 'Stock En Mano' if mode == 'on_hand' else 'Stock Pronosticado'
        lines = [
            f'═══ Pronóstico de Producción ═══',
            f'Producto: {product.display_name}',
            f'BoM: {bom.display_name}',
            f'Modo: {mode_label}',
            f'Resultado: {max_producible} unidades fabricables',
            f'',
            f'─── Árbol de explosión ───',
        ]
        for L in log_lines:
            indent = '  ' * L.get('depth', 0)
            if L['action'] == 'processing':
                lines.append(f"{indent}📦 Procesando: {L['product_name']} (BoM: {L.get('bom_name', '')}, qty: {L['qty']:.2f})")
            elif L['action'] == 'intermediate':
                lines.append(f"{indent}  🔄 {L['product_name']} tiene BoM → explosión recursiva (qty: {L['qty']:.2f})")
            elif L['action'] == 'base':
                lines.append(f"{indent}  ✅ {L['product_name']} → componente base (qty: {L['qty']:.2f} {L['uom']})")
            elif L['action'] == 'max_depth':
                lines.append(f"⚠️ Profundidad máxima alcanzada para {L['product_name']}")
            elif L['action'] == 'cycle':
                lines.append(f"⚠️ Ciclo detectado: {L['product_name']} ya fue procesado")
        lines.append('')
        lines.append('─── Resultado por componente ───')
        for comp in component_data:
            lines.append(
                f'  {comp["name"]}: '
                f'necesita {comp["qty_required"]:.2f}/{comp["uom"]}, '
                f'disponible {comp["available_qty"]:.2f}, '
                f'produce {comp["producible_qty"]} ud. '
                f'[{comp["status"].upper()}]'
            )

        return '\n'.join(lines)

    def _build_structured_log_entries(self, product, bom, mode,
                                      max_producible, component_data,
                                      log_lines):
        """
        Construye una lista de entradas estructuradas para renderizar
        el log de cálculo en la UI como un árbol interactivo.

        Cada entrada es un dict con:
          type: 'header' | 'explosion' | 'component_result' | 'warning'
          text: str
          detail: str (opcional)
          indent: int
          icon: str (clase FontAwesome)
          color: str (css class)
          status: str ('green'/'yellow'/'red', solo component_result)
        """
        mode_label = 'Stock En Mano' if mode == 'on_hand' else 'Stock Pronosticado'
        entries = []

        # Procesar líneas del árbol de explosión (sin header ni nodo raíz redundante)
        for L in log_lines:
            if L['action'] == 'processing':
                # Omitir el nodo raíz (depth 0), ya que el producto final
                # ya se muestra en la tarjeta de resultado a la derecha
                if L['depth'] == 0:
                    continue
                entries.append({
                    'type': 'explosion',
                    'action': 'processing',
                    'text': L['product_name'],
                    'qty_text': f"Produciendo {L['qty']:.2f} ud",
                    'indent': L['depth'] - 1,
                    'icon': 'fa-cube',
                    'color': 'primary',
                    'product_id': L['product_id'],
                })
            elif L['action'] == 'intermediate':
                entries.append({
                    'type': 'explosion',
                    'action': 'intermediate',
                    'text': L['product_name'],
                    'qty_text': f"Requiere {L['qty']:.2f} {L['uom']}",
                    'indent': max(L['depth'] - 1, 0),
                    'icon': 'fa-sitemap',
                    'color': 'info',
                    'product_id': L['product_id'],
                })
            elif L['action'] == 'base':
                entries.append({
                    'type': 'explosion',
                    'action': 'base',
                    'text': L['product_name'],
                    'qty_text': f"Requiere {L['qty']:.2f} {L['uom']}",
                    'indent': max(L['depth'] - 1, 0),
                    'icon': 'fa-check',
                    'color': 'success',
                    'product_id': L['product_id'],
                })
            elif L['action'] in ('max_depth', 'cycle'):
                entries.append({
                    'type': 'warning',
                    'text': f"Error: {L['product_name']}",
                    'detail': 'Ciclo o profundidad máxima en BoM',
                    'indent': L['depth'],
                    'icon': 'fa-exclamation-triangle',
                    'color': 'danger',
                    'product_id': L['product_id'],
                })

        # Resultado por componente
        for comp in component_data:
            status_labels = {'green': 'Suficiente', 'yellow': 'Justo', 'red': 'Limitante'}
            entries.append({
                'type': 'component_result',
                'text': comp['name'],
                'detail': (
                    f'Necesita {comp["qty_required"]:.2f} {comp["uom"]}/ud  •  '
                    f'Disponible: {comp["available_qty"]:.2f}  •  '
                    f'Produce: {comp["producible_qty"]} uds'
                ),
                'indent': 0,
                'icon': 'fa-cog',
                'color': comp.get('status', 'green'),
                'status': comp.get('status', 'green'),
                'status_label': status_labels.get(comp.get('status', ''), ''),
                'product_id': comp['id'],
            })

        return entries

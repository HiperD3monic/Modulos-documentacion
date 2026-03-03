# -*- coding: utf-8 -*-
# =============================================================================
# Script: Limpiar texto "Fecha de Pedimento" de líneas de factura
# =============================================================================
# Ejecutar en el shell de Odoo:
#   python odoo-bin shell -d <nombre_db> -c <config_file>
#   >>> exec(open('extra_addons/v18-main/pedimento_gestion/scripts/clean_invoice_pedimento_text.py').read())
#
# O desde la interfaz de Odoo usando el módulo "base_automation" o similar.
# =============================================================================

import re

# Patrón para encontrar las líneas de "Fecha de Pedimento: ..."
# Busca: \nFecha de Pedimento: <cualquier texto hasta el fin de línea>
PATTERN = re.compile(r'\n?Fecha de Pedimento:[^\n]*', re.IGNORECASE)

# Buscar todas las líneas de factura que contengan el texto
lines = env['account.move.line'].search([
    ('name', 'ilike', 'Fecha de Pedimento'),
])

print(f"Líneas encontradas con 'Fecha de Pedimento': {len(lines)}")

count = 0
for line in lines:
    old_name = line.name or ''
    new_name = PATTERN.sub('', old_name).strip()

    if new_name != old_name:
        # Usar SQL directo para evitar constrains y recomputes innecesarios
        env.cr.execute(
            "UPDATE account_move_line SET name = %s WHERE id = %s",
            (new_name, line.id)
        )
        count += 1
        print(f"  ✓ Línea {line.id} (Factura: {line.move_id.name}) — limpiada")

env.cr.commit()
env.invalidate_all()

print(f"\n{'='*50}")
print(f"Total líneas actualizadas: {count}")
print(f"{'='*50}")

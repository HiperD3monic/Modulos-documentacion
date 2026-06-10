reporte = []

# ═══════════════════════════════════════════════════════════════════════════════
# V6 — BUSCAR RECEPCIONES ORIGINALES DEL EXCHANGE
# Para cada exchange delivery (Y01/OUT/...) encontrar su receipt hermano
# ═══════════════════════════════════════════════════════════════════════════════

# Los exchange deliveries que ya confirmamos como originales:
exchange_out_names = ['Y01/OUT/00002', 'Y01/OUT/00003', 'Y01/OUT/00006', 'R01/OUT/00007']

for out_name in exchange_out_names:
    env.cr.execute("""
        SELECT sp.id, sp.name, sp.origin, sp.create_date, sp.state,
               sp.note, sp.picking_type_id
        FROM stock_picking sp
        WHERE sp.name = %s
        LIMIT 1
    """, [out_name])
    out_row = env.cr.fetchone()
    if not out_row:
        reporte.append("⚠️  %s: NO ENCONTRADO" % out_name)
        continue

    out_id, out_name_db, out_origin, out_date, out_state, out_note, out_pt = out_row

    # Productos del exchange delivery
    env.cr.execute("""
        SELECT pp.default_code, pt.name, sm.product_uom_qty
        FROM stock_move sm
        JOIN product_product pp ON pp.id = sm.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE sm.picking_id = %s
        ORDER BY pp.default_code
    """, [out_id])
    out_products = env.cr.fetchall()

    reporte.append("╔══════════════════════════════════════════════════════════════")
    reporte.append("║ EXCHANGE DELIVERY (ORIGINAL): %s" % out_name_db)
    reporte.append("║ Origin: %s  |  Fecha: %s  |  Estado: %s" % (out_origin, str(out_date)[:19], out_state))
    reporte.append("║ Productos NUEVOS (salida):")
    for code, name, qty in out_products:
        n = name if not isinstance(name, dict) else name.get('es_MX', name.get('en_US', str(name)))
        reporte.append("║   [%s] %s (x%s)" % (code or '?', n, int(qty)))

    # Buscar el receipt hermano usando la nota del delivery
    receipt_name_from_note = ''
    if out_note:
        note_str = str(out_note)
        marker = 'EXCHANGE_RETURN:'
        idx = note_str.find(marker)
        if idx >= 0:
            rest = note_str[idx + len(marker):]
            # Cortar en espacio, < o fin de línea
            end = len(rest)
            for ch in [' ', '<', '\n', '\r', '\t']:
                pos = rest.find(ch)
                if pos >= 0 and pos < end:
                    end = pos
            receipt_name_from_note = rest[:end].strip()

    # Buscar receipt por nombre encontrado en nota
    if receipt_name_from_note:
        env.cr.execute("""
            SELECT sp.id, sp.name, sp.origin, sp.create_date, sp.state,
                   sp.location_id, sp.location_dest_id
            FROM stock_picking sp
            WHERE sp.name = %s
            LIMIT 1
        """, [receipt_name_from_note])
        in_row = env.cr.fetchone()
        if in_row:
            in_id = in_row[0]
            env.cr.execute("""
                SELECT pp.default_code, pt.name, sm.product_uom_qty
                FROM stock_move sm
                JOIN product_product pp ON pp.id = sm.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE sm.picking_id = %s
                ORDER BY pp.default_code
            """, [in_id])
            in_products = env.cr.fetchall()

            reporte.append("║")
            reporte.append("║ EXCHANGE RECEIPT (ORIGINAL): %s" % in_row[1])
            reporte.append("║ Origin: %s  |  Fecha: %s  |  Estado: %s  |  ID: %s" % (
                in_row[2], str(in_row[3])[:19], in_row[4], in_row[0]))
            reporte.append("║ Productos DEVUELTOS (entrada):")
            for code, name, qty in in_products:
                n = name if not isinstance(name, dict) else name.get('es_MX', name.get('en_US', str(name)))
                reporte.append("║   [%s] %s (x%s)" % (code or '?', n, int(qty)))
        else:
            reporte.append("║ ⚠️  Receipt nombre '%s' (de nota) NO encontrado en DB" % receipt_name_from_note)
    else:
        reporte.append("║ ⚠️  No se encontró EXCHANGE_RETURN en la nota del delivery")
        # Fallback: buscar por origin y fecha similar
        env.cr.execute("""
            SELECT sp.id, sp.name, sp.origin, sp.create_date, sp.state
            FROM stock_picking sp
            JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
            WHERE spt.code = 'incoming'
              AND sp.origin = %s
              AND ABS(EXTRACT(EPOCH FROM (sp.create_date - %s))) < 60
            ORDER BY sp.create_date
        """, [out_origin, out_date])
        fallback_rows = env.cr.fetchall()
        if fallback_rows:
            for fr in fallback_rows:
                env.cr.execute("""
                    SELECT pp.default_code, pt.name, sm.product_uom_qty
                    FROM stock_move sm
                    JOIN product_product pp ON pp.id = sm.product_id
                    JOIN product_template pt ON pt.id = pp.product_tmpl_id
                    WHERE sm.picking_id = %s
                """, [fr[0]])
                fb_prods = env.cr.fetchall()
                reporte.append("║")
                reporte.append("║ RECEIPT CANDIDATO (por origin+fecha): %s (ID:%s)" % (fr[1], fr[0]))
                reporte.append("║ Origin: %s  |  Fecha: %s  |  Estado: %s" % (fr[2], str(fr[3])[:19], fr[4]))
                for code, name, qty in fb_prods:
                    n = name if not isinstance(name, dict) else name.get('es_MX', name.get('en_US', str(name)))
                    reporte.append("║   [%s] %s (x%s)" % (code or '?', n, int(qty)))
        else:
            reporte.append("║ ❌ No se encontró receipt con origin='%s' cerca de %s" % (out_origin, str(out_date)[:19]))

    reporte.append("╚══════════════════════════════════════════════════════════════")
    reporte.append("")

# Ahora mostrar los duplicados POS para comparar
reporte.append("")
reporte.append("═══ PICKINGS DUPLICADOS DEL POS (para comparar) ═══")
dup_ids = [4132, 4133, 4136, 4137, 4220, 4221, 4235, 4236]
for did in dup_ids:
    env.cr.execute("""
        SELECT sp.name, sp.origin, sp.create_date, sp.state,
               spt.code, sl_from.complete_name, sl_to.complete_name
        FROM stock_picking sp
        JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
        JOIN stock_location sl_from ON sl_from.id = sp.location_id
        JOIN stock_location sl_to ON sl_to.id = sp.location_dest_id
        WHERE sp.id = %s
    """, [did])
    dr = env.cr.fetchone()
    if not dr:
        reporte.append("ID %s: NO ENCONTRADO" % did)
        continue

    env.cr.execute("""
        SELECT pp.default_code, pt.name, sm.product_uom_qty
        FROM stock_move sm
        JOIN product_product pp ON pp.id = sm.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE sm.picking_id = %s
        ORDER BY pp.default_code
    """, [did])
    d_prods = env.cr.fetchall()

    reporte.append("❌ %s (ID:%s) | %s | %s → %s | %s" % (
        dr[0], did, dr[1], dr[5], dr[6], dr[4]))
    for code, name, qty in d_prods:
        n = name if not isinstance(name, dict) else name.get('es_MX', name.get('en_US', str(name)))
        reporte.append("   [%s] %s (x%s)" % (code or '?', n, int(qty)))
    reporte.append("")

raise UserError('\n'.join(reporte))

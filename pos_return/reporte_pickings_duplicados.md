# Reporte de Auditoría: Pickings Duplicados por Bug en Módulo `pos_return`

**Fecha del reporte:** 1 de mayo de 2026
**Ambiente auditado:** torostj-main (Base de datos de pruebas)
**Módulo afectado:** `pos_return` (Intercambios POS)
**Criticidad:** Media — Afecta inventario pero no facturación

---

## 1. Descripción del Bug

### ¿Qué ocurrió?
El módulo de intercambios del Punto de Venta permite que un cliente devuelva un producto y reciba uno nuevo, pagando la diferencia de precio si aplica. Al realizar este proceso, el sistema:

1. **Registra los movimientos de inventario del intercambio:**
   - **Recepción:** registra la entrada del producto devuelto al almacén
   - **Entrega:** registra la salida del producto nuevo al cliente

2. Si el cliente debe **pagar una diferencia**, el sistema genera una **orden de pago en caja** para cobrar el monto restante.

3. Al procesar esta orden de pago, el sistema **volvía a crear movimientos de inventario** para los mismos productos, duplicando los que ya se habían registrado en el paso 1.

### El problema
Los movimientos de inventario se registraron **dos veces**, causando:
- Los productos nuevos salieron del inventario 2 veces (stock descontado doble)
- Los productos devueltos entraron al inventario 2 veces (stock incrementado doble)

### ¿Cuándo se corrigió?
Se implementó una corrección en el módulo que evita la creación de movimientos de inventario duplicados cuando la orden proviene de un intercambio. Las 4 órdenes afectadas fueron creadas antes de que esta corrección estuviera activa.

---

## 2. Alcance del Impacto

| Métrica | Valor |
|---|---|
| Total de órdenes de intercambio con pago | **21** |
| Órdenes afectadas por el bug | **4** |
| Órdenes protegidas por el fix | **17** |
| Pickings duplicados detectados | **8** |
| Estado de los pickings duplicados | **Todos en `done`** |
| Período afectado | **21 al 23 de abril de 2026** |
| Tiendas afectadas | Caja Alameda, Caja Río 2.0 |

---

## 3. Detalle de las 4 Transacciones Afectadas

### 3.1 — Caja Alameda - 000023 (21 abril 2026, 22:38)
**Sesión:** Caja Alameda/00489

**Productos nuevos (salida):**
| Código | Producto | Cantidad |
|---|---|---|
| 1005-78 | Gorra 2025 Game Cap Olivo 5950 | 1 |
| 6023-89 | Calcetines Marca Toros / Torín | 1 |
| 6023-95 | Calcetines Marca Toros / Isotipo Mini | 1 |

**Producto devuelto (entrada):**
| Código | Producto | Cantidad |
|---|---|---|
| 1005-235 | Gorra 5950 Cápsula JDE Rojo 2025 | 1 |

**Pickings originales (correctos, NO tocar):**

| Referencia | Tipo | Origin | Fecha | ID |
|---|---|---|---|---|
| Y01/OUT/00002 | Entrega | INT:05 | 22:37:56 | — |
| Y01/IN/00003 | Recepción | 05 | 22:37:56 | 4130 |

**Pickings duplicados (DEBEN REVERTIRSE):**

| Referencia | Dirección | Origin | Fecha | ID |
|---|---|---|---|---|
| Tie/POS/00316 | Y01/Existencias → Customers | Caja Alameda - 000023 | 22:38:27 | **4132** |
| Tie/POS/00317 | Customers → Y01/Existencias | Caja Alameda - 000023 | 22:38:27 | **4133** |

---

### 3.2 — Caja Alameda - 000024 (21 abril 2026, 22:45)
**Sesión:** Caja Alameda/00489

**Productos nuevos (salida):**
| Código | Producto | Cantidad |
|---|---|---|
| 1001-23 | Jersey Stoli 2025 Blanca Institucional TJ, Hombre | 1 |
| 1001-28 | Jersey Stoli 2025 Blanca Institucional TJ, Mujer | 1 |

**Producto devuelto (entrada):**
| Código | Producto | Cantidad |
|---|---|---|
| 2002-26 | Jersey Arrieta 2026 Blanco, Hombre | 1 |

**Pickings originales (correctos, NO tocar):**

| Referencia | Tipo | Origin | Fecha | ID |
|---|---|---|---|---|
| Y01/OUT/00003 | Entrega | INT:CAMBIO DE TALLA | 22:45:02 | — |
| Y01/IN/00004 | Recepción | CAMBIO DE TALLA | 22:45:02 | 4134 |

**Pickings duplicados (DEBEN REVERTIRSE):**

| Referencia | Dirección | Origin | Fecha | ID |
|---|---|---|---|---|
| Tie/POS/00318 | Y01/Existencias → Customers | Caja Alameda - 000024 | 22:45:10 | **4136** |
| Tie/POS/00319 | Customers → Y01/Existencias | Caja Alameda - 000024 | 22:45:10 | **4137** |

---

### 3.3 — Caja Río 2.0 - 000015 (22 abril 2026, 19:44)
**Sesión:** Caja Río 2.0/00503

**Producto nuevo (salida):**
| Código | Producto | Cantidad |
|---|---|---|
| 2005-10 | Gorra 2026 Game TJ Institucional 3930 | 1 |

**Producto devuelto (entrada):**
| Código | Producto | Cantidad |
|---|---|---|
| 9005-168 | Gorra Rosa TJ Blanco 940 | 1 |

**Pickings originales (correctos, NO tocar):**

| Referencia | Tipo | Origin | Fecha | ID |
|---|---|---|---|---|
| R01/OUT/00007 | Entrega | INT:267-6-000004 | 19:44:42 | — |
| R01/IN/00009 | Recepción | 267-6-000004 | 19:44:42 | 4218 |

**Pickings duplicados (DEBEN REVERTIRSE):**

| Referencia | Dirección | Origin | Fecha | ID |
|---|---|---|---|---|
| Tie/POS/00669 | R01/Existencias → Customers | Caja Río 2.0 - 000015 | 19:44:48 | **4220** |
| Tie/POS/00670 | Customers → R01/Existencias | Caja Río 2.0 - 000015 | 19:44:48 | **4221** |

---

### 3.4 — Caja Alameda - 000032 (23 abril 2026, 00:17)
**Sesión:** Caja Alameda/00505

**Productos nuevos (salida):**
| Código | Producto | Cantidad |
|---|---|---|
| 6023-154 | Imán Jersey Platinum | 1 |
| 9001-79 | Jersey Stoli Mascotas Torín, Infantil | 1 |

**Producto devuelto (entrada):**
| Código | Producto | Cantidad |
|---|---|---|
| 2002-22 | Jersey Arrieta 2026 Rojo, Infantil | 1 |

**Pickings originales (correctos, NO tocar):**

| Referencia | Tipo | Origin | Fecha | ID |
|---|---|---|---|---|
| Y01/OUT/00006 | Entrega | INT:030 | 00:17:03 | — |
| Y01/IN/00008 | Recepción | 030 | 00:17:03 | 4233 |

**Pickings duplicados (DEBEN REVERTIRSE):**

| Referencia | Dirección | Origin | Fecha | ID |
|---|---|---|---|---|
| Tie/POS/00327 | Y01/Existencias → Customers | Caja Alameda - 000032 | 00:17:10 | **4235** |
| Tie/POS/00328 | Customers → Y01/Existencias | Caja Alameda - 000032 | 00:17:10 | **4236** |

---

## 4. Resumen de IDs a Revertir

```
IDs de pickings duplicados: [4132, 4133, 4136, 4137, 4220, 4221, 4235, 4236]
```

| ID | Referencia | Transacción | Tipo de movimiento |
|---|---|---|---|
| 4132 | Tie/POS/00316 | Alameda 000023 | Entrega (productos nuevos) |
| 4133 | Tie/POS/00317 | Alameda 000023 | Recepción (producto devuelto) |
| 4136 | Tie/POS/00318 | Alameda 000024 | Entrega (productos nuevos) |
| 4137 | Tie/POS/00319 | Alameda 000024 | Recepción (producto devuelto) |
| 4220 | Tie/POS/00669 | Río 2.0 000015 | Entrega (producto nuevo) |
| 4221 | Tie/POS/00670 | Río 2.0 000015 | Recepción (producto devuelto) |
| 4235 | Tie/POS/00327 | Alameda 000032 | Entrega (productos nuevos) |
| 4236 | Tie/POS/00328 | Alameda 000032 | Recepción (producto devuelto) |

---

## 5. Procedimiento de Corrección en Producción

> [!CAUTION]
> Antes de ejecutar estos pasos en producción, se recomienda realizar un respaldo completo de la base de datos.

### Paso 1 — Verificar cada picking duplicado

Para cada picking reportado como duplicado:

1. Abrir el picking duplicado según su tipo de movimiento:
   - Si es una **Entrega** (productos nuevos — dirección: Existencias → Customers): ir a **Inventario → Operaciones → Entregas**
   - Si es una **Recepción** (producto devuelto — dirección: Customers → Existencias): ir a **Inventario → Operaciones → Recepciones**
2. En la barra de búsqueda, escribir la **Referencia** del picking duplicado (ej: "Tie/POS/00316") o filtrar por el **Documento origen** (ej: "Caja Alameda - 000023")
3. Verificar que el **Documento origen** sea una orden POS de intercambio (ej: "Caja Alameda - 000023")
4. Abrir en otra pestaña el picking original del exchange (ej: "Y01/OUT/00002") buscándolo de la misma forma en **Entregas** o **Recepciones** según corresponda
5. **Confirmar** que ambos tienen los **mismos productos y cantidades**
6. Verificar que el picking duplicado fue creado **segundos después** del original

### Paso 2 — Revertir los pickings duplicados

Para cada picking duplicado confirmado:

1. Abrir el picking en el formulario de Inventario
2. Hacer clic en el botón **"Devolver"** (esquina superior izquierda)
3. En el popup de devolución:
   - Verificar que las cantidades sean correctas
   - Hacer clic en **"Devolver"**
4. Se creará un picking de devolución en estado "Listo"
5. Abrir el picking de devolución y hacer clic en **"Validar"**
6. Confirmar la validación

> [!IMPORTANT]
> Repetir el Paso 2 para **cada uno** de los 8 pickings duplicados.

### Paso 3 — Verificar el inventario

Después de revertir todos los pickings:

1. Ir a **Inventario → Reportes → Valoración de inventario**
2. Verificar que los productos afectados tengan cantidades coherentes
3. Si es necesario, realizar un **inventario físico** de los productos específicos

### Paso 4 — Confirmar que el fix previene futuros duplicados

1. Verificar que el módulo `pos_return` actualizado esté desplegado en producción
2. Confirmar que el campo `is_exchange_payment` existe en el modelo `pos.order`
3. Realizar un intercambio de prueba con pago de diferencia
4. Verificar que solo se cree 1 par de pickings (Y01/OUT + Y01/IN), **no** pickings adicionales Tie/POS

---

## 6. Productos Afectados por la Duplicación

Los siguientes productos tienen movimientos de inventario duplicados que deben corregirse:

| Código | Producto | Movimiento duplicado | Efecto en inventario |
|---|---|---|---|
| 1005-78 | Gorra 2025 Game Cap Olivo 5950 | Salida extra x1 | Stock subcontado (-1) |
| 6023-89 | Calcetines Marca Toros / Torín | Salida extra x1 | Stock subcontado (-1) |
| 6023-95 | Calcetines Marca Toros / Isotipo Mini | Salida extra x1 | Stock subcontado (-1) |
| 1005-235 | Gorra 5950 Cápsula JDE Rojo 2025 | Entrada extra x1 | Stock sobrecontado (+1) |
| 1001-23 | Jersey Stoli 2025 Blanca Institucional TJ, Hombre | Salida extra x1 | Stock subcontado (-1) |
| 1001-28 | Jersey Stoli 2025 Blanca Institucional TJ, Mujer | Salida extra x1 | Stock subcontado (-1) |
| 2002-26 | Jersey Arrieta 2026 Blanco, Hombre | Entrada extra x1 | Stock sobrecontado (+1) |
| 2005-10 | Gorra 2026 Game TJ Institucional 3930 | Salida extra x1 | Stock subcontado (-1) |
| 9005-168 | Gorra Rosa TJ Blanco 940 | Entrada extra x1 | Stock sobrecontado (+1) |
| 6023-154 | Imán Jersey Platinum | Salida extra x1 | Stock subcontado (-1) |
| 9001-79 | Jersey Stoli Mascotas Torín, Infantil | Salida extra x1 | Stock subcontado (-1) |
| 2002-22 | Jersey Arrieta 2026 Rojo, Infantil | Entrada extra x1 | Stock sobrecontado (+1) |


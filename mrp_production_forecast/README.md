# Pronóstico de Producción (Manufacturing Production Forecast)

## Descripción

Módulo para Odoo 18 que calcula la cantidad máxima de productos finales
fabricables basándose en la disponibilidad real o pronosticada de materiales,
con explosión recursiva completa de Listas de Materiales (BoM) multinivel.

## Características

- **Explosión multinivel recursiva**: Recorre toda la estructura de BoM
  hasta los componentes base, consolidando cantidades.
- **Dos modos de cálculo**:
  - *Stock En Mano*: solo materiales físicamente disponibles (`qty_available`)
  - *Stock Pronosticado*: incluye recepciones pendientes (`virtual_available`)
- **Indicadores semáforo**: Verde (suficiente), Amarillo (justo), Rojo (limitante)
- **Productos intermedios**: Muestra sub-ensambles con stock disponible
- **Exportar a Excel**: Genera archivo `.xlsx` con el detalle completo
- **Crear Orden de Manufactura**: Wizard prellenado con la cantidad calculada
- **Auditoría**: Registro de quién calculó, cuándo y con qué resultado

## Instalación

1. Copiar la carpeta `mrp_production_forecast` en el directorio de addons
2. Reiniciar el servidor Odoo
3. Activar modo desarrollador
4. Ir a **Apps** → **Actualizar lista de módulos**
5. Buscar "Pronóstico de Producción" e instalar

## Dependencias

- `base`, `mrp`, `stock`, `product` (módulos estándar de Odoo 18)
- `xlsxwriter` (incluido en Odoo 18) para exportación Excel

## Uso

1. Ir a **Manufactura → Reportes → Pronóstico de Producción**
2. Crear un nuevo pronóstico
3. Seleccionar un producto con BoM activa
4. Elegir modo (Stock En Mano / Pronosticado)
5. Hacer clic en **Calcular Pronóstico**
6. Revisar componentes base, indicadores y cantidad máxima fabricable
7. Opcionalmente: exportar a Excel o crear orden de manufactura

## Permisos

| Rol | Permisos |
|-----|----------|
| Manufacturing User | Crear, leer y editar pronósticos |
| Manufacturing Manager | Todos los anteriores + eliminar + ver logs de auditoría |

## Troubleshooting

| Problema | Solución |
|----------|----------|
| No aparece el menú | Verificar que el usuario tiene grupo `Manufacturing/User` |
| "No se encontró BoM" | Verificar que el producto tiene una BoM activa tipo "Manufacture" |
| Ciclo detectado en BoM | Revisar la estructura de BoM, hay componentes que se referencian mutuamente |
| Error en exportación Excel | Verificar que `xlsxwriter` está instalado en el entorno Python |

## Licencia

LGPL-3

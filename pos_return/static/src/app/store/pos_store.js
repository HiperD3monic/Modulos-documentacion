/**
 * =============================================================================
 * MÓDULO: pos_return
 * ARCHIVO: static/src/app/store/pos_store.js
 * DESCRIPCIÓN: Patch al store principal del POS
 * MIGRADO: Odoo 18 -> Odoo 19
 * FECHA: 2026-02-01
 * =============================================================================
 * 
 * NOTAS DE MIGRACIÓN:
 * - Se usa patch() de @web/core/utils/patch para extender funcionalidad
 * - Se extiende PosStore.prototype para añadir métods globales del POS
 * - this.dialog.add() es la forma estándar de abrir popups en v18/v19
 */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ReturnPopup } from "@pos_return/app/return_popup/return_popup";

patch(PosStore.prototype, {
    /**
     * Abre el popup de devoluciones
     * 
     * Este método es llamado desde el botón en control_buttons.xml
     */
    async openReturn() {
        this.dialog.add(ReturnPopup, {});
    },
});

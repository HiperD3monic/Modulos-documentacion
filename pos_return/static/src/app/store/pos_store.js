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
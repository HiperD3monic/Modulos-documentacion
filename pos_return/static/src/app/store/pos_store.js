import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ReturnPopup } from "@pos_return/app/return_popup/return_popup";
import { ExchangePopup } from "@pos_return/app/exchange_popup/exchange_popup";

patch(PosStore.prototype, {
    /**
     * Abre el popup de devoluciones
     */
    async openReturn() {
        this.dialog.add(ReturnPopup, {});
    },

    /**
     * Abre el popup de intercambio de productos
     */
    async openExchange() {
        this.dialog.add(ExchangePopup, {});
    },
});
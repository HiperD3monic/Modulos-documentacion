/** @odoo-module **/
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { patch } from "@web/core/utils/patch";

patch(OrderReceipt.prototype, {
    /**
     * Returns the list of products still in the customer's possession
     * after a partial return. Data is set by return_popup.js on confirm().
     * @returns {Array<{name: string, qty: number}>}
     */
    get possessionItems() {
        return this.order._possessionItems || [];
    },
});

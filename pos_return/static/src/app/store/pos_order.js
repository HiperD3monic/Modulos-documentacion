/** @odoo-module **/
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(PosOrder.prototype, {
    /**
     * Override getName to show "(Reembolso)" for partial returns too.
     * Native Odoo only shows it when is_refund=True, but our partial returns
     * keep is_refund=False for searchability. We detect them by amount_total < 0.
     */
    getName() {
        let name = this.floatingOrderName || "";
        if (this.isRefund) {
            name += _t(" (Refund)");
        } else if (this.amount_total < 0) {
            // Partial return: is_refund=False but total is negative
            name += _t(" (Reembolso)");
        }
        return name;
    },
});

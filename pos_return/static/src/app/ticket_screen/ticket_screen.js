/** @odoo-module **/
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(TicketScreen.prototype, {
    /**
     * Returns an array of label/type indicators for the order.
     * An order can have multiple badges (e.g. "Pago Intercambio" + "Devuelto").
     */
    getOrderTypeLabels(order) {
        const raw = order.raw || {};
        const labels = [];

        if (order.is_exchange_payment || raw.is_exchange_payment) {
            labels.push({ text: _t("Pago Intercambio"), color: "warning" });
        }
        if (order.custom_exchange_done || raw.custom_exchange_done) {
            labels.push({ text: _t("Intercambiado"), color: "warning" });
        }
        if (order.custom_return_done || raw.custom_return_done) {
            labels.push({ text: _t("Devuelto"), color: "info" });
        }

        return labels;
    },

    /**
     * Override: Block keyboard input from modifying refund quantities.
     * The numberBuffer service captures keyboard input and calls this method.
     * By making it a no-op, typing numbers on the keyboard does nothing
     * in the TicketScreen, but the numberBuffer lifecycle is preserved
     * so other screens (ProductScreen) continue working normally.
     */
    _onUpdateSelectedOrderline() {
        // Blocked: refunds are handled exclusively by the pos_return module.
        return;
    },

    /**
     * Override: Block clicking on order lines from setting refund quantities.
     */
    onClickOrderline(orderline) {
        // Only allow selecting the line visually (for details/print),
        // but do NOT interact with the numberBuffer for refund quantities.
        if (this.getSelectedOrder()?.finalized) {
            const order = this.getSelectedOrder();
            this.state.selectedOrderlineIds[order.id] = orderline.id;
        }
    },

    /**
     * Override: Completely block native refund. The pos_return module
     * handles all return and exchange operations.
     */
    async onDoRefund() {
        this.env.services.notification.add(
            _t("Los reembolsos nativos están deshabilitados. Use el módulo de Devoluciones."),
            { type: "danger" }
        );
        return;
    },
});

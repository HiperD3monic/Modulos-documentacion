/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ReturnPopup } from "@pos_return/app/return_popup/return_popup";
import { ExchangePopup } from "@pos_return/app/exchange_popup/exchange_popup";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";

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

    /**
     * Override selectPartner to add a warning when assigning a customer
     * to a refund order that was created without one.
     *
     * When a ticket has no customer and pos_return creates a refund,
     * the refund order also has no customer. If the user tries to assign
     * one from the payment screen, we warn them first.
     */
    async selectPartner(currentOrder = this.getOrder()) {
        if (!currentOrder) {
            return false;
        }

        const currentPartner = currentOrder.getPartner();
        const isRefundOrder = currentOrder.isRefund || currentOrder.amount_total < 0;

        // If the order is a refund and has NO partner, warn the user
        // before allowing them to assign one.
        if (isRefundOrder && !currentPartner) {
            const confirmed = await makeAwaitable(this.dialog, ConfirmationDialog, {
                title: _t("Asignar cliente a reembolso"),
                body: _t(
                    "Esta orden de reembolso fue creada a partir de un ticket sin cliente. " +
                    "¿Está seguro de que desea asignar un cliente? " +
                    "Esto vinculará el reembolso a la cuenta del cliente seleccionado."
                ),
                confirmLabel: _t("Sí, asignar cliente"),
                cancelLabel: _t("Cancelar"),
            });

            if (!confirmed) {
                return currentPartner;
            }
        }

        // Proceed with the standard partner selection
        return await super.selectPartner(currentOrder);
    },
});
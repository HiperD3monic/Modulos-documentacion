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

        if (order.custom_exchange_done || raw.custom_exchange_done) {
            labels.push({ text: _t("Intercambiado"), color: "warning" });
        }
        if (order.custom_return_done || raw.custom_return_done) {
            labels.push({ text: _t("Devuelto"), color: "info" });
        }

        // Detect partial returns: order has refund links but is NOT fully returned
        if (!labels.length && !(order.custom_return_done || raw.custom_return_done)) {
            const orderLines = order.lines || [];
            let hasPartialRefund = false;
            for (const line of orderLines) {
                const refundLines = line.refund_orderline_ids || line.raw?.refund_orderline_ids || [];
                if (refundLines.length > 0) {
                    hasPartialRefund = true;
                    break;
                }
            }
            if (hasPartialRefund) {
                labels.push({ text: _t("Parcial"), color: "warning" });
            }
        }

        // For refund orders (negative total) without other flags,
        // show specific type badge only for non-standard returns
        const amount = order.amount_total ?? raw.amount_total ?? 0;
        if (amount < 0 && !labels.length) {
            // Parse internal_note to determine the return type
            const note = order.internal_note || raw.internal_note || "";
            if (note.includes("Sin Ticket")) {
                labels.push({ text: _t("Sin Ticket"), color: "dark" });
            } else if (note.includes("Arus")) {
                labels.push({ text: _t("Arus"), color: "secondary" });
            }
            // Normal Odoo refunds: no badge (already shows "(Reembolso)" in the name)
        }

        return labels;
    },

    /**
     * Returns parsed remaining products from the selected order's internal_note.
     * internal_note is a JSON array of tag objects [{text, colorIndex}, ...].
     * We look for a tag whose text starts with the possession marker.
     * Returns an array of {name, qty} objects, or empty array if none.
     */
    getRemainingProducts(order) {
        if (!order) return [];
        const raw = order.raw || {};
        const noteRaw = order.internal_note || raw.internal_note || "";
        
        // internal_note is JSON — find the possession tag
        const markers = [
            "Productos en posesión del cliente:",
            "📦 Productos en posesión del cliente:",
        ];
        
        let possessionText = "";
        
        // Try to parse as JSON first (current format)
        try {
            const tags = JSON.parse(noteRaw);
            if (Array.isArray(tags)) {
                for (const tag of tags) {
                    const tagText = tag.text || "";
                    for (const marker of markers) {
                        if (tagText.includes(marker)) {
                            possessionText = tagText.substring(
                                tagText.indexOf(marker) + marker.length
                            ).trim();
                            break;
                        }
                    }
                    if (possessionText) break;
                }
            }
        } catch (e) {
            // Fallback: try plain text search (old format)
            for (const marker of markers) {
                const idx = noteRaw.indexOf(marker);
                if (idx !== -1) {
                    possessionText = noteRaw.substring(idx + marker.length).trim();
                    break;
                }
            }
        }
        
        if (!possessionText) return [];
        
        // Split by newlines and filter empty
        const lines = possessionText.split("\n")
            .map(l => l.trim())
            .filter(l => l.length > 0);
        
        // Parse each line into {name, qty} objects
        return lines.map(line => {
            // Try format: "PRODUCT_NAME     QTY" (spaces + number at end)
            const match = line.match(/^(.+?)\s{2,}(\d+)$/);
            if (match) {
                return { name: match[1].trim(), qty: parseInt(match[2]) };
            }
            // Try old format: "3x PRODUCT_NAME"
            const oldMatch = line.match(/^(\d+)x\s+(.+)$/);
            if (oldMatch) {
                return { name: oldMatch[2].trim(), qty: parseInt(oldMatch[1]) };
            }
            // Fallback: show as-is
            return { name: line, qty: 0 };
        });
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

/** @odoo-module **/
import { OrderDisplay } from "@point_of_sale/app/components/order_display/order_display";
import { patch } from "@web/core/utils/patch";

patch(OrderDisplay.prototype, {
    /**
     * Override getInternalNotes to gracefully handle corrupted JSON in internal_note.
     * Some older orders may have had plain text appended to the JSON, causing parse failures.
     */
    getInternalNotes() {
        try {
            const tags = JSON.parse(this.props.order.internal_note || "[]");
            // Filter out possession tags — they are already rendered
            // in the dedicated green card below the order details.
            return tags.filter(
                (tag) => !(tag.text && tag.text.startsWith("Productos en posesión"))
            );
        } catch (e) {
            // Corrupted note: return empty array to avoid crash
            console.warn("pos_return: Could not parse internal_note as JSON, ignoring:", e.message);
            return [];
        }
    },
});

/** @odoo-module **/

// Sección colapsable de productos intermedios

import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class ForecastIntermediatesSection extends Component {
    static template = "mrp_production_forecast.ForecastIntermediatesSection";
    static props = {
        intermediates: { type: Array, optional: true },
        isExpanded: Boolean,
        toggleExpand: Function,
    };
    static defaultProps = {
        intermediates: [],
    };

    setup() {
        this.actionService = useService("action");
    }

    onClickProduct(productId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "product.product",
            res_id: productId,
            views: [[false, "form"]],
            target: "current",
        });
    };

    formatNumber(value) {
        if (value === null || value === undefined) return "0";
        return Number(value).toLocaleString("es-MX", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        });
    }
}

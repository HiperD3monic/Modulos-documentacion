/** @odoo-module **/

// Tabla de componentes base con indicadores semáforo

import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class ForecastComponentsTable extends Component {
    static template = "mrp_production_forecast.ForecastComponentsTable";
    static props = {
        components: { type: Array, optional: true },
        mode: String,
    };
    static defaultProps = {
        components: [],
    };

    setup() {
        this.actionService = useService("action");
    }

    onClickComponent(componentId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "product.product",
            res_id: componentId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    get modeColumnLabel() {
        return this.props.mode === "on_hand"
            ? "Stock En Mano"
            : "Stock Pronosticado";
    }

    getStatusBadgeClass(status) {
        const classes = {
            green: "pf-badge-green",
            yellow: "pf-badge-yellow",
            red: "pf-badge-red",
        };
        return classes[status] || "pf-badge-green";
    }

    getStatusLabel(status) {
        const labels = {
            green: "Suficiente",
            yellow: "Justo",
            red: "Limitante",
        };
        return labels[status] || "";
    }

    getStatusIcon(status) {
        const icons = {
            green: "fa-check-circle",
            yellow: "fa-exclamation-triangle",
            red: "fa-times-circle",
        };
        return icons[status] || "fa-circle";
    }

    formatNumber(value) {
        if (value === null || value === undefined) return "0";
        return Number(value).toLocaleString("es-MX", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        });
    }
}

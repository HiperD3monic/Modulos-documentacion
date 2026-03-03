/** @odoo-module **/

// Card de resultado destacada con la cantidad máxima producible

import { Component } from "@odoo/owl";

export class ForecastResultCard extends Component {
    static template = "mrp_production_forecast.ForecastResultCard";
    static props = {
        maxProducible: { type: Number, optional: true },
        modeLabel: String,
        product: { type: Object, optional: true },
        limitingComponent: { type: [Object, { value: null }], optional: true },
        components: { type: Array, optional: true },
    };
    static defaultProps = {
        maxProducible: 0,
        components: [],
    };

    get resultClass() {
        const qty = this.props.maxProducible || 0;
        if (qty === 0) return "pf-result-danger";
        if (qty <= 10) return "pf-result-warning";
        return "pf-result-success";
    }

    get resultIcon() {
        const qty = this.props.maxProducible || 0;
        if (qty === 0) return "fa-times-circle";
        if (qty <= 10) return "fa-exclamation-circle";
        return "fa-check-circle";
    }

    get componentCount() {
        return (this.props.components || []).length;
    }

    get greenCount() {
        return (this.props.components || []).filter(c => c.status === "green").length;
    }

    get yellowCount() {
        return (this.props.components || []).filter(c => c.status === "yellow").length;
    }

    get redCount() {
        return (this.props.components || []).filter(c => c.status === "red").length;
    }

    formatNumber(value) {
        if (value === null || value === undefined) return "0";
        return Number(value).toLocaleString("es-MX");
    }
}

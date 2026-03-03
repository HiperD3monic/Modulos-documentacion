/** @odoo-module **/

// Panel de control: buscador de producto + toggle modo + botones de acción

import { Component, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class ForecastControlPanel extends Component {
    static template = "mrp_production_forecast.ForecastControlPanel";
    static props = {
        mode: String,
        productId: { type: [Number, { value: null }], optional: true },
        productName: { type: String, optional: true },
        isLoading: Boolean,
        hasData: Boolean,
        maxProducible: { type: Number, optional: true },
        changeProduct: Function,
        changeMode: Function,
        clickExport: Function,
        clickCreateMO: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.searchState = useState({
            searchTerm: "",
            suggestions: [],
            showSuggestions: false,
            selectedName: this.props.productName || "",
        });
    }

    // ---- Búsqueda de productos ----

    async onSearchInput(ev) {
        const term = ev.target.value;
        this.searchState.searchTerm = term;
        this.searchState.selectedName = term;

        if (term.length < 2) {
            this.searchState.suggestions = [];
            this.searchState.showSuggestions = false;
            return;
        }

        try {
            const results = await this.orm.call(
                "production.forecast.service",
                "get_products_with_bom",
                [term, 10]
            );
            this.searchState.suggestions = results;
            this.searchState.showSuggestions = results.length > 0;
        } catch {
            this.searchState.suggestions = [];
            this.searchState.showSuggestions = false;
        }
    }

    onSelectProduct(product) {
        this.searchState.selectedName = product.name;
        this.searchState.showSuggestions = false;
        this.searchState.searchTerm = "";
        this.props.changeProduct(product.id, product.name);
    }

    onClearProduct() {
        this.searchState.selectedName = "";
        this.searchState.searchTerm = "";
        this.searchState.suggestions = [];
        this.searchState.showSuggestions = false;
        this.props.changeProduct(null, "");
    }

    onSearchFocus() {
        if (this.searchState.suggestions.length) {
            this.searchState.showSuggestions = true;
        }
    }

    onSearchBlur() {
        // Pequeño delay para permitir click en sugerencias
        setTimeout(() => {
            this.searchState.showSuggestions = false;
        }, 200);
    }

    onSearchKeydown(ev) {
        if (ev.key === "Escape") {
            this.searchState.showSuggestions = false;
        }
    }

    // ---- Toggle modo ----

    onClickOnHand() {
        this.props.changeMode("on_hand");
    }

    onClickForecasted() {
        this.props.changeMode("forecasted");
    }
}

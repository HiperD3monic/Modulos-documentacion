/** @odoo-module **/

// Componente principal del Pronóstico de Producción
// Registrado como client action bajo el tag 'production_forecast'

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { ForecastControlPanel } from "../forecast_control_panel/forecast_control_panel";
import { ForecastComponentsTable } from "../forecast_components_table/forecast_components_table";
import { ForecastIntermediatesSection } from "../forecast_intermediates_section/forecast_intermediates_section";
import { ForecastResultCard } from "../forecast_result_card/forecast_result_card";

export class ProductionForecast extends Component {
    static template = "mrp_production_forecast.ProductionForecast";
    static components = {
        ForecastControlPanel,
        ForecastComponentsTable,
        ForecastIntermediatesSection,
        ForecastResultCard,
    };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            mode: "on_hand",
            productId: null,
            productName: "",
            isLoading: false,
            hasData: false,
            forecastData: {},
            showLog: false,
            showIntermediates: true,
        });

        onWillStart(async () => {
            // No cargar datos al inicio, el usuario seleccionará un producto
        });
    }

    // ---- Handlers ----

    async onChangeProduct(productId, productName) {
        this.state.productId = productId;
        this.state.productName = productName;
        if (productId) {
            await this.loadForecastData();
        } else {
            this.state.hasData = false;
            this.state.forecastData = {};
        }
    }

    async onChangeMode(mode) {
        if (this.state.mode !== mode) {
            this.state.mode = mode;
            if (this.state.productId) {
                await this.loadForecastData();
            }
        }
    }

    onToggleLog() {
        this.state.showLog = !this.state.showLog;
    }

    onToggleIntermediates() {
        this.state.showIntermediates = !this.state.showIntermediates;
    }

    onClickLogEntry(productId) {
        if (!productId) return;
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "product.product",
            res_id: productId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async onClickExport() {
        if (!this.state.productId) return;
        try {
            this.state.isLoading = true;
            const result = await this.orm.call(
                "production.forecast.service",
                "export_xlsx",
                [this.state.productId, this.state.mode]
            );
            // Descargar el archivo
            const link = document.createElement("a");
            link.href = `data:${result.mimetype};base64,${result.file_data}`;
            link.download = result.filename;
            link.click();
            this.notification.add("Archivo Excel descargado exitosamente.", {
                type: "success",
            });
        } catch (error) {
            this.notification.add(
                error.message || "Error al exportar a Excel.",
                { type: "danger" }
            );
        } finally {
            this.state.isLoading = false;
        }
    }

    async onClickCreateMO() {
        if (!this.state.hasData || !this.state.forecastData.bom) return;
        try {
            const action = await this.orm.call(
                "production.forecast.service",
                "create_manufacturing_order",
                [
                    this.state.productId,
                    this.state.forecastData.bom.id,
                    this.state.forecastData.max_producible_qty || 1,
                ]
            );
            await this.actionService.doAction(action);
        } catch (error) {
            this.notification.add(
                error.message || "Error al crear la orden de manufactura.",
                { type: "danger" }
            );
        }
    }

    // ---- Data ----

    async loadForecastData() {
        if (!this.state.productId) return;
        try {
            this.state.isLoading = true;
            this.state.hasData = false;
            const data = await this.orm.call(
                "production.forecast.service",
                "get_forecast_data",
                [this.state.productId, this.state.mode]
            );
            if (data.error) {
                this.notification.add(data.error, { type: "warning" });
                this.state.forecastData = {};
                this.state.hasData = false;
            } else {
                this.state.forecastData = data;
                this.state.hasData = true;
            }
        } catch (error) {
            this.notification.add(
                error.message || "Error al calcular el pronóstico.",
                { type: "danger" }
            );
            this.state.forecastData = {};
            this.state.hasData = false;
        } finally {
            this.state.isLoading = false;
        }
    }

    // ---- Getters ----

    get modeLabel() {
        return this.state.mode === "on_hand"
            ? "Stock En Mano"
            : "Stock Pronosticado";
    }
}

registry.category("actions").add("production_forecast", ProductionForecast);

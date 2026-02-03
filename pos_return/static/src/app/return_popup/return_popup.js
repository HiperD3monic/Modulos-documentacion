/**
 * =============================================================================
 * MÓDULO: pos_return
 * ARCHIVO: static/src/app/return_popup/return_popup.js
 * DESCRIPCIÓN: Componente OWL para el popup de devoluciones
 * MIGRADO: Odoo 18 -> Odoo 19
 * FECHA: 2026-02-01
 * =============================================================================
 * 
 * NOTAS DE MIGRACIÓN:
 * - Imports de @web/core/, @odoo/owl, @point_of_sale/ siguen siendo estables
 * - OWL v2 sintaxis (Component, useState, useService) compatible con v19
 * - usePos() hook sigue disponible en @point_of_sale/app/store/pos_hook
 * - BarcodeVideoScanner sigue en @web/core/barcode/barcode_video_scanner
 * - useAsyncLockedMethod de @point_of_sale/app/utils/hooks sigue disponible
 * - this.pos.data.call() y this.pos.data.callRelated() pueden tener cambios
 *   menores en v19 - marcados con TODO para revisión
 * 
 * =============================================================================
 */

import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { parseFloat } from "@web/views/fields/parsers";
import { Component, useState } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { Dialog } from "@web/core/dialog/dialog";
import { useAsyncLockedMethod } from "@point_of_sale/app/hooks/hooks";
import { Input } from "@point_of_sale/app/components/inputs/input/input";
import { BarcodeVideoScanner, isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";

/**
 * ReturnBarcodeScanner
 * 
 * Componente wrapper del BarcodeVideoScanner para escanear
 * códigos de barras de productos en el popup de devolución.
 */
export class ReturnBarcodeScanner extends BarcodeVideoScanner {
    static props = {
        onBarcodeScanned: { type: Function },
    };

    setup() {
        super.setup();
        // Servicio de sonidos para feedback auditivo
        this.sound = useService("mail.sound_effects");
        this.props = {
            ...this.props,
            facingMode: "environment",  // Cámara trasera
            onResult: (result) => this.onResult(result),
            onError: console.error,
            delayBetweenScan: 2000,  // 2 segundos entre escaneos
        };
    }

    onResult(result) {
        this.props.onBarcodeScanned(result);
        this.sound.play("beep");  // Sonido de confirmación
    }
}

/**
 * ReturnPopup
 * 
 * Componente principal del popup de devoluciones.
 * Permite al usuario:
 * - Ingresar número de ticket externo (Arus, etc.)
 * - Buscar/escanear productos para devolver
 * - Especificar cantidades
 * - Confirmar la devolución (crea picking + salida de efectivo)
 */
export class ReturnPopup extends Component {
    static template = "pos_return.ReturnPopup";
    static components = { Input, Dialog, ReturnBarcodeScanner };
    static props = ["close"];

    setup() {
        super.setup();
        // Servicios
        this.notification = useService("notification");
        this.pos = usePos();
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.ui = useService("ui");
        this.isBarcodeScannerSupported = isBarcodeScannerSupported;

        // Estado reactivo del componente
        this.state = useState({
            ticket: "",           // Número de ticket externo
            products: [],         // Productos seleccionados para devolución
            searchQuery: "",      // Query de búsqueda de productos
            scanning: false,      // Estado del escáner de código de barras
        });

        // Lock para evitar múltiples confirmaciones simultáneas
        this.confirm = useAsyncLockedMethod(this.confirm);
    }

    /**
     * Toggle del escáner de código de barras
     */
    onClickScan() {
        this.state.scanning = !this.state.scanning;
    }

    /**
     * Callback cuando se escanea un código de barras
     * 
     * @param {string} barcode - Código escaneado
     */
    async onBarcodeScanned(barcode) {
        // Primero buscar en el cache local del POS
        let product = this.pos.models["product.product"].getBy("barcode", barcode);

        if (!product) {
            // Intentar buscar por default_code en el cache
            product = this.pos.models["product.product"].filter(
                (p) => p.default_code && p.default_code === barcode
            )[0];
        }

        if (product) {
            this.addProduct(product);
            this.notification.add(
                _t("Producto agregado: %s", product.display_name),
                { type: "success" }
            );
        } else {
            // Si no está en cache, buscar en la base de datos
            try {
                // TODO: revisar en Odoo 19 - La API pos.data.callRelated podría
                // haber cambiado. En v19, considerar usar this.pos.data.call()
                // o el nuevo sistema de comunicación frontend-backend.
                const records = await this.pos.data.callRelated(
                    "pos.session",
                    "find_product_by_barcode",
                    [odoo.pos_session_id, barcode, this.pos.config.id]
                );

                if (records && records["product.product"] && records["product.product"].length > 0) {
                    const foundProduct = records["product.product"][0];
                    this.addProduct(foundProduct);
                    this.notification.add(
                        _t("Producto agregado: %s", foundProduct.display_name),
                        { type: "success" }
                    );
                } else {
                    this.notification.add(
                        _t("Producto no encontrado con código: %s", barcode),
                        { type: "warning" }
                    );
                }
            } catch (error) {
                this.notification.add(
                    _t("Producto no encontrado con código: %s", barcode),
                    { type: "warning" }
                );
            }
        }
    }

    /**
     * Getter computado: productos filtrados por búsqueda
     * 
     * @returns {Array} Lista de productos que coinciden con la búsqueda
     */
    get filteredProducts() {
        if (!this.state.searchQuery) {
            return [];
        }
        const query = this.state.searchQuery.toLowerCase();
        return this.pos.models["product.product"].filter(
            (product) =>
                product.available_in_pos &&
                (product.display_name.toLowerCase().includes(query) ||
                    (product.barcode && product.barcode.includes(query)) ||
                    (product.default_code && product.default_code.toLowerCase().includes(query)))
        ).slice(0, 20);  // Limitar a 20 resultados
    }

    /**
     * Getter computado: monto total de la devolución
     * 
     * @returns {number} Suma de (cantidad * precio) de todos los productos
     */
    get totalAmount() {
        return this.state.products.reduce(
            (sum, p) => sum + p.quantity * p.price_unit,
            0
        );
    }

    /**
     * Getter computado: monto total formateado como moneda
     * 
     * @returns {string} Monto formateado
     */
    get formattedTotal() {
        return this.env.utils.formatCurrency(this.totalAmount);
    }

    /**
     * Añade un producto a la lista de devolución
     * Si ya existe, incrementa la cantidad en 1
     * 
     * @param {Object} product - Producto a añadir
     */
    addProduct(product) {
        // Verificar si el producto ya está en la lista
        const existingProduct = this.state.products.find(
            (p) => p.product_id === product.id
        );

        if (existingProduct) {
            existingProduct.quantity += 1;
        } else {
            this.state.products.push({
                product_id: product.id,
                name: product.display_name,
                quantity: 1,
                price_unit: product.lst_price,
            });
        }

        // Limpiar búsqueda después de añadir
        this.state.searchQuery = "";
    }

    /**
     * Actualiza la cantidad de un producto en la lista
     * 
     * @param {number} index - Índice del producto en la lista
     * @param {string} value - Nuevo valor de cantidad
     */
    updateQuantity(index, value) {
        const qty = parseFloat(value) || 0;
        if (qty <= 0) {
            this.removeProduct(index);
        } else {
            this.state.products[index].quantity = qty;
        }
    }

    /**
     * Elimina un producto de la lista
     * 
     * @param {number} index - Índice del producto a eliminar
     */
    removeProduct(index) {
        this.state.products.splice(index, 1);
    }

    /**
     * Valida si la devolución tiene datos suficientes
     * 
     * @returns {boolean} true si la devolución es válida
     */
    isValidReturn() {
        return (
            this.state.ticket.trim() !== "" &&
            this.state.products.length > 0 &&
            this.totalAmount > 0
        );
    }

    /**
     * Confirma la devolución
     * 
     * Llama al backend para crear el picking y la salida de efectivo.
     * Este método está protegido con useAsyncLockedMethod para evitar
     * múltiples ejecuciones simultáneas.
     */
    async confirm() {
        if (!this.isValidReturn()) {
            this.notification.add(
                _t("Debe ingresar el número de ticket y al menos un producto."),
                { type: "warning" }
            );
            return;
        }

        try {
            // Preparar datos de productos
            const productsData = this.state.products.map((p) => ({
                product_id: p.product_id,
                quantity: p.quantity,
                price_unit: p.price_unit,
            }));

            // TODO: revisar en Odoo 19 - La API pos.data.call podría
            // haber cambiado. El cuarto parámetro (true) es para indicar
            // que se ignoren errores de red. Verificar si esto sigue vigente.
            const result = await this.orm.call(
                "pos.session",
                "create_return",
                [[this.pos.session.id], this.state.ticket.trim(), productsData]
            );

            if (result.success) {
                this.notification.add(
                    _t("Devolución creada exitosamente. Recepción: %s", result.picking_name),
                    { type: "success" }
                );
                this.props.close();
            } else {
                this.notification.add(
                    result.error || _t("Error desconocido al crear la devolución."),
                    { type: "danger" }
                );
            }
        } catch (error) {
            this.notification.add(
                error.message || _t("Error de comunicación con el servidor."),
                { type: "danger" }
            );
        }
    }

    /**
     * Cancela y cierra el popup
     */
    cancel() {
        this.props.close();
    }
}

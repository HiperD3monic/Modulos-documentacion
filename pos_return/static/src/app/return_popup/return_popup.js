import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { parseFloat } from "@web/views/fields/parsers";
import { Component, useState } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { Dialog } from "@web/core/dialog/dialog";
import { useAsyncLockedMethod } from "@point_of_sale/app/hooks/hooks";
import { Input } from "@point_of_sale/app/components/inputs/input/input";
import { BarcodeVideoScanner, isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TicketListPopup } from "../ticket_list_popup/ticket_list_popup";

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
            returnType: "odoo",   // 'odoo', 'arus', 'no_ticket'
            partner: null,        // Cliente seleccionado
            customerTickets: [],  // Lista de tickets del cliente
            selectedTicket: null, // Objeto completo del ticket seleccionado
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
     * Handle return type change.
     * Resets dependent state to avoid inconsistent data.
     */
    onTypeChange(ev) {
        // t-model handles the value update, but we need to clear data
        // associated with the previous type.
        this.state.products = [];
        this.state.selectedTicket = null;
        this.state.ticket = "";
        this.state.searchQuery = "";

        // If switching back to Odoo, we might want to re-fetch partner tickets
        // but selectPartner logic handles that when partner changes. 
        // If partner is already selected, tickets are likely cached in customerTickets.

        // Fix: If partner is selected but tickets are missing (e.g. selected during Arus mode),
        // we must fetch them now.
        if (ev.target.value === 'odoo' && this.state.partner) {
            this._loadTicketsForPartner(this.state.partner.id);
        }
    }

    async _loadTicketsForPartner(partnerId) {
        try {
            const tickets = await this.orm.call(
                "pos.session",
                "get_partner_tickets",
                [odoo.pos_session_id, partnerId]
            );
            this.state.customerTickets = tickets;
        } catch (error) {
            console.error("Error fetching partner tickets:", error);
            this.notification.add(_t("Error al cargar tickets del cliente"), { type: "danger" });
            this.state.customerTickets = [];
        }
    }

    async selectPartner() {
        const newPartner = await makeAwaitable(this.dialog, PartnerList, {
            partner: this.state.partner,
        });

        // Debug logging
        console.log("ReturnPopup: PartnerList result (raw):", newPartner);

        // Treat undefined (from 'X' button) as null.
        const resolvedPartner = newPartner || null;

        // Compare IDs to see if there is an actual change.
        // If user clicked 'Discard', PartnerList returns the original partner -> IDs match -> No change.
        // If user clicked 'X', newPartner is undefined -> resolvedPartner is null -> IDs differ -> Update.
        const currentId = this.state.partner ? this.state.partner.id : null;
        const newId = resolvedPartner ? resolvedPartner.id : null;

        if (currentId !== newId) {
            console.log("ReturnPopup: Updating partner to:", resolvedPartner);
            this.state.partner = resolvedPartner;

            // Clear previous ticket data to prevent mismatch
            this.state.customerTickets = [];
            this.state.selectedTicket = null;
            this.state.ticket = "";
            this.state.products = [];
            this.state.searchQuery = "";

            if (resolvedPartner && this.state.returnType === 'odoo') {
                await this._loadTicketsForPartner(resolvedPartner.id);
            }

            // Notification to confirm reset (and debug reactivity)
            const msg = resolvedPartner
                ? _t("Cliente actualizado. Ticket reiniciado.")
                : _t("Cliente desvinculado.");
            this.notification.add(msg, { type: "info" });
        }
    }

    async searchTickets() {
        if (this.state.customerTickets.length === 0) {
            this.notification.add(_t("El cliente no tiene tickets recientes."), { type: "warning" });
            return;
        }

        const selectedTicket = await makeAwaitable(this.dialog, TicketListPopup, {
            tickets: this.state.customerTickets,
        });

        if (selectedTicket) {
            this.state.selectedTicket = selectedTicket;
            this.state.ticket = selectedTicket.pos_reference || selectedTicket.name;

            // Auto-populate products from the ticket (only returnable items)
            if (selectedTicket.lines && selectedTicket.lines.length > 0) {
                this.state.products = selectedTicket.lines.map(line => ({
                    product_id: line.product_id,
                    name: line.name,
                    quantity: line.remaining_qty || line.qty, // Default to remaining
                    max_quantity: line.remaining_qty || line.qty, // Enforce limit
                    price_unit: line.price_unit,
                }));
            } else {
                this.state.products = [];
            }
        }
    }

    get inputPlaceholder() {
        if (this.state.returnType === "no_ticket") {
            return _t("Ingrese la razón de la devolución...");
        }
        return _t("Ingrese el número de ticket...");
    }

    get inputLabel() {
        if (this.state.returnType === "no_ticket") {
            return _t("Razón de Devolución *");
        } else if (this.state.returnType === "arus") {
            return _t("Número de Ticket (Arus) *");
        }
        return _t("Número de Ticket (Odoo) *");
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
        let qty = parseFloat(value) || 0;
        const product = this.state.products[index];

        // Validate against max_quantity if it exists (Strict Mode)
        // Validate against max_quantity if it exists (Strict Mode)
        // We do NOT clamp the value here anymore. We allow the invalid value in state,
        // which causes isValidReturn property to become false, disabling the confirm button.
        if (product.max_quantity !== undefined && qty > product.max_quantity) {
            this.notification.add(
                _t("Atención: La cantidad ingresada (%s) excede el máximo permitido (%s). Corríjala para continuar.", qty, product.max_quantity),
                { type: "warning" }
            );
            // We do NOT reset qty = product.max_quantity here.
            // This ensures the button gets disabled.
        }

        if (qty <= 0) {
            this.removeProduct(index);
        } else {
            product.quantity = qty;
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
        const hasTicketOrReason = this.state.ticket.trim() !== "";

        // Ensure all quantities are within limits
        const withinLimits = this.state.products.every(p => {
            if (p.max_quantity !== undefined) {
                return p.quantity <= p.max_quantity;
            }
            return true;
        });

        return (
            hasTicketOrReason &&
            this.state.products.length > 0 &&
            this.totalAmount > 0 &&
            withinLimits
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

        // Final Security Check: Ensure no product exceeds max_quantity
        // This prevents bypassing UI limits
        for (const product of this.state.products) {
            if (product.max_quantity !== undefined && product.quantity > product.max_quantity) {
                this.notification.add(
                    _t("Error: La cantidad de %s excede lo comprado (%s).", product.name, product.max_quantity),
                    { type: "danger" }
                );
                return;
            }
        }

        try {
            // Preparar datos de productos
            const productsData = this.state.products.map((p) => ({
                product_id: p.product_id,
                quantity: p.quantity,
                price_unit: p.price_unit,
            }));

            const result = await this.orm.call(
                "pos.session",
                "create_return",
                [
                    [this.pos.session.id],
                    this.state.ticket.trim(),
                    productsData,
                    this.state.returnType,
                    this.state.partner ? this.state.partner.id : false
                ]
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
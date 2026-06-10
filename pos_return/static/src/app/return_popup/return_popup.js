import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { parseFloat } from "@web/views/fields/parsers";
import { Component, useState } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { Dialog } from "@web/core/dialog/dialog";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useAsyncLockedMethod } from "@point_of_sale/app/hooks/hooks";
import { Input } from "@point_of_sale/app/components/inputs/input/input";
import { BarcodeVideoScanner, isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TicketListPopup } from "../ticket_list_popup/ticket_list_popup";
import { CustomerMismatchDialog } from "../customer_mismatch_dialog/customer_mismatch_dialog";

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
 * Popup principal del flujo de devoluciones.
 * 
 * ARQUITECTURA v2 (Nativa):
 * 1. Usuario selecciona ticket + productos a devolver
 * 2. Confirm() valida vía backend (create_return)
 * 3. Crea una orden POS nativa con líneas NEGATIVAS
 * 4. Navega al PaymentScreen nativo
 * 5. Al pagar, Odoo crea picking de entrada automáticamente
 * 6. Contabilidad se maneja al cerrar sesión
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
            ticketSearchResults: [], // Resultados de búsqueda por referencia
            searchingTickets: false, // Indicador de búsqueda en progreso
        });

        // Debounce timer para búsqueda de tickets
        this._ticketSearchTimer = null;

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
    onTypeChange() {
        // t-model handles the value update, but we need to clear data
        // associated with the previous type.
        this.state.products = [];
        this.state.selectedTicket = null;
        this.state.ticket = "";
        this.state.searchQuery = "";
        this.state.ticketSearchResults = [];
        this.state.searchingTickets = false;

        // If switching back to Odoo, we might want to re-fetch partner tickets
        if (this.state.returnType === 'odoo' && this.state.partner) {
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

        // Treat undefined (from 'X' button) as null.
        const resolvedPartner = newPartner || null;

        const currentId = this.state.partner ? this.state.partner.id : null;
        const newId = resolvedPartner ? resolvedPartner.id : null;

        if (currentId !== newId) {
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
            this._applyTicketSelection(selectedTicket);
        }
    }

    /**
     * Búsqueda automática de tickets por referencia.
     * Se activa al escribir en el input de ticket (modo Odoo).
     * Usa debounce de 500ms para evitar llamadas excesivas.
     */
    /**
     * Carga las órdenes recientes cuando el input de ticket recibe foco.
     * Permite al usuario ver y seleccionar tickets sin necesidad de escribir.
     */
    async onTicketFocus() {
        // Only load if no text and no results already showing
        if (this.state.ticket.trim().length === 0 && this.state.ticketSearchResults.length === 0) {
            await this._loadRecentTickets();
        }
    }

    /**
     * Carga las últimas órdenes recientes del POS.
     */
    async _loadRecentTickets() {
        this.state.searchingTickets = true;
        try {
            const results = await this.orm.call(
                "pos.session",
                "search_recent_tickets",
                [[this.pos.session.id]]
            );
            this.state.ticketSearchResults = results;
        } catch (error) {
            console.error("Error loading recent tickets:", error);
            this.state.ticketSearchResults = [];
        } finally {
            this.state.searchingTickets = false;
        }
    }

    onTicketInput(ev) {
        const query = ev.target.value.trim();
        this.state.ticket = ev.target.value;

        // Clear previous timer
        if (this._ticketSearchTimer) {
            clearTimeout(this._ticketSearchTimer);
        }

        // If query is empty, load recent tickets instead of clearing
        if (query.length === 0) {
            this._loadRecentTickets();
            return;
        }

        // For very short queries (1 char), don't search yet but keep recent results
        if (query.length < 2) {
            return;
        }

        // Debounce: wait 500ms before searching
        this.state.searchingTickets = true;
        this._ticketSearchTimer = setTimeout(async () => {
            try {
                const results = await this.orm.call(
                    "pos.session",
                    "search_ticket_by_ref",
                    [[this.pos.session.id], query]
                );
                this.state.ticketSearchResults = results;
            } catch (error) {
                console.error("Error searching tickets:", error);
                this.state.ticketSearchResults = [];
            } finally {
                this.state.searchingTickets = false;
            }
        }, 500);
    }

    /**
     * Cierra el dropdown de resultados cuando el input pierde foco.
     * Usa un delay pequeño para permitir que el click en un item
     * del dropdown se registre antes de limpiar los resultados.
     */
    onTicketBlur() {
        setTimeout(() => {
            // Only clear if no ticket was selected (user just clicked away)
            if (!this.state.selectedTicket) {
                this.state.ticketSearchResults = [];
            }
        }, 200);
    }

    /**
     * Selecciona un ticket de los resultados de búsqueda automática.
     */
    selectSearchResult(ticket) {
        this.state.ticketSearchResults = [];
        this._applyTicketSelection(ticket);
    }

    /**
     * Quita el ticket seleccionado y vuelve al input de búsqueda.
     */
    clearSelectedTicket() {
        this.state.selectedTicket = null;
        this.state.ticket = "";
        this.state.products = [];
        this.state.ticketSearchResults = [];
        this.state.searchingTickets = false;
    }

    /**
     * Aplica la selección de un ticket (usado tanto por searchTickets como selectSearchResult).
     * Verifica discrepancia de cliente entre el popup y el ticket seleccionado.
     */
    async _applyTicketSelection(ticket) {
        // ── Customer mismatch check ──
        const popupPartnerId = this.state.partner ? this.state.partner.id : false;
        const ticketPartnerId = ticket.partner_id || false;

        if (popupPartnerId && popupPartnerId !== ticketPartnerId) {
            // Popup has a customer, ticket has a different one (or none)
            const popupPartnerName = this.state.partner.name;
            const ticketPartnerName = ticket.partner_name || _t("Sin cliente");

            if (!ticketPartnerId) {
                // Ticket has NO client — single dialog with 3 distinct actions:
                // "Vincular al cliente" / "Quitar cliente" / X (cancel)
                const choice = await new Promise((resolve) => {
                    this.dialog.add(CustomerMismatchDialog, {
                        body: _t(
                            "Este ticket fue realizado sin cliente, pero usted tiene seleccionado a '%s'. " +
                            "¿Qué desea hacer?",
                            popupPartnerName
                        ),
                        onKeepClient: () => resolve('keep'),
                        onRemoveClient: () => resolve('remove'),
                    }, {
                        onClose: () => resolve('cancel'),
                    });
                });

                if (choice === 'cancel') {
                    return;
                }
                if (choice === 'remove') {
                    this.state.partner = null;
                    this.state.customerTickets = [];
                }
            } else {
                // Ticket has a DIFFERENT client — 2 choices (same as before)
                const bodyMessage = _t(
                    "Este ticket pertenece a '%s', pero usted tiene seleccionado a '%s'. " +
                    "¿Desea cambiar al cliente del ticket?",
                    ticketPartnerName, popupPartnerName
                );

                const confirmed = await makeAwaitable(this.dialog, ConfirmationDialog, {
                    title: _t("Cliente diferente"),
                    body: bodyMessage,
                    confirmLabel: _t("Usar cliente del ticket"),
                    cancelLabel: _t("Cancelar selección"),
                });

                if (!confirmed) {
                    return;
                }

                const ticketPartner = this.pos.models["res.partner"].get(ticketPartnerId);
                if (ticketPartner) {
                    this.state.partner = ticketPartner;
                    await this._loadTicketsForPartner(ticketPartner.id);
                }
            }
        }

        this.state.selectedTicket = ticket;
        this.state.ticket = ticket.pos_reference || ticket.name;
        this.state.ticketSearchResults = [];

        // Auto-populate partner from the ticket if no partner was set yet
        if (ticket.partner_id && !this.state.partner) {
            const partner = this.pos.models["res.partner"].get(ticket.partner_id);
            if (partner) {
                this.state.partner = partner;
                // Load partner's tickets so "change ticket" button works
                await this._loadTicketsForPartner(partner.id);
            }
        }

        // Auto-populate products from the ticket (only returnable items)
        if (ticket.lines && ticket.lines.length > 0) {
            this.state.products = ticket.lines.map(line => ({
                product_id: line.product_id,
                name: line.name,
                quantity: line.remaining_qty || line.qty,
                max_quantity: line.remaining_qty || line.qty,
                price_unit: line.price_unit,
                discount: line.discount || 0,
                tax_ids: line.tax_ids || [],
                original_line_id: line.id,  // For refunded_orderline_id linkage
            }));
        } else {
            this.state.products = [];
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
     */
    async onBarcodeScanned(barcode) {
        let product = this.pos.models["product.product"].getBy("barcode", barcode);

        if (!product) {
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
        ).slice(0, 20);
    }

    /**
     * Getter computado: monto total de la devolución
     */
    get totalAmount() {
        return this.state.products.reduce(
            (sum, p) => {
                const disc = p.discount || 0;
                const effectivePrice = p.price_unit * (1 - disc / 100);
                return sum + p.quantity * effectivePrice;
            },
            0
        );
    }

    /**
     * Getter computado: monto total formateado como moneda
     */
    get formattedTotal() {
        return this.env.utils.formatCurrency(this.totalAmount);
    }

    /**
     * Añade un producto a la lista de devolución
     */
    addProduct(product) {
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
                discount: 0,
            });
        }

        this.state.searchQuery = "";
    }

    /**
     * Actualiza la cantidad de un producto en la lista
     */
    updateQuantity(index, value) {
        let qty = parseFloat(value) || 0;
        const product = this.state.products[index];

        if (product.max_quantity !== undefined && qty > product.max_quantity) {
            this.notification.add(
                _t("Atención: La cantidad ingresada (%s) excede el máximo permitido (%s). Corríjala para continuar.", qty, product.max_quantity),
                { type: "warning" }
            );
        }

        if (qty <= 0) {
            this.removeProduct(index);
        } else {
            product.quantity = qty;
        }
    }

    /**
     * Actualiza el descuento de un producto en la lista
     */
    updateDiscount(index, value) {
        let disc = parseFloat(value) || 0;
        if (disc < 0) disc = 0;
        if (disc > 100) disc = 100;
        this.state.products[index].discount = disc;
    }

    /**
     * Elimina un producto de la lista
     */
    removeProduct(index) {
        this.state.products.splice(index, 1);
    }

    /**
     * Valida si la devolución tiene datos suficientes
     */
    isValidReturn() {
        const hasTicketOrReason = this.state.ticket.trim() !== "";

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
     * Confirma la devolución — ARQUITECTURA v2 (Nativa)
     * 
     * 1. Valida datos vía backend (create_return)
     * 2. Crea una orden POS nativa con líneas NEGATIVAS
     * 3. Navega al PaymentScreen para procesar el pago
     * 4. Al pagar, Odoo crea el picking de entrada automáticamente
     */
    async confirm() {
        if (!this.isValidReturn()) {
            this.notification.add(
                _t("Debe ingresar el número de ticket y al menos un producto."),
                { type: "warning" }
            );
            return;
        }

        // Final Security Check
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
            // 1. Validar en el backend
            const productsData = this.state.products.map((p) => {
                const disc = p.discount || 0;
                const effectivePrice = p.price_unit * (1 - disc / 100);
                return {
                    product_id: p.product_id,
                    quantity: p.quantity,
                    price_unit: effectivePrice,
                };
            });

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

            if (!result.success) {
                this.notification.add(
                    result.error || _t("Error desconocido al validar la devolución."),
                    { type: "danger" }
                );
                return;
            }

            // 2. Cerrar popup
            this.props.close();

            // 3. Crear orden POS nativa con líneas negativas
            const order = this.pos.addNewOrder();

            // 4. Marcar como reembolso y asignar cliente si existe
            const updateVals = { is_refund: true };
            if (this.state.partner) {
                updateVals.partner_id = this.state.partner;
            }
            order.update(updateVals);

            // 5. Agregar líneas NEGATIVAS (devolución)
            for (const prod of this.state.products) {
                const product = this.pos.models["product.product"].get(prod.product_id);
                if (product) {
                    // Build customer note with reason if available
                    let customerNote = _t("Devolución ► %s", result.base_type);
                    if (result.reason) {
                        customerNote = _t("Devolución ► %s | Razón: %s", result.base_type, result.reason);
                    }

                    const disc = prod.discount || 0;
                    const lineVals = {
                        product_id: product,
                        product_tmpl_id: product.product_tmpl_id,
                        price_unit: prod.price_unit,
                        discount: disc,
                        qty: -prod.quantity,  // NEGATIVO = devolución
                        customer_note: customerNote,
                    };

                    // Link to original orderline if available (for native refund tracking)
                    if (prod.original_line_id && typeof prod.original_line_id === 'number') {
                        const originalLine = this.pos.models["pos.order.line"].get(prod.original_line_id);
                        if (originalLine) {
                            lineVals.refunded_orderline_id = originalLine;
                            lineVals.tax_ids = originalLine.tax_ids.map((tax) => ["link", tax]);
                        }
                    }

                    await this.pos.addLineToCurrentOrder(lineVals, {}, false);
                }
            }

            // 6. Agregar nota interna con referencia y razón
            let noteText = _t("Devolución - %s | Ref: %s", result.base_type, result.origin_ref);
            if (result.reason) {
                noteText = _t("Devolución - %s | Razón: %s", result.base_type, result.reason);
            }
            const noteTag = {
                text: noteText,
                colorIndex: 1,
            };
            order.internal_note = JSON.stringify([noteTag]);

            // 6b. Calculate remaining products in customer's possession
            // Store as structured data for receipt template rendering
            if (this.state.selectedTicket && this.state.returnType === "odoo") {
                const ticketLines = this.state.selectedTicket.lines || [];
                // Build map: product_id → total qty being returned now
                const returningMap = {};
                for (const prod of this.state.products) {
                    returningMap[prod.product_id] = (returningMap[prod.product_id] || 0) + prod.quantity;
                }

                const possessionItems = [];
                // Work with a copy of the map so we can deduct as we iterate
                const returningLeft = { ...returningMap };
                for (const line of ticketLines) {
                    if (line.remaining_qty <= 0) continue;
                    const pid = line.product_id;
                    const returningQty = returningLeft[pid] || 0;
                    const consumed = Math.min(returningQty, line.remaining_qty);
                    const stillHas = line.remaining_qty - consumed;
                    // Reduce what's left to deduct for this product
                    returningLeft[pid] = Math.max(0, returningQty - consumed);

                    if (stillHas > 0) {
                        // Clean product name: remove emoji prefixes and [CODE] patterns
                        const cleanName = line.name
                            .replace(/^🔄\s*/, '')
                            .replace(/^↩\s*/, '')
                            .replace(/^\[.*?\]\s*/, '');
                        possessionItems.push({
                            name: cleanName,
                            qty: Math.round(stillHas),
                        });
                    }
                }

                if (possessionItems.length > 0) {
                    // Store structured data for receipt template rendering
                    order._possessionItems = possessionItems;
                }
            }

            // 7. Reload original order if marked
            if (result.original_order_id) {
                try {
                    await this.pos.data.loadServerOrders([
                        ["id", "=", result.original_order_id]
                    ]);
                } catch (e) {
                    console.warn("Could not reload order:", e);
                }
            }

            this.notification.add(
                _t("Devolución preparada. Complete el reembolso en la pantalla de pago."),
                { type: "info" }
            );

            // 8. Navegar al PaymentScreen
            order.setScreenData({ name: "PaymentScreen" });
            this.pos.navigate("PaymentScreen", {
                orderUuid: order.uuid,
            });

        } catch (error) {
            this.notification.add(
                error.message || _t("Error de comunicación con el servidor."),
                { type: "danger" }
            );
        }
    }

    /**
     * Cambia del popup de devolución al popup de intercambio
     */
    switchToExchange() {
        this.props.close();
        this.pos.openExchange();
    }

    /**
     * Cancela y cierra el popup
     */
    cancel() {
        this.props.close();
    }
}
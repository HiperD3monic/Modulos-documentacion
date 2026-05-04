import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { parseFloat } from "@web/views/fields/parsers";
import { Component, useState } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { Dialog } from "@web/core/dialog/dialog";
import { useAsyncLockedMethod } from "@point_of_sale/app/hooks/hooks";
import { Input } from "@point_of_sale/app/components/inputs/input/input";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TicketListPopup } from "../ticket_list_popup/ticket_list_popup";
import { ReturnBarcodeScanner } from "../return_popup/return_popup";

/**
 * ExchangePopup
 * 
 * Popup principal de intercambio de productos.
 * Permite al usuario:
 * - Seleccionar productos a devolver
 * - Seleccionar productos nuevos que se lleva
 * - Ver la diferencia de precio en tiempo real
 * - Confirmar el intercambio (crea picking entrada + salida + movimiento de caja inteligente)
 */
export class ExchangePopup extends Component {
    static template = "pos_return.ExchangePopup";
    static components = { Input, Dialog, ReturnBarcodeScanner };
    static props = ["close"];

    setup() {
        super.setup();
        this.notification = useService("notification");
        this.pos = usePos();
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.ui = useService("ui");
        this.isBarcodeScannerSupported = isBarcodeScannerSupported;

        this.state = useState({
            ticket: "",
            returnedProducts: [],     // Productos que el cliente devuelve
            newProducts: [],           // Productos nuevos que se lleva
            searchQueryReturned: "",   // Búsqueda para productos devueltos
            searchQueryNew: "",        // Búsqueda para productos nuevos
            scanningReturned: false,
            scanningNew: false,
            exchangeType: "odoo",      // 'odoo', 'arus', 'no_ticket'
            partner: null,
            customerTickets: [],
            selectedTicket: null,
            ticketSearchResults: [],
            searchingTickets: false,
        });

        this._ticketSearchTimer = null;
        this.confirm = useAsyncLockedMethod(this.confirm);
    }

    // =====================================================================
    // Type & Partner Management (same patterns as ReturnPopup)
    // =====================================================================

    onTypeChange() {
        this.state.returnedProducts = [];
        this.state.newProducts = [];
        this.state.selectedTicket = null;
        this.state.ticket = "";
        this.state.searchQueryReturned = "";
        this.state.searchQueryNew = "";
        this.state.ticketSearchResults = [];
        this.state.searchingTickets = false;

        if (this.state.exchangeType === 'odoo' && this.state.partner) {
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

        const resolvedPartner = newPartner || null;
        const currentId = this.state.partner ? this.state.partner.id : null;
        const newId = resolvedPartner ? resolvedPartner.id : null;

        if (currentId !== newId) {
            this.state.partner = resolvedPartner;
            this.state.customerTickets = [];
            this.state.selectedTicket = null;
            this.state.ticket = "";
            this.state.returnedProducts = [];
            this.state.newProducts = [];
            this.state.searchQueryReturned = "";
            this.state.searchQueryNew = "";

            if (resolvedPartner && this.state.exchangeType === 'odoo') {
                await this._loadTicketsForPartner(resolvedPartner.id);
            }

            const msg = resolvedPartner
                ? _t("Cliente actualizado. Datos reiniciados.")
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
            await this._applyTicketSelection(selectedTicket);
        }
    }

    /**
     * Búsqueda automática de tickets por referencia (debounce 500ms).
     */
    onTicketInput(ev) {
        const query = ev.target.value.trim();
        this.state.ticket = ev.target.value;

        if (this._ticketSearchTimer) {
            clearTimeout(this._ticketSearchTimer);
        }

        if (query.length < 2) {
            this.state.ticketSearchResults = [];
            this.state.searchingTickets = false;
            return;
        }

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
        this.state.returnedProducts = [];
        this.state.newProducts = [];
        this.state.ticketSearchResults = [];
        this.state.searchingTickets = false;
    }

    /**
     * Aplica la selección de un ticket.
     */
    async _applyTicketSelection(ticket) {
        this.state.selectedTicket = ticket;
        this.state.ticket = ticket.pos_reference || ticket.name;
        this.state.ticketSearchResults = [];

        // Auto-populate partner from the ticket if available
        if (ticket.partner_id) {
            const partner = this.pos.models["res.partner"].get(ticket.partner_id);
            if (partner) {
                this.state.partner = partner;
                await this._loadTicketsForPartner(partner.id);
            }
        }

        // Auto-populate returned products from ticket
        if (ticket.lines && ticket.lines.length > 0) {
            this.state.returnedProducts = ticket.lines.map(line => ({
                product_id: line.product_id,
                name: line.name,
                quantity: line.remaining_qty || line.qty,
                max_quantity: line.remaining_qty || line.qty,
                price_unit: line.price_unit,
            }));
        } else {
            this.state.returnedProducts = [];
        }
        // New products list stays empty — user picks them separately
        this.state.newProducts = [];
    }

    // =====================================================================
    // Input labels & placeholders
    // =====================================================================

    get inputPlaceholder() {
        if (this.state.exchangeType === "no_ticket") {
            return _t("Ingrese la razón del intercambio...");
        }
        return _t("Ingrese el número de ticket...");
    }

    get inputLabel() {
        if (this.state.exchangeType === "no_ticket") {
            return _t("Razón del Intercambio *");
        } else if (this.state.exchangeType === "arus") {
            return _t("Número de Ticket (Arus) *");
        }
        return _t("Número de Ticket (Odoo) *");
    }

    // =====================================================================
    // Product Search & Barcode Scanning
    // =====================================================================

    onClickScanReturned() {
        this.state.scanningReturned = !this.state.scanningReturned;
        this.state.scanningNew = false;
    }

    onClickScanNew() {
        this.state.scanningNew = !this.state.scanningNew;
        this.state.scanningReturned = false;
    }

    async onBarcodeScannedReturned(barcode) {
        await this._handleBarcodeScan(barcode, 'returned');
    }

    async onBarcodeScannedNew(barcode) {
        await this._handleBarcodeScan(barcode, 'new');
    }

    async _handleBarcodeScan(barcode, target) {
        let product = this.pos.models["product.product"].getBy("barcode", barcode);

        if (!product) {
            product = this.pos.models["product.product"].filter(
                (p) => p.default_code && p.default_code === barcode
            )[0];
        }

        if (product) {
            this._addProductTo(target, product);
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
                    this._addProductTo(target, foundProduct);
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

    // =====================================================================
    // Filtered Products — unique templates (not individual variants)
    // =====================================================================

    _filterProductTemplates(query) {
        if (!query) return [];
        const q = query.toLowerCase();

        // Get all products matching the query
        const matchedProducts = this.pos.models["product.product"].filter(
            (product) =>
                product.display_name.toLowerCase().includes(q) ||
                (product.barcode && product.barcode.includes(q)) ||
                (product.default_code && product.default_code.toLowerCase().includes(q))
        );

        // Get active pricelist for pricing
        const order = this.pos.getOrder();
        const pricelist = order?.pricelist_id || this.pos.config.pricelist_id;

        // Deduplicate by product template — show one entry per template
        const seenTemplates = new Set();
        const templates = [];
        for (const product of matchedProducts) {
            const tmpl = product.product_tmpl_id;
            const tmplId = tmpl ? (tmpl.id || tmpl) : product.id;
            if (!seenTemplates.has(tmplId)) {
                seenTemplates.add(tmplId);
                const templateObj = typeof tmpl === 'object' ? tmpl : null;
                const variantCount = templateObj ? (templateObj.product_variant_ids?.length || 1) : 1;

                // Calculate price using pricelist (same as native POS)
                let price = product.lst_price;
                if (pricelist && templateObj && templateObj.getPrice) {
                    price = templateObj.getPrice(pricelist, 1, 0, false, product);
                }

                templates.push({
                    id: tmplId,
                    templateObj: templateObj,
                    product: product,  // first matching product (used as fallback)
                    name: templateObj ? templateObj.display_name : product.display_name,
                    price: price,
                    isConfigurable: templateObj ? templateObj.isConfigurable() : false,
                    variantCount: variantCount,
                });
            }
        }
        return templates.slice(0, 20);
    }

    get filteredProductsReturned() {
        return this._filterProductTemplates(this.state.searchQueryReturned);
    }

    get filteredProductsNew() {
        return this._filterProductTemplates(this.state.searchQueryNew);
    }

    // =====================================================================
    // Product Management (add, update, remove)
    // =====================================================================

    _addProductTo(target, product) {
        // Prevent adding the same product to both sides
        const oppositeList = target === 'returned' ? this.state.newProducts : this.state.returnedProducts;
        const inOpposite = oppositeList.find((p) => p.product_id === product.id);
        if (inOpposite) {
            this.notification.add(
                _t("No puede intercambiar un producto por el mismo. Seleccione una variante o producto diferente."),
                { type: "warning" }
            );
            // Clear search
            if (target === 'returned') {
                this.state.searchQueryReturned = "";
            } else {
                this.state.searchQueryNew = "";
            }
            return;
        }

        const list = target === 'returned' ? this.state.returnedProducts : this.state.newProducts;
        const existing = list.find((p) => p.product_id === product.id);

        if (existing) {
            existing.quantity += 1;
        } else {
            // Use the POS pricelist price (matches actual selling price)
            // Must call getPrice on product_tmpl_id (template), passing the variant
            // as the last argument — this matches native POS behavior in pos_order_line.js
            const order = this.pos.getOrder();
            const pricelist = order?.pricelist_id || this.pos.config.pricelist_id;
            const productTemplate = product.product_tmpl_id;
            let price = product.lst_price;
            if (pricelist && productTemplate && productTemplate.getPrice) {
                price = productTemplate.getPrice(pricelist, 1, 0, false, product);
            }

            list.push({
                product_id: product.id,
                name: product.display_name,
                quantity: 1,
                price_unit: price,
            });
        }

        // Clear the corresponding search
        if (target === 'returned') {
            this.state.searchQueryReturned = "";
        } else {
            this.state.searchQueryNew = "";
        }
    }

    /**
     * Handle clicking a template in the search results.
     * If the template is configurable (has variants), opens the native POS configurator.
     * Otherwise, adds the product directly.
     */
    async addReturnedTemplate(tmpl) {
        await this._addTemplateToTarget('returned', tmpl);
    }

    async addNewTemplate(tmpl) {
        await this._addTemplateToTarget('new', tmpl);
    }

    /**
     * Resolves the correct product variant from a configurator payload.
     * Handles static and dynamic variant creation (mirrors native POS logic).
     *
     * @param {Object} templateObj - product.template record
     * @param {Object} payload - configurator result with attribute_value_ids
     * @returns {Object|null} resolved product.product or null
     */
    async _resolveVariantFromPayload(templateObj, payload) {
        const selectedAttrIds = payload.attribute_value_ids || [];
        const attrValues = this.pos.models["product.template.attribute.value"]
            .readMany(selectedAttrIds)
            .filter((v) => v.attribute_id.create_variant !== "no_variant")
            .map((v) => v.id);

        // Try 1: Find matching variant locally (same logic as native POS)
        let variant = templateObj.product_variant_ids.find((v) => {
            const vAttrIds = v.product_template_variant_value_ids.map((a) => a.id);
            return (
                attrValues.every((id) => vAttrIds.includes(id)) &&
                attrValues.length
            );
        });

        // Try 2: If not found locally, resolve via server
        // This handles: variants not loaded in POS cache + dynamic variants
        if (!variant) {
            try {
                const result = await this.pos.data.callRelated(
                    "product.template",
                    "create_product_variant_from_pos",
                    [templateObj.id, selectedAttrIds, this.pos.config.id]
                );
                if (result && result["product.product"] && result["product.product"].length > 0) {
                    variant = result["product.product"][0];
                }
            } catch (error) {
                console.error("Error resolving variant from server:", error);
            }
        }

        return variant || null;
    }

    async _addTemplateToTarget(target, tmpl) {
        if (tmpl.isConfigurable && tmpl.templateObj) {
            const payload = await this.pos.openConfigurator(tmpl.templateObj);
            if (payload) {
                const variant = await this._resolveVariantFromPayload(tmpl.templateObj, payload);
                if (variant) {
                    this._addProductTo(target, variant);
                } else {
                    this.notification.add(
                        _t("No se pudo determinar la variante seleccionada."),
                        { type: "warning" }
                    );
                }
            }
        } else {
            this._addProductTo(target, tmpl.product);
        }
    }

    addReturnedProduct(product) {
        this._addProductTo('returned', product);
    }

    addNewProduct(product) {
        this._addProductTo('new', product);
    }

    updateReturnedQuantity(index, value) {
        let qty = parseFloat(value) || 0;
        const product = this.state.returnedProducts[index];

        if (product.max_quantity !== undefined && qty > product.max_quantity) {
            this.notification.add(
                _t("Atención: La cantidad (%s) excede el máximo permitido (%s).", qty, product.max_quantity),
                { type: "warning" }
            );
        }

        if (qty <= 0) {
            this.removeReturnedProduct(index);
        } else {
            product.quantity = qty;
        }
    }

    updateNewQuantity(index, value) {
        let qty = parseFloat(value) || 0;
        if (qty <= 0) {
            this.removeNewProduct(index);
        } else {
            this.state.newProducts[index].quantity = qty;
        }
    }

    removeReturnedProduct(index) {
        this.state.returnedProducts.splice(index, 1);
    }

    removeNewProduct(index) {
        this.state.newProducts.splice(index, 1);
    }

    /**
     * Checks if a returned product has variants (i.e., its template is configurable).
     * Used to show/hide the swap button in the UI.
     */
    hasVariants(product) {
        const posProduct = this.pos.models["product.product"].get(product.product_id);
        if (!posProduct) return false;
        const tmpl = posProduct.product_tmpl_id;
        if (!tmpl || typeof tmpl !== 'object') return false;
        return tmpl.isConfigurable ? tmpl.isConfigurable() : false;
    }

    /**
     * Quick Variant Swap: opens the native configurator for the same product template
     * and adds the selected variant to "Productos Nuevos".
     * 
     * Example: Client returns "T-Shirt (Red, L)" → clicks swap → picks "T-Shirt (Blue, M)"
     *          → "T-Shirt (Blue, M)" is added to newProducts automatically.
     */
    async swapVariant(returnedProduct) {
        const posProduct = this.pos.models["product.product"].get(returnedProduct.product_id);
        if (!posProduct) {
            this.notification.add(
                _t("No se encontró el producto en el sistema."),
                { type: "warning" }
            );
            return;
        }

        const tmpl = posProduct.product_tmpl_id;
        if (!tmpl || typeof tmpl !== 'object') {
            // No template object — just add the same product to new
            this._addProductTo('new', posProduct);
            return;
        }

        if (tmpl.isConfigurable && tmpl.isConfigurable()) {
            // Open native configurator for the template
            const payload = await this.pos.openConfigurator(tmpl);
            if (payload) {
                const variant = await this._resolveVariantFromPayload(tmpl, payload);
                if (variant) {
                    this._addProductTo('new', variant);
                    this.notification.add(
                        _t("Producto agregado a nuevos: %s", variant.display_name),
                        { type: "success" }
                    );
                } else {
                    this.notification.add(
                        _t("No se pudo determinar la variante seleccionada."),
                        { type: "warning" }
                    );
                }
            }
        } else {
            // Not configurable — add the same product directly
            this._addProductTo('new', posProduct);
        }
    }

    // =====================================================================
    // Computed Totals
    // =====================================================================

    get returnTotal() {
        return this.state.returnedProducts.reduce(
            (sum, p) => sum + p.quantity * p.price_unit, 0
        );
    }

    get newTotal() {
        return this.state.newProducts.reduce(
            (sum, p) => sum + p.quantity * p.price_unit, 0
        );
    }

    get difference() {
        return this.newTotal - this.returnTotal;
    }

    get formattedReturnTotal() {
        return this.env.utils.formatCurrency(this.returnTotal);
    }

    get formattedNewTotal() {
        return this.env.utils.formatCurrency(this.newTotal);
    }

    get formattedDifference() {
        return this.env.utils.formatCurrency(Math.abs(this.difference));
    }

    get differenceType() {
        if (this.difference > 0) return 'charge';     // Cliente paga
        if (this.difference < 0) return 'refund';      // Se le devuelve
        return 'even';                                  // Sin diferencia
    }

    get differenceLabel() {
        if (this.differenceType === 'charge') {
            return _t("Cliente paga:");
        } else if (this.differenceType === 'refund') {
            return _t("Se devuelve al cliente:");
        }
        return _t("Sin diferencia");
    }

    // =====================================================================
    // Validation
    // =====================================================================

    isValidExchange() {
        const hasTicketOrReason = this.state.ticket.trim() !== "";

        const withinLimits = this.state.returnedProducts.every(p => {
            if (p.max_quantity !== undefined) {
                return p.quantity <= p.max_quantity;
            }
            return true;
        });

        return (
            hasTicketOrReason &&
            this.state.returnedProducts.length > 0 &&
            this.state.newProducts.length > 0 &&
            this.returnTotal > 0 &&
            this.newTotal > 0 &&
            withinLimits
        );
    }

    // =====================================================================
    // Confirm & Cancel
    // =====================================================================

    async confirm() {
        if (!this.isValidExchange()) {
            this.notification.add(
                _t("Complete todos los campos: ticket/razón, productos a devolver y productos nuevos."),
                { type: "warning" }
            );
            return;
        }

        // Security check on returned products max_quantity
        for (const product of this.state.returnedProducts) {
            if (product.max_quantity !== undefined && product.quantity > product.max_quantity) {
                this.notification.add(
                    _t("Error: La cantidad de %s excede lo permitido (%s).", product.name, product.max_quantity),
                    { type: "danger" }
                );
                return;
            }
        }

        try {
            const returnedData = this.state.returnedProducts.map((p) => ({
                product_id: p.product_id,
                quantity: p.quantity,
                price_unit: p.price_unit,
            }));

            const newData = this.state.newProducts.map((p) => ({
                product_id: p.product_id,
                quantity: p.quantity,
                price_unit: p.price_unit,
            }));

            const result = await this.orm.call(
                "pos.session",
                "create_exchange",
                [
                    [this.pos.session.id],
                    this.state.ticket.trim(),
                    returnedData,
                    newData,
                    this.state.exchangeType,
                    this.state.partner ? this.state.partner.id : false,
                ]
            );

            if (result.success) {
                // Reload the original order from server so the custom_exchange_done
                // flag is properly loaded into the JS model (via raw data)
                if (result.original_order_id) {
                    try {
                        await this.pos.data.loadServerOrders([
                            ["id", "=", result.original_order_id]
                        ]);
                    } catch (e) {
                        // Non-critical: badge will show after page refresh
                        console.warn("Could not reload order:", e);
                    }
                }
                if (result.needs_payment) {
                    // === Cliente debe pagar la diferencia ===
                    // Crear una orden POS real para que se pueda elegir método de pago
                    await this._createPaymentOrder(result);
                } else {
                    // === Sin diferencia o devolución al cliente ===
                    let successMsg = _t("Intercambio creado. Entrada: %s | Salida: %s", result.picking_in_name, result.picking_out_name);
                    if (result.difference !== 0) {
                        successMsg += " | " + result.cash_message;
                    }
                    this.notification.add(successMsg, { type: "success" });
                    this.props.close();
                }
            } else {
                this.notification.add(
                    result.error || _t("Error desconocido al crear el intercambio."),
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
     * Crea una orden POS real para cobrar la diferencia del intercambio.
     * 
     * IMPORTANTE: El inventario (picking de entrada para devueltos y picking
     * de salida para nuevos) ya fue procesado por create_exchange en el backend.
     * 
     * Para mostrar un recibo informativo y completo, agregamos:
     * - Cada producto NUEVO como línea positiva a su precio completo
     * - Una línea NEGATIVA de "Crédito por devolución" con el valor de lo devuelto
     * El total neto = diferencia = lo que el cliente paga
     */
    async _createPaymentOrder(exchangeResult) {
        // 1. Cerrar popup de intercambio
        this.props.close();

        // 2. Crear nueva orden POS
        const order = this.pos.addNewOrder();

        // 3. CRITICAL: Mark this order as an exchange payment to prevent
        // duplicate picking creation. The inventory movements have already
        // been handled by create_exchange() on the backend.
        // Without this flag, _create_order_picking() would create DUPLICATE
        // stock pickings when this order is paid.
        order.is_exchange_payment = true;

        // 4. Asignar cliente si existe
        if (this.state.partner) {
            order.update({ partner_id: this.state.partner });
        }

        // 5. Agregar cada producto NUEVO como línea individual
        for (const newProd of this.state.newProducts) {
            const product = this.pos.models["product.product"].get(newProd.product_id);
            if (product) {
                await this.pos.addLineToCurrentOrder(
                    {
                        product_id: product,
                        product_tmpl_id: product.product_tmpl_id,
                        price_unit: newProd.price_unit,
                        qty: newProd.quantity,
                        customer_note: _t("Intercambio ► Producto nuevo"),
                    },
                    {},
                    false
                );
            }
        }

        // 6. Agregar cada producto DEVUELTO como línea con cantidad negativa
        for (const retProd of this.state.returnedProducts) {
            const product = this.pos.models["product.product"].get(retProd.product_id);
            if (product) {
                await this.pos.addLineToCurrentOrder(
                    {
                        product_id: product,
                        product_tmpl_id: product.product_tmpl_id,
                        price_unit: retProd.price_unit,
                        qty: -retProd.quantity,
                        customer_note: _t("Intercambio ► Producto devuelto"),
                    },
                    {},
                    false
                );
            }
        }

        // 7. Agregar nota a la orden con la referencia del intercambio
        // internal_note must be a JSON array (Odoo POS uses JSON.parse on it)
        const noteTag = {
            text: _t(
                "Intercambio - Entrada: %s | Salida: %s | Ref: %s",
                exchangeResult.picking_in_name,
                exchangeResult.picking_out_name,
                exchangeResult.exchange_ref
            ),
            colorIndex: 2,
        };
        order.internal_note = JSON.stringify([noteTag]);

        this.notification.add(
            _t("Intercambio procesado. Complete el pago de la diferencia: %s", 
                this.env.utils.formatCurrency(Math.abs(exchangeResult.difference))),
            { type: "info" }
        );

        // 8. Navegar al PaymentScreen
        this.pos.navigate("PaymentScreen", {
            orderUuid: order.uuid,
        });
    }

    cancel() {
        this.props.close();
    }

    /**
     * Vuelve al popup de devolución
     */
    switchToReturn() {
        this.props.close();
        this.pos.openReturn();
    }
}

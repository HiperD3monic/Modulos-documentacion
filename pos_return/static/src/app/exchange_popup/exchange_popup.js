import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { parseFloat } from "@web/views/fields/parsers";
import { Component, useState } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { Dialog } from "@web/core/dialog/dialog";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useAsyncLockedMethod } from "@point_of_sale/app/hooks/hooks";
import { Input } from "@point_of_sale/app/components/inputs/input/input";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TicketListPopup } from "../ticket_list_popup/ticket_list_popup";
import { CustomerMismatchDialog } from "../customer_mismatch_dialog/customer_mismatch_dialog";
import { ReturnBarcodeScanner } from "../return_popup/return_popup";

/**
 * ExchangePopup
 * 
 * Popup de intercambio de productos.
 * 
 * ARQUITECTURA v2 (Nativa):
 * 1. Usuario selecciona productos a devolver y nuevos
 * 2. Confirm() valida vía backend (create_exchange)
 * 3. Crea una orden POS nativa con líneas mixtas:
 *    - Positivas para productos nuevos (salida de inventario)
 *    - Negativas para productos devueltos (entrada de inventario)
 * 4. Navega al PaymentScreen nativo
 * 5. Al pagar, Odoo crea pickings automáticamente (IN + OUT)
 * 6. Contabilidad se maneja al cerrar sesión
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
     * Carga las órdenes recientes cuando el input de ticket recibe foco.
     */
    async onTicketFocus() {
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
     */
    onTicketBlur() {
        setTimeout(() => {
            if (!this.state.selectedTicket) {
                this.state.ticketSearchResults = [];
            }
        }, 200);
    }

    async selectSearchResult(ticket) {
        this.state.ticketSearchResults = [];
        await this._applyTicketSelection(ticket);
    }

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
     * Verifica discrepancia de cliente entre el popup y el ticket seleccionado.
     */
    async _applyTicketSelection(ticket) {
        // ── Customer mismatch check ──
        const popupPartnerId = this.state.partner ? this.state.partner.id : false;
        const ticketPartnerId = ticket.partner_id || false;

        if (popupPartnerId && popupPartnerId !== ticketPartnerId) {
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
                // Ticket has a DIFFERENT client — 2 choices
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
                await this._loadTicketsForPartner(partner.id);
            }
        }

        if (ticket.lines && ticket.lines.length > 0) {
            this.state.returnedProducts = ticket.lines.map(line => ({
                product_id: line.product_id,
                name: line.name,
                quantity: line.remaining_qty || line.qty,
                max_quantity: line.remaining_qty || line.qty,
                price_unit: line.price_unit,
                discount: line.discount || 0,
                tax_ids: line.tax_ids || [],
                original_line_id: line.id,
                is_exchange_product: line.is_exchange_product || false,
                exchange_order_ref: line.exchange_order_ref || '',
            }));
        } else {
            this.state.returnedProducts = [];
        }
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

        const matchedProducts = this.pos.models["product.product"].filter(
            (product) =>
                product.display_name.toLowerCase().includes(q) ||
                (product.barcode && product.barcode.includes(q)) ||
                (product.default_code && product.default_code.toLowerCase().includes(q))
        );

        const order = this.pos.getOrder();
        const pricelist = order?.pricelist_id || this.pos.config.pricelist_id;

        const seenTemplates = new Set();
        const templates = [];
        for (const product of matchedProducts) {
            const tmpl = product.product_tmpl_id;
            const tmplId = tmpl ? (tmpl.id || tmpl) : product.id;
            if (!seenTemplates.has(tmplId)) {
                seenTemplates.add(tmplId);
                const templateObj = typeof tmpl === 'object' ? tmpl : null;
                const variantCount = templateObj ? (templateObj.product_variant_ids?.length || 1) : 1;

                let price = product.lst_price;
                if (pricelist && templateObj && templateObj.getPrice) {
                    price = templateObj.getPrice(pricelist, 1, 0, false, product);
                }

                templates.push({
                    id: tmplId,
                    templateObj: templateObj,
                    product: product,
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
        const oppositeList = target === 'returned' ? this.state.newProducts : this.state.returnedProducts;
        const inOpposite = oppositeList.find((p) => p.product_id === product.id);
        if (inOpposite) {
            this.notification.add(
                _t("No puede intercambiar un producto por el mismo. Seleccione una variante o producto diferente."),
                { type: "warning" }
            );
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
                discount: 0,
            });
        }

        if (target === 'returned') {
            this.state.searchQueryReturned = "";
        } else {
            this.state.searchQueryNew = "";
        }
    }

    async addReturnedTemplate(tmpl) {
        await this._addTemplateToTarget('returned', tmpl);
    }

    async addNewTemplate(tmpl) {
        await this._addTemplateToTarget('new', tmpl);
    }

    async _resolveVariantFromPayload(templateObj, payload) {
        const selectedAttrIds = payload.attribute_value_ids || [];
        const attrValues = this.pos.models["product.template.attribute.value"]
            .readMany(selectedAttrIds)
            .filter((v) => v.attribute_id.create_variant !== "no_variant")
            .map((v) => v.id);

        let variant = templateObj.product_variant_ids.find((v) => {
            const vAttrIds = v.product_template_variant_value_ids.map((a) => a.id);
            return (
                attrValues.every((id) => vAttrIds.includes(id)) &&
                attrValues.length
            );
        });

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

    /**
     * Actualiza el descuento de un producto devuelto
     */
    updateReturnedDiscount(index, value) {
        let disc = parseFloat(value) || 0;
        if (disc < 0) disc = 0;
        if (disc > 100) disc = 100;
        this.state.returnedProducts[index].discount = disc;
    }

    /**
     * Actualiza el descuento de un producto nuevo
     */
    updateNewDiscount(index, value) {
        let disc = parseFloat(value) || 0;
        if (disc < 0) disc = 0;
        if (disc > 100) disc = 100;
        this.state.newProducts[index].discount = disc;
    }

    removeReturnedProduct(index) {
        this.state.returnedProducts.splice(index, 1);
    }

    removeNewProduct(index) {
        this.state.newProducts.splice(index, 1);
    }

    hasVariants(product) {
        const posProduct = this.pos.models["product.product"].get(product.product_id);
        if (!posProduct) return false;
        const tmpl = posProduct.product_tmpl_id;
        if (!tmpl || typeof tmpl !== 'object') return false;
        return tmpl.isConfigurable ? tmpl.isConfigurable() : false;
    }

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
            this._addProductTo('new', posProduct);
            return;
        }

        if (tmpl.isConfigurable && tmpl.isConfigurable()) {
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
            this._addProductTo('new', posProduct);
        }
    }

    // =====================================================================
    // Computed Totals
    // =====================================================================

    get returnTotal() {
        return this.state.returnedProducts.reduce(
            (sum, p) => {
                const disc = p.discount || 0;
                return sum + p.quantity * p.price_unit * (1 - disc / 100);
            }, 0
        );
    }

    get newTotal() {
        return this.state.newProducts.reduce(
            (sum, p) => {
                const disc = p.discount || 0;
                return sum + p.quantity * p.price_unit * (1 - disc / 100);
            }, 0
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
    // Confirm — ARQUITECTURA v2 (Nativa)
    // =====================================================================

    async confirm() {
        if (!this.isValidExchange()) {
            this.notification.add(
                _t("Complete todos los campos: ticket/razón, productos a devolver y productos nuevos."),
                { type: "warning" }
            );
            return;
        }

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
            // 1. Validar en el backend
            const returnedData = this.state.returnedProducts.map((p) => {
                const disc = p.discount || 0;
                const effectivePrice = p.price_unit * (1 - disc / 100);
                return {
                    product_id: p.product_id,
                    quantity: p.quantity,
                    price_unit: effectivePrice,
                };
            });

            const newData = this.state.newProducts.map((p) => {
                const disc = p.discount || 0;
                const effectivePrice = p.price_unit * (1 - disc / 100);
                return {
                    product_id: p.product_id,
                    quantity: p.quantity,
                    price_unit: effectivePrice,
                    original_price: p.price_unit,
                    discount: disc,
                };
            });

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

            if (!result.success) {
                this.notification.add(
                    result.error || _t("Error desconocido al validar el intercambio."),
                    { type: "danger" }
                );
                return;
            }

            // 2. Cerrar popup
            this.props.close();

            // 3. Crear orden POS nativa con líneas mixtas
            const order = this.pos.addNewOrder();

            // 4. Asignar cliente si existe
            if (this.state.partner) {
                order.update({ partner_id: this.state.partner });
            }

            // 5. Agregar cada producto NUEVO como línea positiva
            //    IMPORTANT: Preserve original price_unit + discount separately
            //    so the resulting ticket retains discount info for future operations.
            for (const newProd of this.state.newProducts) {
                const product = this.pos.models["product.product"].get(newProd.product_id);
                if (product) {
                    const disc = newProd.discount || 0;
                    await this.pos.addLineToCurrentOrder(
                        {
                            product_id: product,
                            product_tmpl_id: product.product_tmpl_id,
                            price_unit: newProd.price_unit,
                            discount: disc,
                            qty: newProd.quantity,
                            customer_note: _t("Intercambio ► Producto nuevo"),
                        },
                        {},
                        false
                    );
                }
            }

            // 6. Agregar cada producto DEVUELTO como línea negativa
            //    Same principle: preserve original price + discount
            for (const retProd of this.state.returnedProducts) {
                const product = this.pos.models["product.product"].get(retProd.product_id);
                if (product) {
                    const disc = retProd.discount || 0;
                    const lineVals = {
                        product_id: product,
                        product_tmpl_id: product.product_tmpl_id,
                        price_unit: retProd.price_unit,
                        discount: disc,
                        qty: -retProd.quantity,
                        customer_note: _t("Intercambio ► Producto devuelto"),
                    };

                    // Link to original orderline if available
                    if (retProd.original_line_id && typeof retProd.original_line_id === 'number') {
                        const originalLine = this.pos.models["pos.order.line"].get(retProd.original_line_id);
                        if (originalLine) {
                            lineVals.refunded_orderline_id = originalLine;
                            lineVals.tax_ids = originalLine.tax_ids.map((tax) => ["link", tax]);
                        }
                    }

                    await this.pos.addLineToCurrentOrder(lineVals, {}, false);
                }
            }

            // 7. Agregar nota interna con referencia del intercambio
            const noteTag = {
                text: _t(
                    "Intercambio - %s | Ref: %s | Diff: %s",
                    result.base_type,
                    result.origin_ref,
                    this.env.utils.formatCurrency(result.difference)
                ),
                colorIndex: 2,
            };
            order.internal_note = JSON.stringify([noteTag]);

            // 7b. Calculate products still in customer's possession for receipt
            if (this.state.selectedTicket) {
                const ticketLines = this.state.selectedTicket.lines || [];
                // Build map: product_id → total qty being exchanged/returned now
                const exchangingMap = {};
                for (const retProd of this.state.returnedProducts) {
                    exchangingMap[retProd.product_id] = (exchangingMap[retProd.product_id] || 0) + retProd.quantity;
                }

                const possessionItems = [];
                const exchangingLeft = { ...exchangingMap };
                for (const line of ticketLines) {
                    if ((line.remaining_qty || 0) <= 0) continue;
                    const pid = line.product_id;
                    const exchangingQty = exchangingLeft[pid] || 0;
                    const consumed = Math.min(exchangingQty, line.remaining_qty);
                    const stillHas = line.remaining_qty - consumed;
                    exchangingLeft[pid] = Math.max(0, exchangingQty - consumed);

                    if (stillHas > 0) {
                        // Clean product name: remove emoji prefixes and [CODE] patterns
                        const cleanName = (line.name || '')
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
                    order._possessionItems = possessionItems;
                }
            }

            // 8. Reload original order if marked
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
                _t("Intercambio procesado. Complete el pago en la pantalla de pago."),
                { type: "info" }
            );

            // 9. Navegar al PaymentScreen
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

    cancel() {
        this.props.close();
    }

    switchToReturn() {
        this.props.close();
        this.pos.openReturn();
    }
}

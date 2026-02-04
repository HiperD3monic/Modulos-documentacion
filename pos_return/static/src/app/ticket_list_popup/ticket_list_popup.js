import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

export class TicketListPopup extends Component {
    static template = "pos_return.TicketListPopup";
    static components = { Dialog };
    static props = {
        close: { type: Function },
        getPayload: { type: Function },
        tickets: { type: Array },
    };

    setup() {
        this.pos = usePos();
        this.state = useState({
            searchQuery: "",
            searchFilter: "all", // 'all', 'ref', 'date', 'product'
            expandedTicketId: null,
            dropdownOpen: false,
        });
    }

    toggleDropdown() {
        this.state.dropdownOpen = !this.state.dropdownOpen;
    }

    setFilter(filter) {
        this.state.searchFilter = filter;
        this.state.dropdownOpen = false;
    }

    get filteredTickets() {
        const query = this.state.searchQuery.toLowerCase();
        const filter = this.state.searchFilter;

        if (!query) {
            return this.props.tickets;
        }

        return this.props.tickets.filter((ticket) => {
            const matchesRef = (ticket.pos_reference || "").toLowerCase().includes(query) || (ticket.name || "").toLowerCase().includes(query);
            const matchesDate = (ticket.date_order || "").includes(query);
            const matchesProduct = ticket.lines && ticket.lines.some(line =>
                (line.name || "").toLowerCase().includes(query)
            );

            if (filter === 'ref') return matchesRef;
            if (filter === 'date') return matchesDate;
            if (filter === 'product') return matchesProduct;

            // Default: All
            return matchesRef || matchesDate || matchesProduct;
        });
    }

    toggleDetails(ticket) {
        if (this.state.expandedTicketId === ticket.id) {
            this.state.expandedTicketId = null;
        } else {
            this.state.expandedTicketId = ticket.id;
        }
    }

    selectTicket(ticket) {
        this.props.getPayload(ticket);
        this.props.close();
    }
}
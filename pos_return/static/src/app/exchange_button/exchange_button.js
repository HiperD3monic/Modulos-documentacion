/** @odoo-module */

import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

export class ExchangeButton extends Component {
    static template = "pos_return.ExchangeButton";
    static props = {
        onClick: { type: Function, optional: true },
        class: { type: String, optional: true },
    };

    setup() {
        this.pos = usePos();
    }

    async click() {
        await this.pos.openExchange();
    }
}

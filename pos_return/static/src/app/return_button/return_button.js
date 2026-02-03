/** @odoo-module */

import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";

export class ReturnButton extends Component {
    static template = "pos_return.ReturnButton";
    static props = {
        onClick: { type: Function, optional: true },
        class: { type: String, optional: true },
    };

    setup() {
        console.log("POS Return Button initialized");
        this.pos = usePos();
    }

    async click() {
        await this.pos.openReturn();
    }
}

// Add the component to the ControlButtons component list
ControlButtons.components.ReturnButton = ReturnButton;

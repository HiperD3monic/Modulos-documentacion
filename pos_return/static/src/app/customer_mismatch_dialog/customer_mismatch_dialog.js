/** @odoo-module **/
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * CustomerMismatchDialog
 * 
 * Custom 3-button dialog for customer mismatch when selecting a ticket
 * without a client while a client is already selected.
 * 
 * Why not ConfirmationDialog?
 * Odoo's ConfirmationDialog calls cancel() in onWillUnmount when X is clicked,
 * making it impossible to distinguish X from the cancel button.
 * This component uses separate callbacks for each action.
 * 
 * Actions:
 * - onKeepClient: Keep current client, link ticket to them
 * - onRemoveClient: Remove client, use ticket without client
 * - X close: Cancel selection entirely (no callback needed)
 */
export class CustomerMismatchDialog extends Component {
    static template = "pos_return.CustomerMismatchDialog";
    static components = { Dialog };
    static props = {
        body: String,
        close: Function,
        onKeepClient: { type: Function, optional: true },
        onRemoveClient: { type: Function, optional: true },
    };

    _onKeepClient() {
        this.props.onKeepClient?.();
        this.props.close();
    }

    _onRemoveClient() {
        this.props.onRemoveClient?.();
        this.props.close();
    }
}

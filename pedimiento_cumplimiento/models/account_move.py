# coding: utf-8
from odoo import _, models
from odoo.tools.misc import format_date
import logging

__logger__ = logging.error

class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        # OVERRIDE
        AccountMoveLine = self.env['account.move.line']
        StockLandedCost = self.env['stock.landed.cost']
        for move in self.filtered(lambda move: move.is_invoice()):
            for line in move.line_ids:
                if line.l10n_mx_edi_customs_number:
                    continue
                stock_moves = line.mapped('sale_line_ids.move_ids').filtered(lambda r: r.state == 'done' and not r.scrapped)
          
                landed_costs = False
                if stock_moves:
                    landed_costs = self.env['stock.landed.cost'].sudo().search([
                        ('picking_ids', 'in', stock_moves.mapped('move_orig_fifo_ids.picking_id').ids),
                        ('l10n_mx_edi_customs_number', '!=', False),
                        ('state', '=', 'done')

                    ])
         
                customs_numbers = False
                formatted_dates = False

                if landed_costs:
                    customs_data = {(format_date(self.env, lc.date, date_format='yyyy-MM-dd'), lc.l10n_mx_edi_customs_number) for lc in landed_costs}
                    customs_dates, customs_numbers = zip(*customs_data) if customs_data else ([], [])
                    customs_numbers = ','.join(customs_numbers)
                    line.l10n_mx_edi_customs_number = customs_numbers
                    
                    if formatted_dates:
                        line.name += '\n' + _('Fecha de Pedimiento: %s', ','.join(customs_dates))
                    logging.error(landed_costs)
                    logging.error(landed_costs.mapped('name'))
                    logging.error(landed_costs.mapped('l10n_mx_edi_customs_number'))
                    logging.error(customs_data)
                else:
                    customs_numbers = AccountMoveLine.search([('product_id','=',line.product_id.id),('l10n_mx_edi_customs_number','!=', False),('move_id.state','=','posted')], order='invoice_date DESC')
                    logging.error(customs_numbers)
                    if customs_numbers:
                        logging.error(customs_numbers[0].l10n_mx_edi_customs_number)
                       
                        line.l10n_mx_edi_customs_number = customs_numbers[0].l10n_mx_edi_customs_number
                        lc = StockLandedCost.search([('l10n_mx_edi_customs_number','=',customs_numbers[0].l10n_mx_edi_customs_number)])
                        if lc:

                            formatted_dates = format_date(self.env, lc.date, date_format='yyyy-MM-dd')
                    
                    if formatted_dates:
                        line.name += '\n' + 'Fecha de Pedimiento: %s' % formatted_dates

        return super()._post(soft)

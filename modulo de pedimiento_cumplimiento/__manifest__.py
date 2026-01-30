{
    'name': 'Pedimientos',
    'version': '1.2',
    'description': 'Permite establecer un pedimiento en la orden de compra',
    'summary': 'Permite establecer un pedimiento en la orden de compra',
    'author': 'dataliza',
    'license': 'OPL-1',
    'depends': [
        'stock_landed_costs', 'purchase', 'l10n_mx_edi_landing', 'sh_landed_cost_cancel'
    ],
    'data': [
        'views/purchase_order_view.xml',
        'views/stock_landed_cost_view.xml',
    ],
    'auto_install': False,
    'application': False,
}
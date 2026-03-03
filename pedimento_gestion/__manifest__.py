# coding: utf-8
{
    'name': 'Gestión de Pedimentos Aduanales',
    'version': '18.0.1.0.0',
    'summary': 'Gestión de pedimentos aduanales mexicanos con validación masiva, '
               'cambio de número de pedimento y auditoría completa.',
    'description': """
        Módulo para gestión de pedimentos aduanales en operaciones de
        comercio exterior mexicano. Incluye:

        - Validación automática de números de pedimento (formato SAT)
        - Creación/reutilización automática de costos en destino
        - Validación masiva de pedimentos desde PO y LC
        - Cambio de número de pedimento desde costo en destino
        - Wizard de preview antes de ejecutar operaciones masivas
        - Registro de auditoría de todas las operaciones
        - Badges de estado de pedimento en vistas de lista
    """,
    'author': 'Dataliza',
    'contributors': [
        'Jorge Medina', 'Gustavo Pozzo'
    ],
    'category': 'Inventory/Landed Costs',
    'license': 'LGPL-3',
    'depends': [
        'stock_landed_costs',
        'purchase',
        'l10n_mx_edi_landing',
        'l10n_mx_edi_extended',
        'sh_landed_cost_cancel',
    ],
    'data': [
        # Seguridad
        'security/ir.model.access.csv',

        # Datos
        'data/ir_sequence_data.xml',

        # Vistas
        'views/purchase_order_view.xml',
        'views/report_invoice_pedimento.xml',
        'views/stock_landed_cost_view.xml',
        'views/pedimento_operation_log_view.xml',
        'views/pedimento_wizard_view.xml',
        'views/pedimento_change_number_view.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
